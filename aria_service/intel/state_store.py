"""
state_store — SQLite-backed replacement for Upstash Redis (R-F235, 2026-05-11).

Why this module exists
──────────────────────
Operator question: "if Claude Code uses neither Redis nor Brave, why do
we?" Honest answer: incremental drift, not necessity. On a single-instance
fly.io machine with a persistent `/data` volume, distributed Redis is
overhead. SQLite at `/data/aria_state.db` is sub-millisecond, ACID, has
zero ops, and gets covered by the same backup chain (R-F224) that holds
knowledge.json and signals.json.

This module mirrors the redis_store public API surface against aiosqlite,
so the rest of the codebase doesn't have to change. The backend is
selected at boot via `ARIA_STATE_BACKEND`:
    upstash  — current (Upstash Redis)        [default — backwards compatible]
    sqlite   — this module                    [recommended target]
    memory   — process-RAM only               [tests / break-glass]

Schema (single table, all collection types JSON-encoded inside `value`):

    CREATE TABLE state (
        key         TEXT PRIMARY KEY,
        value       TEXT NOT NULL,
        kind        TEXT NOT NULL CHECK(kind IN ('string','list','zset','hash')),
        expires_at  REAL                       -- epoch seconds; NULL = no TTL
    )

TTL semantics: lazy expiration on read AND a background sweeper that
purges expired rows every 60s. The sweeper is owned by the lifespan code
in main.py.

Public API mirrors aria_service.intel.redis_store exactly:
    connect() / get / set / delete / get_json / set_json
    lpush / ltrim / llen / lrange
    incr / incrbyfloat / expire
    zadd / zrevrange / zrem / zcard
    hset / hgetall / scan_keys

All operations are coroutines. Single-SQL ops rely on aiosqlite's
single worker thread for serialisation; compound read-modify-write
ops (lpush/ltrim/incr/incrbyfloat/zadd/zrem/hset) hold a Python-level
asyncio.Lock around the read+modify+write trio to keep the JSON-blob
view atomic. The lock is lazy-bound (created on first acquire inside
the running loop) so pytest's per-test `asyncio.run()` loops each get
a fresh lock after `connect()`'s `_reset_lock()` call.

Performance: at ARIA's scale (~1-5 writes/second peak, ~100 reads/min)
the JSON-blob-per-list approach is fine. For DLQ (100K-entry cap) the
blob is ~5-10 MB per operation — slow but acceptable for the daily-tick
volume. If DLQ writes start dominating CPU, migrate to row-per-entry
schema in a follow-up.
"""
from __future__ import annotations

import asyncio
import fnmatch
import json
import logging
import os
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger("aria.state_store")

# ─────────────────────────────────────────────────────────────────────────
# Configuration
# ─────────────────────────────────────────────────────────────────────────

_DB_PATH: Path | None = None
_conn = None  # aiosqlite.Connection — lazy init
# Lazy lock — bound to whatever loop first acquires it. Required because the
# module is imported BEFORE any event loop exists, and pytest spins up a new
# loop per `asyncio.run(...)` test. Single-SQL operations don't need this
# lock (aiosqlite already serialises through one worker thread); only the
# compound read-modify-write paths (lpush/ltrim/incr/incrbyfloat/zadd/zrem/hset)
# acquire it to keep the JSON-blob RMW atomic at the Python layer.
_lock: asyncio.Lock | None = None


def _get_lock() -> asyncio.Lock:
    """Return the module-global lock, creating it inside the running loop
    on first call. Each test's asyncio.run() resets _conn but reuses _lock;
    if the lock was bound to a closed loop, the next test's connect() calls
    _reset_lock() to bind it to the new loop."""
    global _lock
    if _lock is None:
        _lock = asyncio.Lock()
    return _lock


def _reset_lock() -> None:
    """Drop the lock so the next _get_lock() call rebinds to the current loop.
    Called from connect() to handle pytest's per-test loops cleanly."""
    global _lock
    _lock = None


def _now() -> float:
    return time.time()


def _ttl_to_expires(ex: int | None) -> float | None:
    return _now() + ex if ex is not None and ex > 0 else None


def _expired(expires_at: float | None) -> bool:
    return expires_at is not None and expires_at <= _now()


# ─────────────────────────────────────────────────────────────────────────
# Lifecycle
# ─────────────────────────────────────────────────────────────────────────

