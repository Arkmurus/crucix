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
import traceback
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

# R-F1334: warn when the RMW lock is held longer than this. A multi-second
# hold means every compound op (lpush/incr/zadd/hset) in the process queued
# behind one holder — the precursor to the API-wide hangs diagnosed 2026-06-04
# (endpoints stuck 170s+ while /health/live stayed at 0.5s).
_LOCK_HOLD_WARN_S = float(os.getenv("ARIA_STATE_LOCK_HOLD_WARN_S", "5.0"))


class _DiagLock(asyncio.Lock):
    """R-F1334: asyncio.Lock that records its holder for wedge diagnostics.

    asyncio.Lock exposes waiters but NOT the holder — when the 2026-06-04
    starvation wedge hit, the blackout dump (R-F1333) could only say
    "locked=True, N waiters" with no way to name the coroutine sitting on
    the lock. This subclass captures the acquiring task's name + caller
    stack at acquire time so self_restart._write_wedge_dump() and
    get_lock_diagnostics() can name the culprit directly.

    The caller stack is captured at acquire ENTRY (before any suspension)
    because once a contended acquire resumes via the event loop, f_back
    points at the loop runner, not the real caller.
    """

    def __init__(self) -> None:
        super().__init__()
        self.holder_task: str | None = None
        self.holder_stack: list[str] | None = None
        self.acquired_at: float | None = None

    async def acquire(self) -> bool:
        # Capture the awaiting call chain while it is still on the C stack.
        # limit=12 keeps the dump short; [:-1] drops this acquire() frame.
        frames = traceback.extract_stack(limit=12)[:-1]
        result = await super().acquire()
        try:
            task = asyncio.current_task()
            self.holder_task = task.get_name() if task is not None else "?"
        except Exception:
            self.holder_task = "?"
        self.holder_stack = [f"{f.filename}:{f.lineno} {f.name}" for f in frames]
        self.acquired_at = time.monotonic()
        return result

    def release(self) -> None:
        held_for = (
            time.monotonic() - self.acquired_at
            if self.acquired_at is not None
            else None
        )
        if held_for is not None and held_for > _LOCK_HOLD_WARN_S:
            logger.warning(
                "[R-F1334] state_store lock held %.1fs (warn>%.1fs) by task=%s; "
                "acquire stack: %s",
                held_for,
                _LOCK_HOLD_WARN_S,
                self.holder_task,
                " <- ".join(reversed(self.holder_stack or []))[:2000],
            )
        self.holder_task = None
        self.holder_stack = None
        self.acquired_at = None
        super().release()


def get_lock_diagnostics() -> dict:
    """R-F1334: snapshot of the RMW lock for wedge dumps / health probes.

    Safe to call from any thread — reads plain attributes only, never
    touches the event loop. Returns holder identity when the lock is a
    _DiagLock and currently held.
    """
    if _lock is None:
        return {"initialised": False, "locked": False,
                "op_timeouts": dict(_op_timeout_counts)}  # R-F1341
    out: dict[str, Any] = {"initialised": True, "locked": _lock.locked()}
    try:
        waiters = getattr(_lock, "_waiters", None)
        out["waiters"] = len(waiters) if waiters else 0
    except Exception:
        out["waiters"] = None
    if isinstance(_lock, _DiagLock) and _lock.locked():
        out["holder_task"] = _lock.holder_task
        out["holder_stack"] = list(_lock.holder_stack or [])
        out["held_for_s"] = (
            round(time.monotonic() - _lock.acquired_at, 3)
            if _lock.acquired_at is not None
            else None
        )
    out["op_timeouts"] = dict(_op_timeout_counts)  # R-F1341
    return out


def _get_lock() -> asyncio.Lock:
    """Return the module-global lock, creating it inside the running loop
    on first call. Each test's asyncio.run() resets _conn but reuses _lock;
    if the lock was bound to a closed loop, the next test's connect() calls
    _reset_lock() to bind it to the new loop."""
    global _lock
    if _lock is None:
        _lock = _DiagLock()  # R-F1334: holder-tracked (was plain asyncio.Lock)
    return _lock


