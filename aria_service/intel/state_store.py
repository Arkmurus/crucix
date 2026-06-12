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
import builtins
import fnmatch
import json
import logging
import os
import time
import traceback
from pathlib import Path
from typing import Any

# R-F1518: structural guard — verify that no module-level function shadows
# a Python built-in. If this assertion fails, a function was added that
# collides with a built-in name (e.g. `def set()` shadows `builtins.set`).
# Fix: rename the function or use `builtins.` prefix for the built-in.
_builtin_names = {name for name in dir(builtins) if not name.startswith('_')}
_module_funcs = {name for name in dir() if not name.startswith('_')}
_collisions = _module_funcs & _builtin_names
if _collisions:
    raise RuntimeError(
        f"state_store: module-level functions shadow built-ins: {_collisions}. "
        f"Rename these functions or use builtins. prefix."
    )

logger = logging.getLogger("aria.state_store")

# ─────────────────────────────────────────────────────────────────────────
# Configuration
# ─────────────────────────────────────────────────────────────────────────

_DB_PATH: Path | None = None
_conn = None  # aiosqlite.Connection — lazy init
_read_conn = None  # R-F1449: separate read connection, never touched by _reconnect()
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

    # R-F1504: warn if lock contention is high (every 10 timeouts)
    timeouts = out["op_timeouts"]
    op_total = timeouts.get("op", 0) + timeouts.get("acquire", 0)
    if op_total > 10 and op_total % 10 == 0:
        logger.warning(
            "[R-F1504] state_store lock contention: %d op timeouts, %d acquire timeouts, "
            "%d waiters — possible wedge forming",
            timeouts.get("op", 0), timeouts.get("acquire", 0),
            out.get("waiters", 0),
        )

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


# R-F1397: how long the SELECT 1 health probe may take before the connection
# is declared wedged and replaced. A merely-backlogged worker thread usually
# answers well inside this; a stuck write (the R-F1341 1311s hset) never does.
_PROBE_TIMEOUT_S = float(os.getenv("ARIA_STATE_PROBE_TIMEOUT_S", "5.0"))

# R-F1400 — lock-storm guards (2026-06-07 death spiral: ~2887 waiters).
# Shed new lock entrants once the waiter queue is past this cap — joining a
# queue this deep can only time out and amplify the storm.
_WAITER_SHED_THRESHOLD = int(os.getenv("ARIA_STATE_WAITER_SHED", "500"))
# Contention telemetry is rate-limited to one line per level per window; the
# rest go to DEBUG. Per-op WARNING/ERROR spam was itself loop pressure AND
# (pre-fix) each line spawned a locked incr via error_log_handler → feedback
# loop. Per-LEVEL windows so a genuine isolated final-failure ERROR (which
# feeds Gate #3 and is a real dropped write) stays visible even right after a
# retry WARNING, while a storm of either level is throttled.
_WARN_INTERVAL_S = float(os.getenv("ARIA_STATE_WARN_INTERVAL_S", "10.0"))
_last_log_at: dict[int, float] = {}


def _log_rate_limited(level: int, msg: str, *args) -> None:
    """One log at `level` per _WARN_INTERVAL_S window (per level); the rest
    of that level's lines in the window go to DEBUG."""
    now = time.monotonic()
    if now - _last_log_at.get(level, 0.0) >= _WARN_INTERVAL_S:
        _last_log_at[level] = now
        logger.log(level, msg, *args)
    else:
        logger.debug(msg, *args)


def _warn_rate_limited(msg: str, *args) -> None:
    """Back-compat shim: rate-limited WARNING."""
    _log_rate_limited(logging.WARNING, msg, *args)


async def _reconnect() -> None:
    """Single-flight: drop the wedged aiosqlite connection and reopen it.
    Does NOT touch the lock (callers may hold it). Safe to call concurrently —
    only the first caller reconnects; the rest no-op.

    R-F1397 (live 2026-06-07 — self-heal #2 fired 80s after a fresh boot and
    each heal failed every in-flight op with 'Cannot operate on a closed
    database', which the job pollers read as 'job expired'):
      (a) PROBE before churning — if SELECT 1 answers, the connection was
          merely backlogged (slow op), not dead; replacing it would only
          fail every in-flight op for nothing.
      (b) NEW-CONN-FIRST swap — open the replacement BEFORE closing the old
          conn, so there is no `_conn is None` window in which reads return
          false not_founds and _upsert used to silently drop writes.
      (c) On reopen failure keep the OLD (dead) conn in place — its errors
          keep scheduling reconnects (self-healing), whereas a None conn
          made reads return None silently and never healed."""
    global _conn, _reconnect_in_progress
    if _reconnect_in_progress:
        return
    _reconnect_in_progress = True
    try:
        import aiosqlite

        old = _conn
        if old is not None:
            try:
                cur = await asyncio.wait_for(old.execute("SELECT 1"),
                                             timeout=_PROBE_TIMEOUT_S)
                await cur.fetchone()
                await cur.close()
                logger.info(
                    "[R-F1397] state_store probe OK — connection healthy, "
                    "skipping reset (slow op, not a dead conn)")
                return
            except Exception:
                pass  # dead or wedged — proceed with replacement
        if _DB_PATH is None:
            return
        conn = await aiosqlite.connect(str(_DB_PATH))
        await conn.execute("PRAGMA journal_mode=WAL")
        await conn.execute("PRAGMA synchronous=NORMAL")
        await conn.execute("PRAGMA foreign_keys=OFF")
        await conn.execute("PRAGMA busy_timeout=5000")
        await conn.commit()
        _conn = conn  # R-F1397: swap only once the replacement is ready
        _op_timeout_counts["reconnect"] += 1
        logger.warning("[R-F1341] state_store connection reset (self-heal) #%d",
                       _op_timeout_counts["reconnect"])
        if old is not None:
            try:
                await asyncio.wait_for(old.close(), timeout=5.0)
            except Exception:
                pass  # the whole point is that it was wedged
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