async def connect(db_path: str | None = None) -> bool:
    """Open the SQLite file and create the schema if missing. Returns True
    on success. Caller (main.py) should fall back to in-memory dict if
    False (matches redis_store.connect contract)."""
    global _conn, _DB_PATH
    _reset_lock()
    try:
        import aiosqlite
    except ImportError:
        logger.error(
            "state_store: aiosqlite not installed. Add `aiosqlite>=0.19` "
            "to requirements.txt to enable SQLite backend."
        )
        return False

    if db_path is None:
        db_path = os.getenv("ARIA_STATE_DB_PATH", "/data/aria_state.db")
    _DB_PATH = Path(db_path)
    try:
        _DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    except Exception as e:
        logger.warning("state_store: cannot create parent dir %s: %s",
                       _DB_PATH.parent, e)
        # Fall back to /tmp if /data is unmounted
        _DB_PATH = Path("/tmp/aria_state.db")
        try:
            _DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        except Exception:
            pass

    try:
        _conn = await aiosqlite.connect(str(_DB_PATH))
        # WAL mode → concurrent readers don't block writers. Crucial for
        # the chat path while autonomous tasks are also writing.
        await _conn.execute("PRAGMA journal_mode=WAL")
        await _conn.execute("PRAGMA synchronous=NORMAL")
        await _conn.execute("PRAGMA foreign_keys=OFF")
        await _conn.execute("PRAGMA busy_timeout=5000")
        await _conn.execute(
            """
            CREATE TABLE IF NOT EXISTS state (
                key         TEXT PRIMARY KEY,
                value       TEXT NOT NULL,
                kind        TEXT NOT NULL DEFAULT 'string',
                expires_at  REAL
            )
            """
        )
        await _conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_state_expires ON state(expires_at) "
            "WHERE expires_at IS NOT NULL"
        )
        await _conn.commit()
        logger.info("state_store: SQLite ready at %s (WAL mode)", _DB_PATH)
        return True
    except Exception as e:
        logger.error("state_store: connect failed: %s", e)
        _conn = None
        return False


async def close() -> None:
    """Close the SQLite connection. Called from lifespan shutdown."""
    global _conn
    if _conn is not None:
        try:
            await _conn.close()
        except Exception:
            pass
        _conn = None


async def sweep_expired() -> int:
    """Purge rows whose expires_at is in the past. Returns the row count
    deleted. Run periodically from a background task."""
    if _conn is None:
        return 0
    try:
        cur = await _conn.execute(
            "DELETE FROM state WHERE expires_at IS NOT NULL AND expires_at <= ?",
            (_now(),),
        )
        await _conn.commit()
        n = cur.rowcount or 0
        if n > 0:
            logger.debug("state_store: swept %d expired rows", n)
        return n
    except Exception as e:
        logger.warning("state_store: sweep failed: %s", e)
        return 0


# ─────────────────────────────────────────────────────────────────────────
# Internal helpers
# ─────────────────────────────────────────────────────────────────────────

async def _row(key: str, expected_kind: str | None = None) -> tuple[str, str, float | None] | None:
    """Fetch (value, kind, expires_at) for a key. Returns None if missing
    or expired. If expected_kind is given and the kind mismatches, returns
    None (treat as missing — Redis raises WRONGTYPE; we degrade gracefully)."""
    if _conn is None:
        return None
    try:
        cur = await _conn.execute(
            "SELECT value, kind, expires_at FROM state WHERE key = ?",
            (key,),
        )
        row = await cur.fetchone()
        await cur.close()
    except Exception as e:
        logger.warning("state_store: SELECT %s failed: %s", key, e)
        return None
    if not row:
        return None
    value, kind, expires_at = row
    if _expired(expires_at):
        # Lazy expiry — drop the row on read. No python-level lock needed:
        # aiosqlite serialises through a single worker thread, and DELETE
        # is idempotent so a parallel sweep_expired() can't double-fault.
        try:
            await _conn.execute("DELETE FROM state WHERE key = ?", (key,))
            await _conn.commit()
        except Exception:
            pass
        return None
    if expected_kind and kind != expected_kind:
        logger.debug("state_store: kind mismatch on %s — wanted %s, got %s",
                     key, expected_kind, kind)
        return None
    return value, kind, expires_at