def _reset_lock() -> None:
    """Drop the lock so the next _get_lock() call rebinds to the current loop.
    Called from connect() to handle pytest's per-test loops cleanly."""
    global _lock
    _lock = None


# ── R-F1341: bounded, self-healing locked-op wrapper ──────────────────────
# Root cause of the recurring blackout (proven 2026-06-05 via R-F1334 lock
# diagnostics): one state_store.hset acquired the global RMW lock and its
# in-lock aiosqlite write never returned — lock held 1311s with 140 waiters,
# starving the event loop until the heartbeat went stale → blackout. Every
# compound op shared ONE asyncio.Lock around ONE aiosqlite connection with no
# timeout, so a single stalled write took down the whole app.
#
# Fix: every compound op now runs through _run_locked(), which bounds BOTH
#   (a) lock ACQUISITION — a waiter that can't get the lock in
#       _ACQUIRE_TIMEOUT_S gives up and returns a safe default, so a stalled
#       holder can never starve the loop into a blackout, AND
#   (b) the in-lock OPERATION — if the aiosqlite read+write exceeds
#       _OP_TIMEOUT_S the holder aborts, releases the lock, and triggers a
#       single-flight connection reset (self-heal) so a wedged connection
#       doesn't poison every subsequent op.
# A fatal "one stall = whole-app blackout" becomes "one stall = one dropped
# write + auto-reconnect", which is the reliability floor for 99.99% uptime.
_OP_TIMEOUT_S: float = 15.0  # overridden by _timeout_config()
_ACQUIRE_TIMEOUT_S: float = 20.0  # overridden by _timeout_config()


def _timeout_config() -> tuple[float, float]:
    """Read timeout values from env vars at call time.

    R-F1376: moved from module-level constants to call-time reads so tests
    can monkeypatch env vars without needing importlib.reload (which
    duplicates class definitions and breaks isinstance checks).
    """
    return (
        float(os.getenv("ARIA_STATE_OP_TIMEOUT_S", "15")),
        float(os.getenv("ARIA_STATE_ACQUIRE_TIMEOUT_S", "20")),
    )
_op_timeout_counts = {"acquire": 0, "op": 0, "reconnect": 0}
_reconnect_in_progress = False


async def _reconnect() -> None:
    """Single-flight: drop the wedged aiosqlite connection and reopen it.
    Does NOT touch the lock (callers may hold it). Safe to call concurrently —
    only the first caller reconnects; the rest no-op."""
    global _conn, _reconnect_in_progress
    if _reconnect_in_progress:
        return
    _reconnect_in_progress = True
    try:
        import aiosqlite

        old, _conn = _conn, None
        if old is not None:
            try:
                await asyncio.wait_for(old.close(), timeout=5.0)
            except Exception:
                pass  # the whole point is that it was wedged
        if _DB_PATH is None:
            return
        conn = await aiosqlite.connect(str(_DB_PATH))
        await conn.execute("PRAGMA journal_mode=WAL")
        await conn.execute("PRAGMA synchronous=NORMAL")
        await conn.execute("PRAGMA foreign_keys=OFF")
        await conn.execute("PRAGMA busy_timeout=5000")
        await conn.commit()
        _conn = conn
        _op_timeout_counts["reconnect"] += 1
        logger.warning("[R-F1341] state_store connection reset (self-heal) #%d",
                       _op_timeout_counts["reconnect"])
    except Exception as e:
        logger.error("[R-F1341] state_store reconnect failed: %s", e)
    finally:
        _reconnect_in_progress = False


def _is_conn_dead(e: Exception) -> bool:
    """R-F1352: does this exception indicate the aiosqlite connection itself is
    dead (vs a query/programming error)? Matches aiosqlite/sqlite3 closed-conn
    messages so we only churn the connection when reconnecting can actually help."""
    m = str(e).lower()
    return any(
        s in m
        for s in ("closed", "no active connection", "cannot operate", "connection")
    )