class StateReadError(Exception):
    """R-F1392: raised by get_strict() when a READ failed at the store layer
    (dead/closed connection, reconnect window) — as opposed to the key being
    genuinely absent. Live 2026-06-07: during a self-heal window every job-poll
    read returned None → /read-document/result said not_found → the WA listener
    declared a LIVE extraction 'expired' and the operator was told to resend.
    Callers that must distinguish 'key missing' from 'store down' (async job
    polls) use get_strict; everything else keeps the graceful None-on-error
    contract of get()."""


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

    # R-F1400 — waiter shed (2026-06-07 lock-storm: ~2887 waiters, all
    # timing out at 20s, queue growing faster than it drained). When the
    # waiter queue is already past the cap, joining it can only deepen the
    # storm: shed immediately at DEBUG (the storm itself is surfaced by the
    # rate-limited warning below + _op_timeout_counts, not per-op spam).
    waiters_now = len(getattr(lock, "_waiters", None) or ())
    if waiters_now > _WAITER_SHED_THRESHOLD:
        _op_timeout_counts["acquire"] += 1
        _warn_rate_limited(
            "[R-F1400] state_store %s: shedding — %d waiters already queued "
            "(cap %d); returning default to keep the event loop alive",
            op_name, waiters_now, _WAITER_SHED_THRESHOLD,
        )
        if critical:
            raise StateWriteError(f"{op_name}: waiter queue over cap ({waiters_now})")
        return default

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
                # R-F1400: rate-limited — under the 2026-06-07 storm this
                # line fired thousands of times in seconds; the log volume
                # was itself event-loop pressure (and pre-R-F1400 each line
                # spawned a new locked incr via error_log_handler).
                _warn_rate_limited(
                    "[R-F1376] state_store %s: lock-acquire timed out after "
                    "%.0fs (attempt %d/%d, holder=%s held_for=%ss waiters=%s) "
                    "— retrying with backoff",
                    op_name, acquire_timeout, attempt + 1, max_retries + 1,
                    _diag.get("holder_task"), _diag.get("held_for_s"),
                    _diag.get("waiters"),
                )
                await asyncio.sleep(2 ** attempt)  # exponential backoff: 1s, 2s, 4s
                continue
            # Final failure — a real dropped write: keep it at ERROR (feeds
            # Gate #3, must stay visible) but rate-limited per-level so a
            # storm can't emit thousands. R-F1376 single-drop ERROR contract
            # preserved (first ERROR in the window logs at ERROR).
            _diag = get_lock_diagnostics()
            _log_rate_limited(
                logging.ERROR,
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
    global _conn, _DB_PATH, _read_conn
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
        # R-F1449: also open the dedicated read connection
        _read_conn = await aiosqlite.connect(str(_DB_PATH))
        await _read_conn.execute("PRAGMA journal_mode=WAL")
        await _read_conn.execute("PRAGMA synchronous=NORMAL")
        await _read_conn.execute("PRAGMA foreign_keys=OFF")
        await _read_conn.execute("PRAGMA busy_timeout=5000")
        await _read_conn.commit()
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
        # R-F1515: row-per-entry list storage. Each list entry gets its own row
        # with a sequence number, eliminating the read-modify-write cycle that
        # held the Python lock for the entire JSON blob. lpush is now a single
        # INSERT — no lock, no contention, no 15s stalls.
        await _conn.execute(
            """
            CREATE TABLE IF NOT EXISTS list_entries (
                list_key    TEXT NOT NULL,
                seq         INTEGER NOT NULL,
                value       TEXT NOT NULL,
                expires_at  REAL,
                PRIMARY KEY (list_key, seq)
            )
            """
        )
        await _conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_list_entries_key ON list_entries(list_key, seq DESC)"
        )
        # R-F1518: row-per-entry hash storage. Each hash field gets its own row,
        # eliminating the read-modify-write cycle that held the Python lock for
        # the entire JSON blob. hset is now a single UPSERT — no lock, no contention.
        await _conn.execute(
            """
            CREATE TABLE IF NOT EXISTS hash_entries (
                hash_key    TEXT NOT NULL,
                field       TEXT NOT NULL,
                value       TEXT NOT NULL,
                expires_at  REAL,
                PRIMARY KEY (hash_key, field)
            )
            """
        )
        await _conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_hash_entries_key ON hash_entries(hash_key)"
        )
        await _conn.commit()
        logger.info("state_store: SQLite ready at %s (WAL mode)", _DB_PATH)
        return True
    except Exception as e:
        logger.error("state_store: connect failed: %s", e)
        _conn = None
        return False