async def _upsert(key: str, value: str, kind: str, expires_at: float | None,
                  keepttl: bool = False) -> None:
    # No python-level lock: this is a single SQL statement and aiosqlite
    # serialises through one worker thread. Compound RMW ops (lpush etc.)
    # hold _get_lock() themselves to keep their read+write atomic.
    if _conn is None:
        return
    try:
        if keepttl and expires_at is None:
            # Preserve existing expires_at; only update value
            await _conn.execute(
                "INSERT INTO state(key, value, kind, expires_at) "
                "VALUES(?, ?, ?, NULL) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value, kind = excluded.kind",
                (key, value, kind),
            )
        else:
            await _conn.execute(
                "INSERT INTO state(key, value, kind, expires_at) "
                "VALUES(?, ?, ?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value, "
                "kind = excluded.kind, expires_at = excluded.expires_at",
                (key, value, kind, expires_at),
            )
        await _conn.commit()
    except Exception as e:
        logger.warning("state_store: UPSERT %s failed: %s", key, e)


# ─────────────────────────────────────────────────────────────────────────
# Public API — mirrors redis_store
# ─────────────────────────────────────────────────────────────────────────

async def get(key: str) -> str | None:
    row = await _row(key, expected_kind="string")
    return row[0] if row else None


async def set(key: str, value: str, ex: int | None = None,
              keepttl: bool = False) -> None:
    expires_at = _ttl_to_expires(ex)
    await _upsert(key, value, kind="string", expires_at=expires_at, keepttl=keepttl)


async def delete(key: str) -> bool:
    if _conn is None:
        return False
    try:
        cur = await _conn.execute("DELETE FROM state WHERE key = ?", (key,))
        await _conn.commit()
        return (cur.rowcount or 0) > 0
    except Exception as e:
        logger.warning("state_store: DELETE %s failed: %s", key, e)
        return False


async def get_json(key: str) -> Any:
    raw = await get(key)
    if raw:
        try:
            return json.loads(raw)
        except Exception as e:
            logger.warning("state_store: JSON parse %s failed: %s", key, e)
    return None


async def set_json(key: str, obj: Any, ex: int | None = None,
                   keepttl: bool = False) -> None:
    await set(key, json.dumps(obj, default=str), ex=ex, keepttl=keepttl)


# ── List operations (JSON-blob backed) ───────────────────────────────────

async def _read_list(key: str) -> tuple[list, float | None]:
    row = await _row(key, expected_kind="list")
    if not row:
        return [], None
    try:
        lst = json.loads(row[0])
        return (lst if isinstance(lst, list) else []), row[2]
    except Exception:
        return [], row[2]


async def lpush(key: str, value: str) -> None:
    async with _get_lock():
        lst, expires_at = await _read_list(key)
        lst.insert(0, value)
        await _upsert(key, json.dumps(lst, default=str), kind="list",
                      expires_at=expires_at, keepttl=True)


async def ltrim(key: str, start: int, stop: int) -> None:
    async with _get_lock():
        lst, expires_at = await _read_list(key)
        if not lst:
            return
        # Redis LTRIM is inclusive on both ends. stop=-1 means "to end".
        end = (stop + 1) if stop >= 0 else (len(lst) + stop + 1)
        trimmed = lst[start:end]
        await _upsert(key, json.dumps(trimmed, default=str), kind="list",
                      expires_at=expires_at, keepttl=True)


async def llen(key: str) -> int:
    lst, _ = await _read_list(key)
    return len(lst)


async def lrange(key: str, start: int, stop: int) -> list[str]:
    lst, _ = await _read_list(key)
    end = (stop + 1) if stop >= 0 else (len(lst) + stop + 1)
    return lst[start:end]


# ── Counters ─────────────────────────────────────────────────────────────

async def incr(key: str, amount: int = 1) -> int:
    """Atomic integer increment (atomic due to the lock + UPSERT)."""
    async with _get_lock():
        row = await _row(key, expected_kind="string")
        try:
            current = int(row[0]) if row else 0
        except Exception:
            current = 0
        new_val = current + amount
        expires_at = row[2] if row else None
        await _upsert(key, str(new_val), kind="string",
                      expires_at=expires_at, keepttl=True)
        return new_val


async def incrbyfloat(key: str, amount: float) -> float:
    """Atomic float increment."""
    async with _get_lock():
        row = await _row(key, expected_kind="string")
        try:
            current = float(row[0]) if row else 0.0
        except Exception:
            current = 0.0
        new_val = current + amount
        expires_at = row[2] if row else None
        await _upsert(key, f"{new_val:.6f}", kind="string",
                      expires_at=expires_at, keepttl=True)
        return new_val


async def expire(key: str, seconds: int) -> bool:
    if _conn is None:
        return False
    try:
        cur = await _conn.execute(
            "UPDATE state SET expires_at = ? WHERE key = ?",
            (_ttl_to_expires(seconds), key),
        )
        await _conn.commit()
        return (cur.rowcount or 0) > 0
    except Exception as e:
        logger.warning("state_store: EXPIRE %s failed: %s", key, e)
        return False