def _schedule_reconnect_if_dead(e: Exception) -> None:
    """R-F1352: the READ path (plain SELECTs in _row/scan_keys/stats) bypasses
    _run_locked, so before this it had NO self-heal — once the single shared
    connection died, every read failed 'Connection closed' forever and the
    state-reading endpoints (health/perf, autonomous/status) hung. Mirror the
    write-path self-heal: on a dead-connection read error, fire-and-forget a
    single-flight reconnect so subsequent reads recover. Never awaited, never
    raises — the read still returns its graceful default this call."""
    if _reconnect_in_progress or not _is_conn_dead(e):
        return
    try:
        asyncio.get_running_loop().create_task(_reconnect())
    except Exception:
        pass


class StateWriteError(Exception):
    """R-F1351: raised by a compound op invoked with critical=True when its
    write did NOT land (lock-acquire timeout, in-lock op timeout, or error).

    R-F1341 made _run_locked return a `default` on any failure so a stalled
    write can't blackout the event loop — correct, but it silently disabled
    every caller's `except` branch, so a dropped write was reported as success.
    For DATA-INTEGRITY writes (evidentiary hash-chains, the cost cap, the gap
    ledger) that silent drop is worse than an exception: a caller that advances
    a head-hash or reports cost-recorded on a dropped write corrupts state.
    Such callers pass critical=True and handle StateWriteError (e.g. do NOT
    advance the chain / retry / WAL). Non-critical callers keep the
    return-default behaviour — no blackout, no new crash surface."""


async def _run_locked(op_name: str, factory, default=None, critical: bool = False):
    """Run a compound RMW op under the global lock with bounded acquire +
    bounded execution. Never lets a single stalled op blackout the app.

    R-F1351: when critical=True, a failed write RAISES StateWriteError instead
    of silently returning `default`, so data-integrity callers can react.

    R-F1376: bounded retry with exponential backoff on lock-acquire timeout.
    A transient contention burst (e.g. 40 concurrent writes during cold-start
    hydration) no longer logs ERROR on every attempt — only the final failure
    after all retries are exhausted. This directly reduces the ERROR count
    that holds Gate #3 open."""
    lock = _get_lock()
    op_timeout, acquire_timeout = _timeout_config()
    last_error: Exception | None = None
    # Non-critical ops retry once (fast fail — they return default anyway).
    # Critical ops retry 3 times (more persistence for data-integrity writes).
    max_retries = 3 if critical else 1
    for attempt in range(max_retries + 1):
        try:
            await asyncio.wait_for(lock.acquire(), timeout=acquire_timeout)
        except asyncio.TimeoutError:
            _op_timeout_counts["acquire"] += 1
            last_error = None  # timeout is not an exception
            if attempt < max_retries:
                _diag = get_lock_diagnostics()
                logger.warning(
                    "[R-F1376] state_store %s: lock-acquire timed out after "
                    "%.0fs (attempt %d/%d, holder=%s held_for=%ss waiters=%s) "
                    "— retrying with backoff",
                    op_name, acquire_timeout, attempt + 1, max_retries + 1,
                    _diag.get("holder_task"), _diag.get("held_for_s"),
                    _diag.get("waiters"),
                )
                await asyncio.sleep(2 ** attempt)  # exponential backoff: 1s, 2s, 4s
                continue
            # Final failure — log ERROR
            _diag = get_lock_diagnostics()
            logger.error(
                "[R-F1341] state_store %s: lock-acquire timed out after %.0fs "
                "(%d attempts, holder=%s held_for=%ss waiters=%s) — dropping "
                "write to keep the event loop alive",
                op_name, acquire_timeout, max_retries + 1,
                _diag.get("holder_task"), _diag.get("held_for_s"),
                _diag.get("waiters"),
            )
            if critical:
                raise StateWriteError(f"{op_name}: lock-acquire timeout after {max_retries + 1} attempts")
            return default
        # Lock acquired — proceed
        break

    try:
        return await asyncio.wait_for(factory(), timeout=op_timeout)
    except asyncio.TimeoutError:
        _op_timeout_counts["op"] += 1
        logger.error(
            "[R-F1341] state_store %s: in-lock op exceeded %.0fs — aborting + "
            "resetting connection (self-heal)", op_name, op_timeout,
        )
        # Reconnect off the critical path so we release the lock promptly.
        try:
            asyncio.get_running_loop().create_task(_reconnect())
        except Exception:
            pass
        if critical:
            raise StateWriteError(f"{op_name}: in-lock op timeout")
        return default
    except StateWriteError:
        raise  # already distinguishable — don't re-wrap
    except Exception as e:
        logger.warning("[R-F1341] state_store %s failed: %s", op_name, e)
        if critical:
            raise StateWriteError(f"{op_name}: {e}") from e
        return default
    finally:
        try:
            lock.release()
        except RuntimeError:
            pass  # already released / not held — never raise from the wrapper


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
        _schedule_reconnect_if_dead(e)  # R-F1352: read path self-heals
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