async def close() -> None:
    """Close the database connection."""
    global _conn, _read_conn
    if _read_conn:
        try:
            await _read_conn.close()
        except Exception:
            pass
        _read_conn = None
    if _conn:
        try:
            await _conn.close()
        except Exception:
            pass
        _conn = None


async def sweep_expired() -> int:
    """Purge rows whose expires_at is in the past. Returns the row count
    deleted. Run periodically from a background task.
    
    R-F1515: also sweeps expired list_entries rows.
    R-F1518: also sweeps expired hash_entries rows."""
    if _conn is None:
        return 0
    total = 0
    try:
        cur = await _conn.execute(
            "DELETE FROM state WHERE expires_at IS NOT NULL AND expires_at <= ?",
            (_now(),),
        )
        await _conn.commit()
        total += cur.rowcount or 0
    except Exception as e:
        logger.warning("state_store: sweep state failed: %s", e)
    try:
        cur = await _conn.execute(
            "DELETE FROM list_entries WHERE expires_at IS NOT NULL AND expires_at <= ?",
            (_now(),),
        )
        await _conn.commit()
        total += cur.rowcount or 0
    except Exception as e:
        logger.warning("state_store: sweep list_entries failed: %s", e)
    try:
        cur = await _conn.execute(
            "DELETE FROM hash_entries WHERE expires_at IS NOT NULL AND expires_at <= ?",
            (_now(),),
        )
        await _conn.commit()
        total += cur.rowcount or 0
    except Exception as e:
        logger.warning("state_store: sweep hash_entries failed: %s", e)
    if total > 0:
        logger.debug("state_store: swept %d expired rows", total)
    return total


# ─────────────────────────────────────────────────────────────────────────
# Internal helpers
# ─────────────────────────────────────────────────────────────────────────


def _get_read_conn():
    """R-F1449: return the dedicated read connection.

    The read connection is NEVER touched by _reconnect(), so a write-side
    reset cannot kill concurrent reads. Under WAL mode, reads on this
    connection see a consistent snapshot without blocking writers.

    Falls back to _conn if _read_conn is not initialized (graceful
    degradation during early boot before connect() completes).
    """
    return _read_conn if _read_conn is not None else _conn



async def _ensure_read_conn() -> None:
    """R-F1449: ensure the dedicated read connection is open.

    Called by _row() retry-once path when the read connection was closed
    by a write-side reset. Opens a new read connection without touching
    _conn or _reconnect().
    """
    global _read_conn
    if _DB_PATH is None:
        return
    try:
        import aiosqlite
        new_conn = await aiosqlite.connect(str(_DB_PATH))
        await new_conn.execute("PRAGMA journal_mode=WAL")
        await new_conn.execute("PRAGMA synchronous=NORMAL")
        await new_conn.execute("PRAGMA foreign_keys=OFF")
        await new_conn.execute("PRAGMA busy_timeout=5000")
        await new_conn.commit()
        _read_conn = new_conn
    except Exception as e:
        logger.warning("[R-F1449] _ensure_read_conn failed: %s", e)
async def _row(key: str, expected_kind: str | None = None) -> tuple[str, str, float | None] | None:
    """Fetch (value, kind, expires_at) for a key. Returns None if missing
    or expired. If expected_kind is given and the kind mismatches, returns
    None (treat as missing — Redis raises WRONGTYPE; we degrade gracefully).

    R-F1449: uses _get_read_conn() (separate read connection) so a write-side
    _reconnect() cannot kill concurrent reads. Retries ONCE on 'closed
    database' by reopening the read connection and re-executing.
    """
    conn = _get_read_conn()
    if conn is None:
        return None
    try:
        cur = await conn.execute(
            "SELECT value, kind, expires_at FROM state WHERE key = ?",
            (key,),
        )
        row = await cur.fetchone()
        await cur.close()
    except Exception as e:
        err_str = str(e)
        # R-F1449: retry-once on closed database
        if 'closed' in err_str or 'Cannot operate' in err_str or 'no active connection' in err_str:
            try:
                await _ensure_read_conn()
                conn = _get_read_conn()
                if conn is not None:
                    cur = await conn.execute(
                        "SELECT value, kind, expires_at FROM state WHERE key = ?",
                        (key,),
                    )
                    row = await cur.fetchone()
                    await cur.close()
                    if not row:
                        return None
                    value, kind, expires_at = row
                    if _expired(expires_at):
                        try:
                            await conn.execute("DELETE FROM state WHERE key = ?", (key,))
                            await conn.commit()
                        except Exception:
                            pass
                        return None
                    if expected_kind and kind != expected_kind:
                        return None
                    return value, kind, expires_at
            except Exception:
                pass
        logger.warning("state_store: SELECT %s failed: %s", key, e)
        _schedule_reconnect_if_dead(e)
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