# ── Sorted sets (JSON-blob; volumes are small) ──────────────────────────

async def _read_zset(key: str) -> tuple[list[tuple[float, str]], float | None]:
    row = await _row(key, expected_kind="zset")
    if not row:
        return [], None
    try:
        raw = json.loads(row[0])
        entries = [(float(s), str(m)) for s, m in raw]
        return entries, row[2]
    except Exception:
        return [], row[2]


async def zadd(key: str, score: float, member: str) -> None:
    async with _get_lock():
        entries, expires_at = await _read_zset(key)
        # Remove existing member, then re-insert with the new score
        entries = [(s, m) for s, m in entries if m != member]
        entries.append((float(score), member))
        entries.sort(key=lambda x: x[0])
        await _upsert(key, json.dumps(entries, default=str), kind="zset",
                      expires_at=expires_at, keepttl=True)


async def zrevrange(key: str, start: int, stop: int) -> list[str]:
    entries, _ = await _read_zset(key)
    entries.sort(key=lambda x: x[0], reverse=True)
    end = (stop + 1) if stop >= 0 else (len(entries) + stop + 1)
    return [m for _, m in entries[start:end]]


async def zrem(key: str, member: str) -> bool:
    async with _get_lock():
        entries, expires_at = await _read_zset(key)
        before = len(entries)
        entries = [(s, m) for s, m in entries if m != member]
        await _upsert(key, json.dumps(entries, default=str), kind="zset",
                      expires_at=expires_at, keepttl=True)
        return len(entries) < before


async def zcard(key: str) -> int:
    entries, _ = await _read_zset(key)
    return len(entries)


# ── Hashes ──────────────────────────────────────────────────────────────

async def _read_hash(key: str) -> tuple[dict, float | None]:
    row = await _row(key, expected_kind="hash")
    if not row:
        return {}, None
    try:
        h = json.loads(row[0])
        return (h if isinstance(h, dict) else {}), row[2]
    except Exception:
        return {}, row[2]


async def hset(key: str, mapping: dict) -> None:
    async with _get_lock():
        existing, expires_at = await _read_hash(key)
        existing.update(mapping)
        await _upsert(key, json.dumps(existing, default=str), kind="hash",
                      expires_at=expires_at, keepttl=True)


async def hgetall(key: str) -> dict:
    h, _ = await _read_hash(key)
    return h


# ── Glob scan ───────────────────────────────────────────────────────────

async def scan_keys(pattern: str, count: int = 200) -> list[str]:
    if _conn is None:
        return []
    # Convert Redis glob (* ? [abc]) to SQL LIKE wildcards; do a fnmatch
    # filter on the result to handle [abc] correctly (LIKE doesn't).
    try:
        cur = await _conn.execute(
            "SELECT key FROM state WHERE (expires_at IS NULL OR expires_at > ?)",
            (_now(),),
        )
        rows = await cur.fetchall()
        await cur.close()
    except Exception as e:
        logger.warning("state_store: SCAN failed: %s", e)
        return []
    matched: list[str] = []
    for (k,) in rows:
        if fnmatch.fnmatch(k, pattern):
            matched.append(k)
            if len(matched) >= count:
                break
    return matched


# ─────────────────────────────────────────────────────────────────────────
# Diagnostics
# ─────────────────────────────────────────────────────────────────────────

async def stats() -> dict:
    """Return basic backend stats for the /health endpoint."""
    if _conn is None:
        return {
            "backend": "sqlite",
            "configured": False,
            "db_path": str(_DB_PATH) if _DB_PATH else None,
        }
    try:
        cur = await _conn.execute(
            "SELECT COUNT(*), "
            "SUM(CASE WHEN expires_at IS NOT NULL AND expires_at <= ? THEN 1 ELSE 0 END), "
            "SUM(length(value)) FROM state",
            (_now(),),
        )
        row = await cur.fetchone()
        await cur.close()
        total, expired, total_bytes = row if row else (0, 0, 0)
        file_bytes = _DB_PATH.stat().st_size if _DB_PATH and _DB_PATH.exists() else 0
        return {
            "backend": "sqlite",
            "configured": True,
            "db_path": str(_DB_PATH) if _DB_PATH else None,
            "key_count": total or 0,
            "expired_pending_sweep": expired or 0,
            "value_bytes_total": total_bytes or 0,
            "file_bytes": file_bytes,
        }
    except Exception as e:
        logger.warning("state_store: stats failed: %s", e)
        return {"backend": "sqlite", "configured": True, "error": str(e)[:200]}