# R-F669 (2026-05-17): CLAUDE.md §7 — "ARIA has infinite memory. No TTL
# on knowledge." Defensive guard: refuse to set an expiry on any key
# whose prefix matches a canonical knowledge namespace. Caught by an
# explicit ValueError so a buggy caller fails loudly in test rather
# than silently rotting data months later.
#
# Audit 2026-05-17 flagged: "state_store.sweep_expired() deletes
# entries with expires_at <= now. Safe today because no code writes
# knowledge with a TTL. Fragile because nothing structurally prevents
# a future caller from passing ex= on a knowledge key. Worth a
# defensive assert in state_store.set_json() if namespace='knowledge'."
_INFINITE_KEY_PREFIXES: tuple[str, ...] = (
    "crucix:aria:knowledge",         # primary knowledge store + shards
    "crucix:aria:verified_intel",    # verified facts + sources
    "crucix:aria:intel_ledger",      # signal ledger (R-F239 100yr)
    "crucix:aria:neural",            # neural memory + sub-spaces
    "crucix:aria:neural_edges",      # edge weights (variant w/o colon)
    "crucix:aria:neural_meta",       # neuron metadata
    "crucix:aria:neural_conflicts",  # flagged contradictions
)


def _is_infinite_key(key: str) -> bool:
    """True if `key` belongs to a knowledge namespace that must never
    carry a TTL per CLAUDE.md §7. Matches exact prefix OR prefix
    followed by ':' (sub-keyspace). Variants without colon (e.g.,
    'crucix:aria:neural_edges') are listed explicitly in
    _INFINITE_KEY_PREFIXES."""
    if not key:
        return False
    return any(key == p or key.startswith(p + ":") for p in _INFINITE_KEY_PREFIXES)


async def set(key: str, value: str, ex: int | None = None,
              keepttl: bool = False) -> None:
    # R-F669: refuse TTL on knowledge namespaces. Raise loudly so a
    # buggy caller fails in test, not in production six months later
    # when the data silently disappears.
    if ex is not None and _is_infinite_key(key):
        raise ValueError(
            f"R-F669: refusing TTL ex={ex}s on knowledge key {key!r} — "
            f"CLAUDE.md §7 mandates infinite retention on knowledge "
            f"namespaces ({_INFINITE_KEY_PREFIXES}). If you genuinely "
            f"need a TTL on this data, it does not belong in the "
            f"knowledge store — pick a different key prefix."
        )
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
    # R-F996 — wire to brain
    from .engine_wiring import wire_success
    wire_success(
        module="state_store",
        summary="Get Json",
        source_id="state_store:R-F996",
    )

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


async def lpush(key: str, value: str, *, critical: bool = False) -> None:
    async def _op():
        lst, expires_at = await _read_list(key)
        lst.insert(0, value)
        await _upsert(key, json.dumps(lst, default=str), kind="list",
                      expires_at=expires_at, keepttl=True)
    # R-F1351: critical=True → raise StateWriteError on drop (hash-chain ledgers).
    await _run_locked("lpush", _op, critical=critical)
    # R-F1252: yield event loop after list write so aiosqlite's single
    # worker thread can drain its queue and other coroutines can run.
    # Without this, sequential lpush/ltrim pairs (common in agent_registry,
    # capability_gaps, mistake_ledger) stall the event loop for 3-4s.
    await asyncio.sleep(0)