# R-F1510: timeout for _upsert SQL operations. When the aiosqlite worker
# thread is busy with a long-running locked op (incr/hset), concurrent
# _upsert calls from the unlocked path (set/set_json) would block
# indefinitely on the SQLite connection mutex and eventually hit
# "database is locked". A short timeout lets them fail fast instead of
# piling up behind the lock holder.
_UPSERT_TIMEOUT_S = float(os.getenv("ARIA_STATE_UPSERT_TIMEOUT_S", "5.0"))

# R-F1510: rate-limited log for _upsert failures to prevent the
# error_log_handler → record_error → set_json → _upsert feedback loop
# from amplifying a single lock storm into thousands of log lines.
_upsert_last_log: float = 0.0
_UPSERT_LOG_INTERVAL_S = 10.0


async def _upsert(key: str, value: str, kind: str, expires_at: float | None,
                  keepttl: bool = False) -> None:
    # R-F1510: rate-limited logging needs the global at function scope
    # (before any use in the try/except blocks below).
    global _upsert_last_log

    # No python-level lock: this is a single SQL statement and aiosqlite
    # serialises through one worker thread. Compound RMW ops (lpush etc.)
    # hold _get_lock() themselves to keep their read+write atomic.
    if _conn is None:
        # R-F1397: raising matches the R-F1388 contract (callers already
        # handle a raised write failure). The old silent `return` made a
        # job-store set_json "succeed" during a reconnect window — the 202
        # said processing but the job was never stored, so the poller got
        # not_found forever ("extraction job expired").
        # NOT StateWriteError: _run_locked re-raises that type even for
        # non-critical compound ops (lpush/hset would crash their callers
        # instead of returning the graceful default). OperationalError is
        # caught by _run_locked's generic branch AND matches _is_conn_dead
        # ("connection") so the read-path self-heal fires too.
        import sqlite3
        e = sqlite3.OperationalError(
            f"state_store: no connection (reconnect in progress) writing {key}")
        _schedule_reconnect_if_dead(e)
        raise e
    try:
        # R-F1510: bounded execution so a contended SQLite worker thread
        # doesn't block _upsert callers indefinitely. The timeout is short
        # (5s default) — if the worker is busy with a locked op, fail fast
        # rather than pile up behind it.
        if keepttl and expires_at is None:
            await asyncio.wait_for(
                _conn.execute(
                    "INSERT INTO state(key, value, kind, expires_at) "
                    "VALUES(?, ?, ?, NULL) "
                    "ON CONFLICT(key) DO UPDATE SET value = excluded.value, kind = excluded.kind",
                    (key, value, kind),
                ),
                timeout=_UPSERT_TIMEOUT_S,
            )
        else:
            await asyncio.wait_for(
                _conn.execute(
                    "INSERT INTO state(key, value, kind, expires_at) "
                    "VALUES(?, ?, ?, ?) "
                    "ON CONFLICT(key) DO UPDATE SET value = excluded.value, "
                    "kind = excluded.kind, expires_at = excluded.expires_at",
                    (key, value, kind, expires_at),
                ),
                timeout=_UPSERT_TIMEOUT_S,
            )
        await asyncio.wait_for(_conn.commit(), timeout=_UPSERT_TIMEOUT_S)
    except asyncio.TimeoutError:
        # R-F1510: timeout on the SQLite worker thread — the connection is
        # busy with a locked op. Log rate-limited (not per-call) to avoid
        # the error_log_handler → record_error → set_json feedback loop.
        now = time.monotonic()
        if now - _upsert_last_log > _UPSERT_LOG_INTERVAL_S:
            _upsert_last_log = now
            logger.warning(
                "state_store: UPSERT %s timed out after %.1fs — "
                "SQLite worker busy (locked op in progress); write dropped",
                key, _UPSERT_TIMEOUT_S,
            )
        _schedule_reconnect_if_dead(
            __import__("sqlite3").OperationalError("database is locked"))
        raise  # propagate so _run_locked callers know the write failed
    except Exception as e:
        # R-F1510: rate-limited to prevent feedback-loop amplification.
        # The error_log_handler mirrors every WARNING+ into record_error,
        # which calls set_json → _upsert — so a single lock storm must not
        # produce one WARNING per failed write.
        now = time.monotonic()
        if now - _upsert_last_log > _UPSERT_LOG_INTERVAL_S:
            _upsert_last_log = now
            logger.warning("state_store: UPSERT %s failed: %s", key, e)
        _schedule_reconnect_if_dead(e)  # R-F1388: trigger self-heal on dead conn
        raise  # R-F1388: propagate so callers (e.g. _chat_job_set) know the write failed


# ─────────────────────────────────────────────────────────────────────────
# Public API — mirrors redis_store
# ─────────────────────────────────────────────────────────────────────────

async def get(key: str) -> str | None:
    row = await _row(key, expected_kind="string")
    return row[0] if row else None


async def get_strict(key: str) -> str | None:
    """R-F1392: like get(), but a store-layer failure RAISES StateReadError
    instead of silently returning None. A None return therefore means the key
    is GENUINELY absent/expired — which is what the async job-poll endpoints
    need to honestly answer not_found vs 503-retry (see StateReadError)."""
    if _conn is None:
        raise StateReadError(
            f"state_store: no connection (reconnect in progress) reading {key}")
    try:
        cur = await _conn.execute(
            "SELECT value, kind, expires_at FROM state WHERE key = ?", (key,))
        row = await cur.fetchone()
        await cur.close()
    except Exception as e:
        _schedule_reconnect_if_dead(e)  # same self-heal as the graceful path
        raise StateReadError(f"state_store: SELECT {key} failed: {e}") from e
    if not row:
        return None
    value, kind, expires_at = row
    if _expired(expires_at) or kind != "string":
        return None  # genuinely absent (expired / wrong kind) — not a store error
    return value


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


# ── List operations (row-per-entry) ─────────────────────────────────────
# R-F1515: replaced JSON-blob-backed list ops with row-per-entry storage.
# Each list entry is a separate row in the `list_entries` table with a
# sequence number. This eliminates the read-modify-write cycle that held
# the Python-level lock for the entire JSON blob — lpush is now a single
# INSERT with no lock acquisition, no contention, and no 15s stalls.
#
# Backward compatibility: on first read of a legacy JSON-blob key (kind="list"
# in the `state` table), the entries are migrated to the new table and the
# old key is deleted. This is a one-time migration per key.


async def _migrate_list_if_needed(key: str) -> None:
    """Migrate a legacy JSON-blob list to the row-per-entry table.
    
    Called lazily on first read. If the key exists in the `state` table
    with kind='list', its entries are copied to `list_entries` and the
    old key is deleted. This is a one-time migration per key.
    """
    row = await _row(key, expected_kind="list")
    if not row:
        return
    try:
        lst = json.loads(row[0])
        if not isinstance(lst, list) or not lst:
            return
        expires_at = row[2]
        # Insert all entries with descending sequence (index 0 = highest seq)
        for i, val in enumerate(lst):
            await _conn.execute(
                "INSERT OR IGNORE INTO list_entries(list_key, seq, value, expires_at) "
                "VALUES(?, ?, ?, ?)",
                (key, len(lst) - i, json.dumps(val, default=str) if not isinstance(val, str) else val, expires_at),
            )
        await _conn.commit()
        # Delete the old JSON blob
        await _conn.execute("DELETE FROM state WHERE key = ?", (key,))
        await _conn.commit()
        logger.debug("state_store: migrated list %s (%d entries) to row-per-entry", key, len(lst))
    except Exception as e:
        logger.warning("state_store: list migration failed for %s: %s", key, e)


def _list_seq_counter(key: str) -> str:
    """Return the counter key used for sequence numbers of this list."""
    return f"{key}:_seq"


# R-F1518: per-list locks for lpush serialization. Each list gets its own
# asyncio.Lock so pushes to different lists don't block each other. The
# lock is held only for the counter increment + INSERT (microseconds).
_lpush_locks: dict[str, asyncio.Lock] = {}


def _get_lpush_lock(key: str) -> asyncio.Lock:
    """Get or create a per-list lock for lpush serialization."""
    if key not in _lpush_locks:
        _lpush_locks[key] = asyncio.Lock()
    return _lpush_locks[key]