async def lpop(key: str) -> str | None:
    """Pop the first item from a list.

    Args:
        key: Redis key.

    Returns:
        The popped value, or None if the list is empty.
    """
    async def _op():
        lst, expires_at = await _read_list(key)
        if not lst:
            return None
        val = lst.pop(0)
        await _upsert(key, json.dumps(lst, default=str), kind="list",
                      expires_at=expires_at, keepttl=True)
        return str(val) if val is not None else None
    return await _run_locked("lpop", _op)  # R-F1341


async def ltrim(key: str, start: int, stop: int) -> None:
    async def _op():
        lst, expires_at = await _read_list(key)
        if not lst:
            return
        # Redis LTRIM is inclusive on both ends. stop=-1 means "to end".
        end = (stop + 1) if stop >= 0 else (len(lst) + stop + 1)
        trimmed = lst[start:end]
        await _upsert(key, json.dumps(trimmed, default=str), kind="list",
                      expires_at=expires_at, keepttl=True)
    await _run_locked("ltrim", _op)  # R-F1341
    await asyncio.sleep(0)


async def llen(key: str) -> int:
    lst, _ = await _read_list(key)
    return len(lst)


async def lrange(key: str, start: int, stop: int) -> list[str]:
    lst, _ = await _read_list(key)
    end = (stop + 1) if stop >= 0 else (len(lst) + stop + 1)
    return lst[start:end]


# ── Counters ─────────────────────────────────────────────────────────────

async def incr(key: str, amount: int = 1, *, critical: bool = False) -> int:
    """Atomic integer increment (atomic due to the lock + UPSERT)."""
    async def _op():
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
    return await _run_locked("incr", _op, default=0, critical=critical)  # R-F1341/1351


async def incrbyfloat(key: str, amount: float, *, critical: bool = False) -> float:
    """Atomic float increment."""
    async def _op():
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
    return await _run_locked("incrbyfloat", _op, default=0.0, critical=critical)  # R-F1341/1351


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
    async def _op():
        entries, expires_at = await _read_zset(key)
        # Remove existing member, then re-insert with the new score
        entries = [(s, m) for s, m in entries if m != member]
        entries.append((float(score), member))
        entries.sort(key=lambda x: x[0])
        await _upsert(key, json.dumps(entries, default=str), kind="zset",
                      expires_at=expires_at, keepttl=True)
    await _run_locked("zadd", _op)  # R-F1341


async def zrevrange(key: str, start: int, stop: int) -> list[str]:
    entries, _ = await _read_zset(key)
    entries.sort(key=lambda x: x[0], reverse=True)
    end = (stop + 1) if stop >= 0 else (len(entries) + stop + 1)
    return [m for _, m in entries[start:end]]


async def zrem(key: str, member: str) -> bool:
    async def _op():
        entries, expires_at = await _read_zset(key)
        before = len(entries)
        entries = [(s, m) for s, m in entries if m != member]
        await _upsert(key, json.dumps(entries, default=str), kind="zset",
                      expires_at=expires_at, keepttl=True)
        return len(entries) < before
    return bool(await _run_locked("zrem", _op, default=False))  # R-F1341


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


async def hset(key: str, mapping: dict, *, critical: bool = False) -> None:
    async def _op():
        existing, expires_at = await _read_hash(key)
        existing.update(mapping)
        await _upsert(key, json.dumps(existing, default=str), kind="hash",
                      expires_at=expires_at, keepttl=True)
    # R-F1341: was the 1311s-hold blackout culprit. R-F1351: critical passthrough.
    await _run_locked("hset", _op, critical=critical)


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
        _schedule_reconnect_if_dead(e)  # R-F1352: read path self-heals
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
        _schedule_reconnect_if_dead(e)  # R-F1352: read path self-heals
        return {"backend": "sqlite", "configured": True, "error": str(e)[:200]}