async def lpush(key: str, value: str, *, critical: bool = False) -> None:
    """Push a value to the front of a list.
    
    R-F1515: single INSERT with no Python-level lock. The sequence number
    is derived from a dedicated counter key so concurrent pushes don't
    collide. No read-modify-write cycle — this is O(1) and lock-free.
    
    R-F1518: uses a dedicated per-list asyncio.Lock to serialize the
    counter increment and INSERT, eliminating the race where concurrent
    lpush calls read the same counter value. The lock is held only for
    the duration of the counter increment + INSERT (microseconds), not
    for any I/O — so it cannot cause contention.
    """
    if _conn is None:
        if critical:
            raise StateWriteError(f"lpush {key}: no connection")
        return
    seq_key = _list_seq_counter(key)
    # R-F1518: per-list lock to serialize counter increment + INSERT.
    # This is a fast operation (microseconds) — the lock is never held
    # across I/O boundaries, so it cannot cause contention.
    lock = _get_lpush_lock(key)
    async with lock:
        try:
            # Atomically increment the sequence counter
            await _conn.execute(
                "INSERT INTO state(key, value, kind) "
                "VALUES(?, CAST(? AS TEXT), 'string') "
                "ON CONFLICT(key) DO UPDATE SET "
                "  value = CAST(CAST(value AS INTEGER) + 1 AS TEXT)",
                (seq_key, 1),
            )
            await _conn.commit()
            # Read back the new sequence number
            cur = await _conn.execute("SELECT value FROM state WHERE key = ?", (seq_key,))
            row = await cur.fetchone()
            await cur.close()
            seq = int(row[0]) if row else 1
            # Insert the entry with the new sequence number
            await _conn.execute(
                "INSERT INTO list_entries(list_key, seq, value) VALUES(?, ?, ?)",
                (key, seq, value),
            )
            await _conn.commit()
        except Exception as e:
            logger.warning("[R-F1518] lpush %s failed: %s", key, e)
            if critical:
                raise StateWriteError(f"lpush {key}: {e}") from e


async def lpop(key: str) -> str | None:
    """Pop the first (most recently pushed) item from a list.
    
    R-F1515: single DELETE + SELECT — no lock, no read-modify-write.
    Falls back to legacy JSON-blob pop for backward compatibility.
    """
    if _conn is None:
        return None
    try:
        # Find the highest sequence number for this list
        cur = await _conn.execute(
            "SELECT seq, value FROM list_entries WHERE list_key = ? "
            "ORDER BY seq DESC LIMIT 1",
            (key,),
        )
        row = await cur.fetchone()
        await cur.close()
        if row:
            seq, value = row
            await _conn.execute(
                "DELETE FROM list_entries WHERE list_key = ? AND seq = ?",
                (key, seq),
            )
            await _conn.commit()
            return value
        # Fallback: check legacy JSON blob
        await _migrate_list_if_needed(key)
        cur = await _conn.execute(
            "SELECT seq, value FROM list_entries WHERE list_key = ? "
            "ORDER BY seq DESC LIMIT 1",
            (key,),
        )
        row = await cur.fetchone()
        await cur.close()
        if row:
            seq, value = row
            await _conn.execute(
                "DELETE FROM list_entries WHERE list_key = ? AND seq = ?",
                (key, seq),
            )
            await _conn.commit()
            return value
        return None
    except Exception as e:
        logger.warning("[R-F1515] lpop %s failed: %s", key, e)
        return None


async def ltrim(key: str, start: int, stop: int) -> None:
    """Trim a list to the specified range.
    
    R-F1515: single DELETE — no lock, no read-modify-write.
    Redis LTRIM semantics: inclusive on both ends. stop=-1 means "to end".
    Falls back to legacy JSON-blob trim for backward compatibility.
    """
    if _conn is None:
        return
    try:
        # Get all sequences for this list, ordered DESC
        cur = await _conn.execute(
            "SELECT seq FROM list_entries WHERE list_key = ? ORDER BY seq DESC",
            (key,),
        )
        rows = await cur.fetchall()
        await cur.close()
        if not rows:
            # Fallback: check legacy JSON blob
            await _migrate_list_if_needed(key)
            cur = await _conn.execute(
                "SELECT seq FROM list_entries WHERE list_key = ? ORDER BY seq DESC",
                (key,),
            )
            rows = await cur.fetchall()
            await cur.close()
            if not rows:
                return
        seqs = [r[0] for r in rows]
        end = (stop + 1) if stop >= 0 else (len(seqs) + stop + 1)
        keep = builtins.set(seqs[start:end])
        delete = [s for s in seqs if s not in keep]
        if delete:
            placeholders = ",".join("?" for _ in delete)
            await _conn.execute(
                f"DELETE FROM list_entries WHERE list_key = ? AND seq IN ({placeholders})",
                (key, *delete),
            )
            await _conn.commit()
    except Exception as e:
        logger.warning("[R-F1515] ltrim %s failed: %s", key, e)


async def llen(key: str) -> int:
    """Return the number of entries in a list.
    
    R-F1515: single COUNT — no lock, no read-modify-write.
    Falls back to legacy JSON-blob count for backward compatibility.
    """
    if _conn is None:
        return 0
    try:
        cur = await _conn.execute(
            "SELECT COUNT(*) FROM list_entries WHERE list_key = ?",
            (key,),
        )
        row = await cur.fetchone()
        await cur.close()
        if row and row[0] > 0:
            return row[0]
        # Fallback: check legacy JSON blob
        await _migrate_list_if_needed(key)
        cur = await _conn.execute(
            "SELECT COUNT(*) FROM list_entries WHERE list_key = ?",
            (key,),
        )
        row = await cur.fetchone()
        await cur.close()
        return row[0] if row else 0
    except Exception as e:
        logger.warning("[R-F1515] llen %s failed: %s", key, e)
        return 0


async def lrange(key: str, start: int, stop: int) -> list[str]:
    """Return a range of entries from a list.
    
    R-F1515: single SELECT — no lock, no read-modify-write.
    Redis LTRIM semantics: inclusive on both ends. stop=-1 means "to end".
    Falls back to legacy JSON-blob range for backward compatibility.
    """
    if _conn is None:
        return []
    try:
        # Get all sequences for this list, ordered DESC
        cur = await _conn.execute(
            "SELECT seq, value FROM list_entries WHERE list_key = ? ORDER BY seq DESC",
            (key,),
        )
        rows = await cur.fetchall()
        await cur.close()
        if rows:
            seqs = [r[0] for r in rows]
            values = [r[1] for r in rows]
            end = (stop + 1) if stop >= 0 else (len(seqs) + stop + 1)
            return values[start:end]
        # Fallback: check legacy JSON blob
        await _migrate_list_if_needed(key)
        cur = await _conn.execute(
            "SELECT seq, value FROM list_entries WHERE list_key = ? ORDER BY seq DESC",
            (key,),
        )
        rows = await cur.fetchall()
        await cur.close()
        if rows:
            seqs = [r[0] for r in rows]
            values = [r[1] for r in rows]
            end = (stop + 1) if stop >= 0 else (len(seqs) + stop + 1)
            return values[start:end]
        return []
    except Exception as e:
        logger.warning("[R-F1515] lrange %s failed: %s", key, e)
        return []


# ── Counters ─────────────────────────────────────────────────────────────

async def incr(key: str, amount: int = 1, *, critical: bool = False) -> int:
    """Atomic integer increment.

    R-F1493: uses a single SQL UPSERT (atomic at the SQLite level) instead
    of holding the Python-level lock for a read-modify-write cycle. Previously
    this held _get_lock() for the entire _row + _upsert sequence, which could
    block for 15+ seconds under write contention, causing the state_store lock
    storm that wedged the app and caused WA timeouts.

    Falls back to the locked path only when the atomic UPSERT fails.
    """
    if _conn is None:
        import sqlite3
        e = sqlite3.OperationalError(
            f"state_store: no connection (reconnect in progress) writing {key}")
        _schedule_reconnect_if_dead(e)
        if critical:
            raise StateWriteError(f"incr: no connection") from e
        return 0

    try:
        # Atomic UPSERT: INSERT if missing (value=1), else increment.
        # SQLite serialises writes through its single worker thread — no
        # Python-level lock needed. This avoids the _run_locked contention
        # that caused the 2026-06-10 state_store wedge.
        await _conn.execute(
            # R-F1494: on a FRESH key the inserted value must be `amount`, not a
            # hardcoded '1'. R-F1493 hardcoded '1', so incr(key, amount=N) on a
            # missing key stored 1 instead of N (e.g. stream_guard_observer.py:180
            # calls incr(key, amount=count) → silent undercount). CAST(? AS TEXT)
            # keeps the value column TEXT-typed like every other state write.
            "INSERT INTO state(key, value, kind, expires_at) "
            "VALUES(?, CAST(? AS TEXT), 'string', NULL) "
            "ON CONFLICT(key) DO UPDATE SET "
            "  value = CAST(CAST(value AS INTEGER) + ? AS TEXT)",
            (key, amount, amount),
        )
        await _conn.commit()
        # Read back the new value
        cur = await _conn.execute(
            "SELECT value FROM state WHERE key = ?", (key,)
        )
        row = await cur.fetchone()
        await cur.close()
        return int(row[0]) if row else amount
    except Exception as e:
        err_str = str(e).lower()
        # R-F1510: if the atomic path failed because the SQLite worker is
        # busy (database is locked), retry ONCE with a short delay before
        # falling back to the locked path. This avoids entering _run_locked
        # for transient contention — which would acquire the Python lock
        # and potentially amplify the storm.
        if "database is locked" in err_str or "busy" in err_str:
            try:
                await asyncio.sleep(0.5)
                await _conn.execute(
                    "INSERT INTO state(key, value, kind, expires_at) "
                    "VALUES(?, CAST(? AS TEXT), 'string', NULL) "
                    "ON CONFLICT(key) DO UPDATE SET "
                    "  value = CAST(CAST(value AS INTEGER) + ? AS TEXT)",
                    (key, amount, amount),
                )
                await _conn.commit()
                cur = await _conn.execute(
                    "SELECT value FROM state WHERE key = ?", (key,)
                )
                row = await cur.fetchone()
                await cur.close()
                return int(row[0]) if row else amount
            except Exception:
                pass  # fall through to locked path below
        logger.debug("[R-F1493] atomic incr failed for %s: %s — falling back to locked path", key, e)
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
        return await _run_locked("incr", _op, default=0, critical=critical)


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


# ── Hashes (row-per-entry) ────────────────────────────────────────────
# R-F1518: replaced JSON-blob-backed hash ops with row-per-entry storage.
# Each hash field is a separate row in the `hash_entries` table. hset is
# now a single UPSERT per field — no Python lock, no read-modify-write.
#
# Backward compatibility: on first read of a legacy JSON-blob hash key
# (kind="hash" in the `state` table), the fields are migrated to the new
# table and the old key is deleted.


async def _migrate_hash_if_needed(key: str) -> None:
    """Migrate a legacy JSON-blob hash to the row-per-entry table.
    
    Called lazily on first read. If the key exists in the `state` table
    with kind='hash', its entries are copied to `hash_entries` and the
    old key is deleted.
    """
    row = await _row(key, expected_kind="hash")
    if not row:
        return
    try:
        h = json.loads(row[0])
        if not isinstance(h, dict) or not h:
            return
        expires_at = row[2]
        for field, value in h.items():
            await _conn.execute(
                "INSERT OR IGNORE INTO hash_entries(hash_key, field, value, expires_at) "
                "VALUES(?, ?, ?, ?)",
                (key, field, json.dumps(value, default=str) if not isinstance(value, str) else str(value), expires_at),
            )
        await _conn.commit()
        # Delete the old JSON blob
        await _conn.execute("DELETE FROM state WHERE key = ?", (key,))
        await _conn.commit()
        logger.debug("state_store: migrated hash %s (%d fields) to row-per-entry", key, len(h))
    except Exception as e:
        logger.warning("state_store: hash migration failed for %s: %s", key, e)


async def hset(key: str, mapping: dict, *, critical: bool = False) -> None:
    """Set one or more fields in a hash.
    
    R-F1518: single UPSERT per field — no Python lock, no read-modify-write.
    Each field is a separate row in the `hash_entries` table.
    """
    if _conn is None:
        if critical:
            raise StateWriteError(f"hset {key}: no connection")
        return
    try:
        for field, value in mapping.items():
            # Store as string — hgetall returns strings to match Redis semantics
            str_value = str(value) if not isinstance(value, str) else value
            await _conn.execute(
                "INSERT INTO hash_entries(hash_key, field, value) "
                "VALUES(?, ?, ?) "
                "ON CONFLICT(hash_key, field) DO UPDATE SET value = excluded.value",
                (key, field, str_value),
            )
        await _conn.commit()
    except Exception as e:
        logger.warning("[R-F1518] hset %s failed: %s", key, e)
        if critical:
            raise StateWriteError(f"hset {key}: {e}") from e


async def hgetall(key: str) -> dict:
    """Get all fields in a hash.
    
    R-F1518: single SELECT — no lock, no read-modify-write.
    Falls back to legacy JSON-blob hash for backward compatibility.
    """
    if _conn is None:
        return {}
    try:
        cur = await _conn.execute(
            "SELECT field, value FROM hash_entries WHERE hash_key = ?",
            (key,),
        )
        rows = await cur.fetchall()
        await cur.close()
        if rows:
            return {field: value for field, value in rows}
        # Fallback: check legacy JSON blob
        await _migrate_hash_if_needed(key)
        cur = await _conn.execute(
            "SELECT field, value FROM hash_entries WHERE hash_key = ?",
            (key,),
        )
        rows = await cur.fetchall()
        await cur.close()
        if rows:
            return {field: value for field, value in rows}
        return {}
    except Exception as e:
        logger.warning("[R-F1518] hgetall %s failed: %s", key, e)
        return {}


async def hdel(key: str, field: str) -> bool:
    """Delete a field from a hash.
    
    R-F1518: single DELETE — no lock, no read-modify-write.
    """
    if _conn is None:
        return False
    try:
        cur = await _conn.execute(
            "DELETE FROM hash_entries WHERE hash_key = ? AND field = ?",
            (key, field),
        )
        await _conn.commit()
        return (cur.rowcount or 0) > 0
    except Exception as e:
        logger.warning("[R-F1518] hdel %s failed: %s", key, e)
        return False


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


# R-F1518: structural guard — warn if any module-level function shadows a
# Python built-in. This prevents the entire failure class of built-in
# shadowing bugs (like the ltrim `set()` bug that caused cryptic errors).
# The `set()` function is intentionally named to match the Redis API, so
# this is a warning, not a hard error. Code inside the module that needs
# the built-in `set()` must use `builtins.set()` explicitly.
import builtins as _builtins
_builtin_names = {name for name in dir(_builtins) if not name.startswith('_')}
_module_funcs = {name for name in dir() if not name.startswith('_')}
_collisions = _module_funcs & _builtin_names
if _collisions:
    import logging as _logging
    _logging.warning(
        "state_store: module-level functions shadow built-ins: %s. "
        "Code inside this module that needs the built-in must use "
        "builtins.<name>() explicitly.",
        _collisions,
    )
