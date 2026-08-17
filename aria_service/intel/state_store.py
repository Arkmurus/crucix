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
from .engine_wiring import wire_failure

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


def _ensure_aiosqlite_daemon_workers(aiosqlite_module: Any) -> None:
    """Ensure aiosqlite worker threads cannot keep the process alive."""
    try:
        connection_cls = aiosqlite_module.core.Connection
        if getattr(connection_cls, "_aria_daemon_patch", False):
            return
        original_init = connection_cls.__init__

        def _patched_init(self, *args, **kwargs):
            original_init(self, *args, **kwargs)
            try:
                self._thread.daemon = True
            except Exception:
                pass

        connection_cls.__init__ = _patched_init
        connection_cls._aria_daemon_patch = True
    except Exception:
        pass

# ─────────────────────────────────────────────────────────────────────────
# Configuration
# ─────────────────────────────────────────────────────────────────────────

_DB_PATH: Path | None = None
_conn = None  # aiosqlite.Connection — lazy init (compound ops: hset, lpush, etc.)
_read_conn = None  # R-F1449: separate read connection, never touched by _reconnect()
# R-F2242: read-connection POOL. A SINGLE aiosqlite read connection serializes
# ALL key-value reads (get/get_json/scan) on one background thread, so a burst of
# concurrent reads — the dashboard 24-panel refresh, the self-diagnostic probes
# (capability_card/pending_actions/coverage_heatmap ReadTimeouts), WA→brain
# fetches (R-F1515 "brain fetch FAILED after 3 attempts") — queues behind one
# another. A small pool (each connection its own thread) lets those reads run
# truly concurrently on the shared-cpu-4x box. WAL supports N readers + 1 writer
# safely; the writer stays the separate _conn queue (R-F1449/R-F1541), untouched.
# _read_conn is kept as pool member [0] so existing close()/reconnect refs hold.
_read_pool: list = []
_read_pool_rr = 0  # round-robin cursor
_READ_POOL_SIZE = max(1, int(os.getenv("ARIA_STATE_READ_POOL_SIZE", "3")))

# R-F2754 — superseded-connection reaper. _reconnect() (write) and
# _ensure_read_conn() (read pool) both open a REPLACEMENT connection and swap it
# in, but historically never CLOSED the old one — so every self-heal cycle
# orphaned the old aiosqlite worker thread (each connection owns one). Live
# forensic (wedge_679, 2026-07-18): 54 live _connection_worker_thread threads vs
# ~6 intended → thread oversubscription starves the event loop (2–5s heartbeat
# stalls). Strong refs are held so the detached close tasks aren't GC'd mid-flight.
_reap_tasks: set = set()

# R-F1541: bounded write queue replaces the timeout-and-drop _upsert model.
# Instead of every write going through _conn.execute() with a 30s timeout
# (which silently drops writes when the worker thread is saturated), writes
# are enqueued to an asyncio.Queue and processed by a background worker.
# The queue has a bounded max size — if full, the caller gets StateWriteError
# immediately (backpressure) instead of timing out after 30s and silently
# losing data. This eliminates the entire failure class of:
#   - 30s timeout → silent data loss
#   - timeout WARNING → error_log_handler → record_error → more timeouts
#   - brain_hook circuit trip → all learning stops
#   - autonomous_engine blackout → heartbeat stale
_QUEUED_WRITES: asyncio.Queue[tuple] | None = None
_WRITE_WORKER_TASK: asyncio.Task | None = None
_WRITE_QUEUE_MAX = int(os.getenv("ARIA_STATE_WRITE_QUEUE_MAX", "2000"))
# How many writes to batch into a single transaction before flushing.
# Higher = better throughput, but delays visibility of individual writes.
_WRITE_BATCH_SIZE = int(os.getenv("ARIA_STATE_WRITE_BATCH_SIZE", "50"))
# How long the worker waits for more writes before flushing a partial batch.
_WRITE_FLUSH_INTERVAL_S = float(os.getenv("ARIA_STATE_WRITE_FLUSH_INTERVAL_S", "0.1"))
# R-F2815 (Stage B of the R-F2813 HA re-architecture) — INTERRUPTIBLE WRITES.
# DEFAULT OFF: when off, the writer path below is byte-identical to pre-R-F2815
# (measure-first rollout). When on: (1) a write that exceeds _WRITE_EXECUTE_TIMEOUT_S
# is ABORTED via sqlite3.Connection.interrupt() — which is thread-safe and callable
# from THIS event-loop thread — freeing the wedged aiosqlite worker thread IN-PROCESS
# instead of waiting for the R-F2277 watchdog's os._exit cold-boot (the ~10-min
# outage); and (2) each drain TRANSACTION is bounded to _WRITE_BATCH_SIZE so one
# flush can't monopolise the single writer thread for thousands of serial round-trips
# (the wedge precursor). The R-F2277 watchdog stays the ultimate backstop for any
# wedge interrupt() cannot clear — this change LOWERS how often it must fire, never
# disarms it.
_INTERRUPTIBLE_WRITES = os.getenv("ARIA_INTERRUPTIBLE_WRITES", "0").strip().lower() in ("1", "true", "yes", "on")
# Per-write execute timeout (was a hardcoded 60.0 at the flush site, R-F2154). A
# write exceeding this on the single writer thread is a wedge signal; in interruptible
# mode it triggers interrupt()+reconnect rather than a silent 60s coroutine-cancel
# that leaves the C thread running.
_WRITE_EXECUTE_TIMEOUT_S = float(os.getenv("ARIA_STATE_WRITE_EXECUTE_TIMEOUT_S", "60"))
# R-F2137: runtime WAL maintenance. PRAGMA wal_autocheckpoint is PASSIVE — it
# transfers frames into the DB but NEVER shrinks the -wal file, so under
# sustained writes + reader pinning the file's high-water mark only grows at
# runtime (110 MB observed 2026-06-29, feeding 'database is locked' contention).
# R-F2116 only TRUNCATEs at boot. The write worker now also runs a periodic
# wal_checkpoint(TRUNCATE) — which resets the file whenever it catches a
# reader-free moment — gated on the -wal exceeding a threshold so the common
# small-WAL case pays nothing.
_WAL_CHECKPOINT_INTERVAL_S = float(os.getenv("ARIA_WAL_CHECKPOINT_INTERVAL_S", "60"))
_WAL_TRUNCATE_THRESHOLD_BYTES = int(
    os.getenv("ARIA_WAL_TRUNCATE_THRESHOLD_MB", "25")) * 1024 * 1024
_WAL_CHECKPOINT_TIMEOUT_S = float(os.getenv("ARIA_WAL_CHECKPOINT_TIMEOUT_S", "2.5"))
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


# ── R-F1541: bounded write queue ──────────────────────────────────────────
# A background worker drains _QUEUED_WRITES and writes to SQLite in batches.
# This replaces the timeout-and-drop model where every _upsert called
# _conn.execute() + _conn.commit() with a 30s timeout — when the worker
# thread was saturated, writes were silently dropped after the timeout.
#
# The queue is bounded (_WRITE_QUEUE_MAX). If full, the caller gets
# StateWriteError immediately (backpressure) instead of waiting 30s and
# losing data. The worker batches writes into transactions for efficiency.


async def _start_write_worker() -> None:
    """Initialise the write queue AND start a background worker that drains
    it continuously. Called from connect().

    R-F1973: replaces the R-F1541 design where the queue was drained
    SYNCHRONOUSLY on every read. That design meant a backlog of 1500+ writes
    (common during boot) caused every read to hang for 12+ seconds. Now the
    background worker drains the queue every 100ms, and reads NEVER touch the
    queue — they go straight to the database. Read-after-write consistency is
    guaranteed by the 100ms drain interval (writes are visible within 100ms).
    """
    global _QUEUED_WRITES, _WRITE_WORKER_TASK
    if _QUEUED_WRITES is not None:
        return  # already initialised
    _QUEUED_WRITES = asyncio.Queue(maxsize=_WRITE_QUEUE_MAX)

    async def _worker_loop():
        """Background loop: drain the write queue every 100ms, and periodically
        TRUNCATE the WAL so it can't grow unbounded at runtime (R-F2137)."""
        _ckpt_every = max(1, int(_WAL_CHECKPOINT_INTERVAL_S / 0.1))
        _ckpt_counter = 0
        while True:
            try:
                await asyncio.sleep(0.1)
                if _INTERRUPTIBLE_WRITES:
                    # R-F2815 — bound each TRANSACTION to _WRITE_BATCH_SIZE so one
                    # commit-cycle can't hold the single writer thread for thousands
                    # of serial round-trips, while still draining the whole backlog
                    # this tick. Cap the sub-flushes so a sustained storm can't loop
                    # forever here — the queue's own backpressure takes over past that.
                    for _ in range(64):
                        if await _flush_write_queue(max_items=_WRITE_BATCH_SIZE) < _WRITE_BATCH_SIZE:
                            break
                else:
                    await _flush_write_queue()
                _ckpt_counter += 1
                if _ckpt_counter >= _ckpt_every:
                    _ckpt_counter = 0
                    await _maybe_checkpoint_wal()
            except asyncio.CancelledError:
                await _flush_write_queue()
                break
            except Exception:
                pass

    _WRITE_WORKER_TASK = asyncio.ensure_future(_worker_loop())
    logger.info(
        "state_store: write queue + background worker started (max=%d, batch_size=%d)",
        _WRITE_QUEUE_MAX, _WRITE_BATCH_SIZE,
    )


async def _stop_write_worker() -> None:
    """Cancel the background write worker and flush remaining writes."""
    global _WRITE_WORKER_TASK
    if _WRITE_WORKER_TASK is not None:
        _WRITE_WORKER_TASK.cancel()
        try:
            await _WRITE_WORKER_TASK
        except asyncio.CancelledError:
            pass
        _WRITE_WORKER_TASK = None


# ─────────────────────────────────────────────────────────────────────────
# R-F2290 — hot/cold DB split, PHASE 0 (flag-gated, default OFF).
#
# The 907 MB single-writer ceiling: hot operational writes (cost, gaps,
# heartbeats) share one file + one writer thread with ~450k COLD, permanent,
# append-only rows (audit chains, verified facts/intel). One slow op stalls ALL
# writes. Phase 0 ships the router + a SECOND ("cold") SQLite file with its own
# connection + write queue + worker, and routes the K/V (set_key/set_json ↔
# get/get_json) write & read path by key. COMPOUND-op cold keys (lists/hashes,
# e.g. crucix:audit:by_entity via lpush) are NOT routed yet — Phase 0b (the ~30
# direct _conn.execute sites) + delete/scan.
#
# SAFETY: when ARIA_STATE_HOTCOLD_SPLIT is unset/0 (the DEFAULT + the live state)
# NOTHING changes — every accessor short-circuits to the existing hot globals,
# the cold file is never opened, and no cold worker starts. The split only
# activates when the flag is explicitly set (the Phase 2 cutover).
# ─────────────────────────────────────────────────────────────────────────

_HOTCOLD_SPLIT = os.getenv("ARIA_STATE_HOTCOLD_SPLIT", "").strip().lower() in (
    "1", "true", "yes", "on")
_COLD_DB_PATH = None          # Path to the cold DB file (set in _open_cold_store)
_cold_conn = None             # aiosqlite writer connection for the cold DB
_cold_read_conn = None        # aiosqlite read connection for the cold DB
_cold_queue: "asyncio.Queue[tuple] | None" = None
_COLD_WORKER_TASK: "asyncio.Task | None" = None
# R-F2563 — cold-store-open-failed flag (fail-loud, not silent-degrade). Post the
# R-F2504 reclaim the cold store is the ONLY home of verified_facts/audit/reasoning_library.
_cold_open_failed = False
# R-F2563 — boot VACUUM fires only when the hot DB carries at least this much free space
# (the R-F2504 reclaim left ~1GB uncompacted). Small enough to run once for the current
# bloat, large enough that it's not a routine-boot tax.
_VACUUM_MIN_FREE_MB = 300

# COLD (permanent, append-only, read-rarely) K/V key prefixes. ONLY simple-K/V
# prefixes here — list/hash prefixes (audit:by_entity …) route in Phase 0b.
_COLD_KEY_PREFIXES: tuple = (
    "crucix:audit:by_hash:",
    "aria:verified_facts:",
    # R-F2413: trailing colon so the singular fact keys
    # (crucix:verified_intel:fact:<id>, knowledge.py:1249) route cold WITHOUT
    # swallowing the churny plural K/V list crucix:verified_intel:facts
    # (verified_intel.py:1651, read by 5+ callers) — that stays HOT.
    "crucix:verified_intel:fact:",
    "crucix:aria:reasoning_library",
)


def _route_db(key: str) -> str:
    """R-F2290 — route a KEY to 'cold' (permanent append-only store) or 'hot'
    (operational store). Pure + module-level → unit-testable. Consulted ONLY
    when _HOTCOLD_SPLIT is on; otherwise every op uses the hot store."""
    if key:
        for _p in _COLD_KEY_PREFIXES:
            if key.startswith(_p):
                return "cold"
    return "hot"


def _writer_queue_for(key: str):
    """Write queue for `key`. Flag OFF → the hot queue, byte-identical to
    pre-R-F2290. Cold K/V keys → the cold queue when the split is open."""
    if not _HOTCOLD_SPLIT:
        return _QUEUED_WRITES
    if _cold_queue is not None and _route_db(key) == "cold":
        return _cold_queue
    return _QUEUED_WRITES


def _reader_conn_for(key: str):
    """Read connection for `key`. Flag OFF → the hot read pool, byte-identical.
    Cold K/V keys → the cold read connection when the split is open."""
    if not _HOTCOLD_SPLIT:
        return _get_read_conn()
    if _cold_read_conn is not None and _route_db(key) == "cold":
        return _cold_read_conn
    return _get_read_conn()


async def _flush_cold_queue() -> int:
    """R-F2290 — drain the cold write queue into the cold connection (mirror of
    _flush_write_queue). No-op when the split is off / cold queue absent."""
    queue = _cold_queue
    if queue is None or queue.empty() or _cold_conn is None:
        return 0
    flushed = 0
    batch: list[tuple] = []
    while not queue.empty():
        try:
            sql, params = queue.get_nowait()
            batch.append((sql, params))
            flushed += 1
        except asyncio.QueueEmpty:
            break
    if batch and _cold_conn is not None:
        try:
            for sql, params in batch:
                await asyncio.wait_for(_cold_conn.execute(sql, params), timeout=_WRITE_EXECUTE_TIMEOUT_S)
            await asyncio.wait_for(_cold_conn.commit(), timeout=_WRITE_EXECUTE_TIMEOUT_S)
        except asyncio.TimeoutError:
            logger.error("state_store[cold]: flush timed out (%d writes) — may be lost.", len(batch))
            # R-F2816: cold-writer wedge recovery is handled by the boot-settle-armed
            # R-F2277 watchdog (probe_liveness R-F2580 also SELECT-1s the cold conn),
            # not per-flush — see the hot path above.
        except Exception as e:
            logger.error("state_store[cold]: flush failed (%d writes): %s", len(batch), e)
    return flushed


async def _start_cold_write_worker() -> None:
    """R-F2290 — start the cold write queue + background worker (mirror of
    _start_write_worker). Called from _open_cold_store only when the split is on."""
    global _cold_queue, _COLD_WORKER_TASK
    if _cold_queue is not None:
        return
    _cold_queue = asyncio.Queue(maxsize=_WRITE_QUEUE_MAX)

    async def _cold_loop():
        while True:
            try:
                await asyncio.sleep(0.1)
                await _flush_cold_queue()
            except asyncio.CancelledError:
                await _flush_cold_queue()
                break
            except Exception:
                pass

    _COLD_WORKER_TASK = asyncio.ensure_future(_cold_loop())
    logger.info("state_store: R-F2290 cold-store write worker started")


async def _stop_cold_write_worker() -> None:
    global _COLD_WORKER_TASK
    if _COLD_WORKER_TASK is not None:
        _COLD_WORKER_TASK.cancel()
        try:
            await _COLD_WORKER_TASK
        except asyncio.CancelledError:
            pass
        _COLD_WORKER_TASK = None


async def _open_cold_store() -> None:
    """R-F2290 — open + configure the cold DB file (same `state` schema as hot)
    and its read connection, then start the cold write worker. Sibling of the hot
    connect() plumbing; only called when _HOTCOLD_SPLIT is on. The cold file lives
    beside the hot DB (`aria_knowledge_store.db`). On any failure the split
    silently degrades to hot-only (cold conns stay None → accessors fall back)."""
    global _cold_conn, _cold_read_conn, _COLD_DB_PATH
    if _DB_PATH is None:
        return
    import aiosqlite
    _ensure_aiosqlite_daemon_workers(aiosqlite)
    _COLD_DB_PATH = _DB_PATH.with_name("aria_knowledge_store.db")
    _cold_conn = await asyncio.wait_for(aiosqlite.connect(str(_COLD_DB_PATH)), timeout=30.0)
    await _cold_conn.execute("PRAGMA busy_timeout=120000")
    await _cold_conn.execute("PRAGMA journal_mode=WAL")
    await _cold_conn.execute("PRAGMA synchronous=NORMAL")
    await _cold_conn.execute("PRAGMA foreign_keys=OFF")
    await _cold_conn.execute(
        "CREATE TABLE IF NOT EXISTS state (key TEXT PRIMARY KEY, value TEXT NOT NULL, "
        "kind TEXT NOT NULL DEFAULT 'string', expires_at REAL)")
    await _cold_conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_state_expires ON state(expires_at) "
        "WHERE expires_at IS NOT NULL")
    await _cold_conn.commit()
    _cold_read_conn = await asyncio.wait_for(aiosqlite.connect(str(_COLD_DB_PATH)), timeout=30.0)
    await _configure_read_conn(_cold_read_conn)
    await _start_cold_write_worker()
    logger.info("state_store: R-F2290 cold store OPEN at %s", _COLD_DB_PATH)


async def _enqueue_write(sql: str, params: tuple, key: str | None = None) -> None:
    """Enqueue a write operation. Raises StateWriteError if the queue is full.
    
    This is the core of R-F1541: instead of calling _conn.execute() with a
    30s timeout (which silently drops writes when the worker thread is busy),
    we enqueue the write to a bounded queue. If the queue is full, the caller
    gets immediate backpressure (StateWriteError) instead of silent data loss.
    
    The caller MUST handle StateWriteError — either retry, fall back, or
    accept the loss. No write is ever silently dropped.

    R-F2290: `key` routes the write to the cold store when the hot/cold split is
    on and the key is a cold prefix; otherwise (and always when the flag is off)
    it goes to the hot queue.
    """
    queue = _writer_queue_for(key)
    if queue is None:
        raise StateWriteError("state_store: write queue not initialised")
    try:
        queue.put_nowait((sql, params))
    except asyncio.QueueFull:
        raise StateWriteError(
            f"state_store: write queue full ({_WRITE_QUEUE_MAX} items) — "
            f"caller must retry or accept data loss"
        )


def _interrupt_wedged_writer(conn, where: str, reconnect: bool = True) -> None:
    """R-F2815 (Stage B) / R-F2816 — abort a write wedged on the aiosqlite worker thread.

    Called ONLY from the R-F2277 liveness watchdog's escalation step (R-F2816) — which
    is boot-settle-armed and fires once per unhealthy streak — NOT from the per-flush
    timeout path (that fired during the legit-slow cold boot and was reverted).

    asyncio.wait_for cancels the awaiting COROUTINE but CANNOT stop the running C
    thread — the exact 2026-07-02 wedge property documented at the R-F2277 watchdog.
    sqlite3.Connection.interrupt() IS thread-safe (the sqlite3 docs guarantee it may
    be called from a different thread to abort in-flight queries), so calling it here
    from the event-loop thread aborts the statement stuck on aiosqlite's worker
    thread, freeing it so the store recovers IN-PROCESS. We then schedule the
    single-flight, PROBE-FIRST _reconnect: its SELECT-1 probe finds the freed conn
    healthy and SKIPS the reset, or (if interrupt could not clear it) replaces the
    conn — either way the next writes get a clean writer. Never raises; on any failure
    the R-F2277 watchdog remains the backstop (os._exit past its ceiling).
    """
    # aiosqlite 0.22.x stores the raw sqlite3.Connection at `_connection`. getattr
    # (no raise) so a version/shape change just skips the interrupt (falls back to the
    # pre-R-F2815 behaviour + watchdog), never crashes the flush loop.
    raw = getattr(conn, "_connection", None) if conn is not None else None
    if raw is None:
        return
    try:
        raw.interrupt()  # thread-safe: aborts the query running on the worker thread
        logger.error(
            "[R-F2815] interrupted a wedged state_store write (%s) — worker thread "
            "freed in-process; scheduling probe-first reconnect", where,
        )
    except Exception as e:
        logger.error("[R-F2815] interrupt() failed (%s): %s — watchdog remains the backstop", where, e)
        return
    # HOT writer: probe-first reconnect for a clean conn. COLD writer (reconnect=False):
    # interrupt frees the thread; if that didn't recover it, probe_liveness (which also
    # SELECT-1s the cold conn, R-F2580) detects the wedge and the watchdog's os._exit
    # reopens BOTH conns — so no cold-specific reconnect is needed here.
    if not reconnect:
        return
    try:
        asyncio.get_running_loop().create_task(_reconnect())
    except Exception:
        pass


async def _flush_write_queue(max_items: int | None = None) -> int:
    """Flush pending writes immediately. Returns the number flushed.
    Used by close(), reads, probe_liveness, and tests to ensure writes are durable.

    R-F1541: the flush is a no-op when the queue is empty (the common case), so the
    overhead is negligible in steady state.

    R-F2815: `max_items` bounds how many queued writes are drained (and committed as
    ONE transaction) in this call. Default None = drain ALL — every existing caller
    (close, reads, probe_liveness, the immediate-write ops) relies on a full drain, so
    None is byte-identical to the pre-R-F2815 behaviour. The periodic worker passes
    _WRITE_BATCH_SIZE (interruptible mode only) so a single commit-cycle can't hold the
    one writer thread for thousands of serial round-trips.
    """
    queue = _QUEUED_WRITES
    if queue is None or queue.empty():
        return 0
    flushed = 0
    batch: list[tuple] = []
    # Collection is await-free (get_nowait) → an atomic snapshot; max_items bounds it.
    while not queue.empty() and (max_items is None or len(batch) < max_items):
        try:
            sql, params = queue.get_nowait()
            batch.append((sql, params))
            flushed += 1
        except asyncio.QueueEmpty:
            break
    if batch and _conn is not None:
        try:
            # R-F2154: bounded flush — each execute has a generous timeout so a large
            # DB (verified facts, neural edges) has time to complete. 10s was too tight
            # for a ~790 MB DB. R-F2815: the value is now the _WRITE_EXECUTE_TIMEOUT_S
            # constant, and exceeding it is treated as a WEDGE signal below.
            for sql, params in batch:
                await asyncio.wait_for(_conn.execute(sql, params), timeout=_WRITE_EXECUTE_TIMEOUT_S)
            await asyncio.wait_for(_conn.commit(), timeout=_WRITE_EXECUTE_TIMEOUT_S)
        except asyncio.TimeoutError:
            logger.error(
                "state_store: flush timed out (%d writes) — DB may be "
                "bloated or under WAL recovery. Writes may be lost.",
                len(batch),
            )
            # R-F2816: wedge RECOVERY (interrupt + reconnect) lives in the boot-settle-
            # armed R-F2277 watchdog now — NOT here. The reverted R-F2815 flush-path
            # interrupt fired during the legit-slow ~10-min cold boot (writes exceed the
            # timeout while the heavy graphs load) → reconnect churn → the store never
            # settled → watchdog os._exit loop → boot FLAP. The watchdog arms only after
            # the boot-settle window and escalates once per unhealthy streak.
        except Exception as e:
            # R-F3363 — capture the lock state AT THE MOMENT OF FAILURE.
            #
            # This is the single error class holding Phase-A gate #3 open: one
            # `database is locked` restarts the 7-day clean clock, and live on
            # 2026-07-28 it had fired exactly once in 23h. Rare is what makes it
            # expensive — by the time anyone looks, the causing state is gone,
            # and the handler threw away the only moment it was observable.
            #
            # No cause is asserted here. `busy_timeout` is already 120s on both
            # connections and the hot/cold stores are separate files, so the
            # obvious explanations are already excluded; one unreproduced event
            # is not evidence for a mechanism, and naming one would be the
            # fabricated diagnosis §22 exists to prevent. get_lock_diagnostics()
            # (R-F1334) reports the RMW holder + acquire stack and is safe from
            # any thread — the blackout wedge dumper already relies on it. Now
            # the NEXT occurrence arrives with the holder attached.
            #
            # The probe is wrapped: instrumentation must never eat the signal it
            # exists to explain.
            try:
                _diag = f" | lock={str(get_lock_diagnostics())[:400]}"
            except Exception as _de:
                _diag = f" | lock diagnostics unavailable: {str(_de)[:80]}"
            logger.error(
                "state_store: flush failed (%d writes): %s%s", len(batch), e, _diag
            )
            _schedule_reconnect_if_dead(e)
    return flushed


async def _maybe_checkpoint_wal() -> None:
    """R-F2137: periodically TRUNCATE the -wal so it cannot grow unbounded at
    runtime. wal_autocheckpoint is PASSIVE (transfers frames but never resets
    the file), so under sustained writes + reader pinning the -wal high-water
    mark only grows (110 MB observed 2026-06-29). A checkpoint(TRUNCATE) resets
    the file whenever it catches a reader-free moment; if a reader pins it the
    call returns busy and we simply retry next interval — never fatal. Gated on
    a size threshold so the common small-WAL case skips the checkpoint IO. Fully
    guarded: a failure here must never break the write-drain loop."""
    if _conn is None or _DB_PATH is None:
        return
    try:
        _wal_file = _DB_PATH.with_name(_DB_PATH.name + "-wal")
        _before = _wal_file.stat().st_size if _wal_file.exists() else 0
        if _before < _WAL_TRUNCATE_THRESHOLD_BYTES:
            return  # small enough — let PASSIVE autocheckpoint handle it
        await asyncio.wait_for(
            _bounded_wal_checkpoint("runtime"),
            timeout=max(0.25, float(_WAL_CHECKPOINT_TIMEOUT_S)),
        )
        _after = _wal_file.stat().st_size if _wal_file.exists() else 0
        if _before - _after > 20 * 1024 * 1024:
            logger.info(
                "state_store: R-F2137 runtime WAL checkpoint reclaimed "
                "%.1f MB -> %.1f MB", _before / 1e6, _after / 1e6)
    except asyncio.TimeoutError:
        logger.warning(
            "state_store: R-F2378 runtime WAL checkpoint exceeded %.1fs — "
            "skipped to protect the write worker",
            _WAL_CHECKPOINT_TIMEOUT_S,
        )
    except Exception as e:
        logger.debug("state_store: R-F2137 runtime WAL checkpoint skipped: %s", e)


async def _bounded_wal_checkpoint(reason: str = "maintenance") -> None:
    """R-F2378 — run TRUNCATE checkpoint on a bounded maintenance connection.

    ``PRAGMA wal_checkpoint(TRUNCATE)`` can wait behind readers/writers. Running
    it on the main writer connection means ordinary state writes queue behind
    maintenance. Use a short-lived connection with a short busy_timeout and a
    total ``wait_for`` cap instead; failure just defers truncation to the next
    maintenance tick.
    """
    if _DB_PATH is None:
        return
    timeout_s = max(0.25, float(_WAL_CHECKPOINT_TIMEOUT_S))

    async def _run() -> None:
        import aiosqlite
        _ensure_aiosqlite_daemon_workers(aiosqlite)
        conn = await aiosqlite.connect(str(_DB_PATH))
        try:
            await conn.execute(f"PRAGMA busy_timeout={max(1, int(timeout_s * 1000))}")
            await conn.execute("PRAGMA journal_mode=WAL")
            await conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            await conn.commit()
        finally:
            await conn.close()

    await asyncio.wait_for(_run(), timeout=timeout_s)


def _reap_old_conns(*conns) -> None:
    """R-F2754 — close superseded aiosqlite connections so their worker threads
    terminate, reclaiming the leak that grew to 54 live connection threads.

    Fire-and-forget + bounded, NEVER awaited on the hot/self-heal path: a wedged
    connection's close() queues behind its stuck op and would hang, so we must not
    block reconnect/read-refresh on it. For a merely-slow (recovered) conn the
    close runs promptly and reclaims the thread; for a still-wedged one the queued
    close applies once the wedge clears — strictly better than never closing it.
    The task is tracked in a module set (strong ref) so it can't be GC'd before it
    runs, with a done-callback to drop it."""
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return  # no running loop (interpreter shutdown) — nothing to reap
    for _c in conns:
        if _c is None:
            continue

        async def _close(c=_c) -> None:
            try:
                await asyncio.wait_for(c.close(), timeout=30.0)
            except Exception:
                pass  # a wedged close must never surface or block

        t = loop.create_task(_close())
        _reap_tasks.add(t)
        t.add_done_callback(_reap_tasks.discard)


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
        _ensure_aiosqlite_daemon_workers(aiosqlite)
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
        # R-F2277: bound the reopen. The old code awaited aiosqlite.connect()
        # unbounded — if the wedged OLD connection's thread holds a lock, the
        # replacement's open (and its journal_mode=WAL replay) can block behind
        # it, so _reconnect never returns and _reconnect_in_progress stays True
        # forever → no further self-heal. connect() (boot) already bounds this
        # at 30s; mirror it here so a locked DB can't hang the self-heal path.
        conn = await asyncio.wait_for(aiosqlite.connect(str(_DB_PATH)), timeout=30.0)
        # R-F2131/R-F2132: set busy_timeout (120s) BEFORE journal_mode=WAL.
        # R-F2131: the old 5s value caused reconnect to fail under WAL-replay
        # contention, keeping the app on the in-memory fallback indefinitely.
        # R-F2132: journal_mode must not run first — a multi-GB WAL recovery
        # raises 'database is locked' before the 120s timeout can apply.
        await conn.execute("PRAGMA busy_timeout=120000")
        await conn.execute("PRAGMA journal_mode=WAL")
        await conn.execute("PRAGMA synchronous=NORMAL")
        await conn.execute("PRAGMA foreign_keys=OFF")
        await conn.commit()
        _conn = conn  # R-F1397: swap only once the replacement is ready
        _reap_old_conns(old)  # R-F2754: close the superseded conn (reclaim its thread)
        _op_timeout_counts["reconnect"] += 1
        logger.warning("[R-F1341] state_store connection reset (self-heal) #%d",
                       _op_timeout_counts["reconnect"])
        if old is not None:
            try:
                await asyncio.wait_for(old.close(), timeout=5.0)
            except Exception:
                pass  # the whole point is that it was wedged
    except Exception as e:
        # ── R-F3262: reap the replacement we opened but never installed ────
        #
        # The same gap R-F3251 closed in `_ensure_read_conn`, in its sibling.
        # The connect above succeeds and then four PRAGMAs run before `_conn`
        # is assigned — and the comments right there record that
        # `journal_mode=WAL` can raise "database is locked" during a multi-GB
        # WAL replay. When it does, this `except` ran and the connection was
        # neither installed nor closed: an orphaned worker thread, on the
        # SELF-HEAL path, which by definition runs when the store is already
        # wedged and can least afford another one.
        #
        # Live evidence: R-F3251 alone took the count from 56 to 20 against a
        # design of ~6, so a second source had to exist. This is it.
        #
        # `conn` may be unbound if the connect itself failed — reap only what
        # actually exists, and never touch `old`, which is still installed.
        _pending = locals().get("conn")
        if _pending is not None and _pending is not _conn:
            _reap_old_conns(_pending)
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


# ─────────────────────────────────────────────────────────────────────────
# R-F2277 — state-store liveness watchdog (escalating self-heal)
#
# The 2026-07-02 3.5h outage: the single aiosqlite WRITER thread wedged (one op
# blocked the thread — aiosqlite serialises all ops on one thread per connection,
# and asyncio.wait_for cancels the awaiting coroutine but CANNOT interrupt the
# running thread, so every later op queued behind it and timed out forever). The
# event-loop watchdog (main.py R-F1417) never fired because the LOOP stayed
# healthy — all state_store ops timed out at 5s and returned graceful defaults,
# so the loop heartbeat kept ticking while the DB limb was dead. The reconnect
# self-heal never fired either: it only triggers on _is_conn_dead() error strings
# ('closed'/'cannot operate'), never on a TimeoutError. So nothing escalated to
# the one action that recovers a lock-holding wedged thread — a process restart.
#
# This watchdog is that missing recovery actor. It runs PER-PROCESS (each process
# owns a connection that can wedge — NOT election-gated), round-trips the store on
# an interval, and on sustained unavailability escalates: first an in-process
# _reconnect (cheap, recovers a merely-slow/closed conn), then past a hard ceiling
# os._exit(1) so Fly cold-boots a fresh process + fresh connection. Mirrors the
# proven R-F1417 event-loop self-restart, but for the state_store limb.
# ─────────────────────────────────────────────────────────────────────────

_ss_wd_unhealthy_since: float | None = None  # monotonic ts of first failed probe (None = healthy)
_ss_wd_reconnect_fired: bool = False         # reconnect attempted for the current unhealthy streak


def _ss_env_true(name: str, default: bool = True) -> bool:
    """Env-truthy helper — default-on kill-switches read '0/false/no/off' as off."""
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() not in ("0", "false", "no", "off")


def _should_restart_for_wedge(
    unhealthy_for_s: float, armed: bool, enabled: bool, ceiling_s: float
) -> bool:
    """R-F2277 — decide whether the liveness watchdog should force os._exit(1)
    (so Fly cold-boots a fresh process). Pure + module-level so the dangerous
    exit it gates is unit-testable. True ONLY when self-restart is enabled, the
    watchdog is armed (past the boot-settle window — never fires during the
    ~10-min cold boot), and the store has been continuously unavailable past the
    hard ceiling (a genuine wedge, not a transient slow op). Mirrors
    main._should_force_restart."""
    try:
        return bool(enabled and armed and float(unhealthy_for_s) > float(ceiling_s))
    except (TypeError, ValueError):
        return False


async def probe_liveness(timeout_s: float = 3.0) -> bool:
    """R-F2277 — real round-trip that exercises BOTH the wedge-prone paths: a
    write drained through the single writer thread AND a read through the pool.
    Returns True iff the full write→flush→read completes within timeout_s. Any
    timeout/exception → False (the store is unavailable). Never raises."""
    key = "crucix:state_store:wd_heartbeat"
    val = str(_now())
    try:
        await asyncio.wait_for(set_key(key, val, ex=120), timeout=timeout_s)
        # Force the write through the writer thread NOW (don't wait for the
        # 100ms background drain) — this is the exact op that wedged 2026-07-02.
        await asyncio.wait_for(_flush_write_queue(), timeout=timeout_s)
        got = await asyncio.wait_for(get(key), timeout=timeout_s)
        if got != val:
            return False
        # R-F2580 — when the hot/cold split is active, ALSO probe the COLD writer conn.
        # The round-trip above exercises only the HOT conn (the heartbeat key routes hot),
        # so a wedged cold writer thread would silently blind verified_facts / audit /
        # reasoning_library with NO watchdog escalation. A lightweight, non-churning
        # SELECT 1 through the cold writer thread detects a wedge (a stuck thread can't
        # answer it); any timeout/error → the store is reported unavailable → the watchdog
        # escalates → its os._exit restart reopens BOTH conns. Gated on the split + an
        # actually-open cold conn (byte-identical when the split is off).
        if _HOTCOLD_SPLIT and _cold_conn is not None:
            # Consume + close the cursor (mirror the hot-conn reconnect probe) so no
            # statement lingers; any timeout/error here → the outer except → unavailable.
            _cur = await asyncio.wait_for(_cold_conn.execute("SELECT 1"), timeout=timeout_s)
            await asyncio.wait_for(_cur.fetchone(), timeout=timeout_s)
            await _cur.close()
        return True
    except Exception:
        return False


def _dump_wedge_forensics(unhealthy_for_s: float, base_dir: str | None = None) -> None:
    """R-F2279 — best-effort dump of ALL thread stacks right before the watchdog's
    os._exit, so the operation that wedged the aiosqlite connection thread is
    captured (the 2026-07-02 outage left no trace of its trigger). Writes to
    stderr (→ fly logs) AND a durable file under /data/wedge_stacks (the fly
    volume survives the cold-boot). Mirrors main.py's R-F704 wedge dump. Never
    raises — a forensic failure must not delay the recovery restart."""
    import os as _o
    import sys as _s
    import time as _t
    import faulthandler as _fh
    header = (
        f"=== [R-F2279] state_store WEDGE forensic dump — store unavailable "
        f"{unhealthy_for_s:.0f}s at "
        f"{_t.strftime('%Y-%m-%d %H:%M:%S UTC', _t.gmtime())} (pid {_o.getpid()}) "
        f"— ALL thread stacks below; the aiosqlite connection worker thread's "
        f"frame is the op that wedged the store ===\n"
    )
    # 1) stderr — always captured in the container/fly log stream.
    try:
        _s.stderr.write(header)
        _s.stderr.flush()
        _fh.dump_traceback(file=_s.stderr, all_threads=True)
        _s.stderr.flush()
    except Exception:
        pass
    # 2) durable file on the /data volume (survives the cold-boot for post-mortem).
    try:
        if base_dir is not None:
            base = base_dir
        elif _o.path.isdir("/data") and _o.access("/data", _o.W_OK):
            base = "/data/wedge_stacks"
        else:
            base = _o.path.join(_o.path.dirname(__file__), "..", "..", "data", "wedge_stacks")
        _o.makedirs(base, exist_ok=True)
        path = _o.path.join(base, f"ss_wedge_{_o.getpid()}_{int(_t.time())}.log")
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(header)
            _fh.dump_traceback(file=fh, all_threads=True)
            fh.write("=== end [R-F2279] dump ===\n")
        logger.critical("[R-F2279] state_store wedge forensic dump written -> %s", path)
    except Exception:
        pass


async def liveness_watchdog_loop() -> None:
    """R-F2277 — escalating self-heal for a wedged store. Probe every interval;
    on a sustained failure, reconnect once, then os._exit past the ceiling so
    Fly cold-boots. Runs per-process. Gated by ARIA_STATE_STORE_WATCHDOG_ENABLED
    (default on) and armed only after the boot-settle window."""
    global _ss_wd_unhealthy_since, _ss_wd_reconnect_fired
    import os as _os
    if not _ss_env_true("ARIA_STATE_STORE_WATCHDOG_ENABLED", default=True):
        logger.info("[R-F2277] state_store liveness watchdog DISABLED via env")
        return
    interval = float(_os.getenv("ARIA_SS_WATCHDOG_INTERVAL_S", "15"))
    reconnect_after = float(_os.getenv("ARIA_SS_WATCHDOG_RECONNECT_S", "45"))
    ceiling = float(_os.getenv("ARIA_SS_WATCHDOG_CEILING_S", "180"))
    settle = float(_os.getenv("ARIA_SS_WATCHDOG_SETTLE_S", "120"))
    self_restart = _ss_env_true("ARIA_SS_WATCHDOG_SELF_RESTART", default=True)
    # Boot-settle: cold boot legitimately makes the store slow/absent for
    # minutes (WAL replay, 907 MB hydrate) — never act during that window.
    await asyncio.sleep(settle)
    armed = True
    _ss_wd_unhealthy_since = None
    _ss_wd_reconnect_fired = False
    logger.info(
        "[R-F2277] state_store liveness watchdog armed "
        "(interval=%.0fs reconnect_after=%.0fs ceiling=%.0fs self_restart=%s)",
        interval, reconnect_after, ceiling, self_restart,
    )
    while True:
        try:
            await asyncio.sleep(interval)
            ok = await probe_liveness()
            now = time.monotonic()
            if ok:
                if _ss_wd_unhealthy_since is not None:
                    logger.warning(
                        "[R-F2277] state_store RECOVERED after %.0fs unavailable",
                        now - _ss_wd_unhealthy_since,
                    )
                _ss_wd_unhealthy_since = None
                _ss_wd_reconnect_fired = False
                continue
            # Unavailable this tick.
            if _ss_wd_unhealthy_since is None:
                _ss_wd_unhealthy_since = now
            unhealthy_for = now - _ss_wd_unhealthy_since
            logger.warning(
                "[R-F2277] state_store liveness probe FAILED — unavailable for %.0fs",
                unhealthy_for,
            )
            # Step 1 — in-process reconnect once per streak. Fire-and-forget so a
            # hung reopen can't block the watchdog's own escalation to os._exit.
            if unhealthy_for >= reconnect_after and not _ss_wd_reconnect_fired:
                _ss_wd_reconnect_fired = True
                logger.warning("[R-F2277] attempting in-process reconnect self-heal")
                try:
                    if _INTERRUPTIBLE_WRITES:
                        # R-F2816 (Stage B) — before the plain reconnect, INTERRUPT the
                        # wedged writer thread that a reconnect's SELECT-1 probe cannot
                        # itself clear: sqlite3.interrupt() aborts the stuck statement,
                        # freeing the thread, then _interrupt_wedged_writer schedules the
                        # same probe-first reconnect. This is the ONLY place the interrupt
                        # fires — inside the watchdog, which is boot-settle-armed (never
                        # during the ~10-min cold boot) and escalates once per unhealthy
                        # streak (no churn). It is the safe home for the in-process wedge
                        # recovery the reverted R-F2815 flush-path version got wrong.
                        _interrupt_wedged_writer(_conn, "watchdog")   # interrupt + reconnect
                        if _HOTCOLD_SPLIT and _cold_conn is not None:
                            _interrupt_wedged_writer(_cold_conn, "watchdog-cold", reconnect=False)
                    else:
                        asyncio.get_running_loop().create_task(_reconnect())
                    asyncio.get_running_loop().create_task(_ensure_read_conn())
                except Exception as _e:
                    logger.error("[R-F2277] reconnect self-heal scheduling failed: %s", _e)
            # Step 2 — escalate to a process restart (the only reliable recovery
            # for a lock-holding wedged thread).
            if _should_restart_for_wedge(unhealthy_for, armed, self_restart, ceiling):
                logger.critical(
                    "[R-F2277] state_store unavailable %.0fs > ceiling %.0fs — "
                    "forcing os._exit(1) so Fly cold-boots a fresh process "
                    "(self-recovery from a wedged aiosqlite connection)",
                    unhealthy_for, ceiling,
                )
                # R-F2279: BEFORE exiting, capture WHAT wedged the store. The op
                # that blocked the aiosqlite thread is not otherwise logged — the
                # 2026-07-02 wedge left zero trace of its trigger, so it could not
                # be root-caused. Dump ALL thread stacks (the aiosqlite connection
                # worker thread's frame reveals the exact stuck SQL) so the NEXT
                # wedge is diagnosable. Best-effort; never blocks the exit.
                _dump_wedge_forensics(unhealthy_for)
                # os._exit (not sys.exit): immediate, no atexit hooks that would
                # themselves try to touch the wedged store. WAL is crash-consistent
                # so an exit mid-write is safe; Fly's on-failure restart cold-boots.
                _os._exit(1)
        except asyncio.CancelledError:
            raise
        except Exception:
            # Watchdog must never die on an unexpected error.
            continue


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

async def maybe_boot_vacuum(db_path: str | None = None) -> dict:
    """R-F2563 — one-shot compaction of the hot DB when it carries >= _VACUUM_MIN_FREE_MB
    of free pages (the R-F2504 reclaim deleted 376k rows but left ~1GB uncompacted → slow
    WAL boots + writer pressure = the wedge class). Opens its OWN autocommit connection
    (VACUUM cannot run inside a txn) and MUST be called from the lifespan BEFORE
    state_store.connect() opens the main conn + read pool, so the DB is EXCLUSIVE and the
    work is OFF the 20s connect budget (main.py ARIA_STATE_CONNECT_BOOT_TIMEOUT_S) — a slow
    VACUUM here delays reclamation, it does NOT drop the box to the in-memory fallback.
    Self-gating (skips a compact DB), existence-guarded (never creates a stray DB when the
    backend isn't sqlite), timeout-bounded, failure-tolerant. Disable via
    ARIA_STATE_VACUUM_ON_BOOT=0."""
    if os.getenv("ARIA_STATE_VACUUM_ON_BOOT", "").strip().lower() in ("0", "false", "off", "no"):
        return {"vacuumed": False, "reason": "disabled"}
    if db_path is None:
        db_path = os.getenv("ARIA_STATE_DB_PATH", "/data/aria_state.db")
    db_path = str(db_path)
    # Existence guard: only compact a DB that is already there. A missing file means a
    # fresh boot (nothing to reclaim) or a non-sqlite backend — do NOT create a stray file.
    if not os.path.exists(db_path):
        return {"vacuumed": False, "reason": "no_db_file"}
    def _work(path: str) -> dict:
        # Stdlib sqlite3 in AUTOCOMMIT (isolation_level=None) is the clean way to VACUUM —
        # no lingering-cursor / implicit-txn issues. Runs in a worker thread so the boot
        # event loop stays free while VACUUM rewrites the file.
        import sqlite3
        con = sqlite3.connect(path, isolation_level=None, timeout=60.0)
        try:
            cur = con.execute("PRAGMA freelist_count"); row = cur.fetchone(); cur.close()
            free_pages = int(row[0]) if row else 0
            cur = con.execute("PRAGMA page_size"); row = cur.fetchone(); cur.close()
            page_sz = int(row[0]) if row else 4096
            free_mb = free_pages * page_sz / (1024 * 1024)
            if free_mb < _VACUUM_MIN_FREE_MB:
                return {"vacuumed": False, "reason": "below_threshold", "free_mb": round(free_mb, 1)}
            con.execute("PRAGMA busy_timeout=60000")
            t0 = time.monotonic()
            con.execute("VACUUM")
            return {"vacuumed": True, "reclaimed_mb": round(free_mb, 1),
                    "seconds": round(time.monotonic() - t0, 1)}
        finally:
            con.close()
    try:
        r = await asyncio.wait_for(asyncio.to_thread(_work, str(db_path)), timeout=300.0)
        if r.get("vacuumed"):
            logger.warning("state_store: R-F2563 boot VACUUM reclaimed ~%.0fMB free pages in %.1fs",
                           r.get("reclaimed_mb", 0), r.get("seconds", 0))
        return r
    except Exception as e:
        logger.error("state_store: R-F2563 boot VACUUM skipped (%s)", e)
        return {"vacuumed": False, "reason": f"vacuum_failed: {e}"}


async def connect(db_path: str | None = None) -> bool:
    """Open the SQLite file and create the schema if missing. Returns True
    on success. Caller (main.py) should fall back to in-memory dict if
    False (matches redis_store.connect contract).

    R-F1541: also starts the background write worker. The worker drains
    a bounded async queue and writes to SQLite in batches, replacing the
    timeout-and-drop model that caused cascading failures."""
    global _conn, _DB_PATH, _read_conn, _read_pool
    _reset_lock()
    try:
        import aiosqlite
        _ensure_aiosqlite_daemon_workers(aiosqlite)
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
        # R-F2151: wrap connect in a timeout so a locked/WAL-recovering DB
        # doesn't hang boot forever. 30s is generous for any SQLite open.
        # NOTE (R-F2563): the one-shot boot VACUUM is deliberately NOT run here.
        # connect() is wrapped by main.py in a 20s boot budget (ARIA_STATE_CONNECT_
        # BOOT_TIMEOUT_S); a slow VACUUM inside it would trip that cap → in-memory
        # fallback + an orphaned lock-holding thread. The VACUUM runs as a dedicated
        # EXCLUSIVE pre-connect step (state_store.maybe_boot_vacuum(), called from the
        # lifespan BEFORE this connect) so it is off the connect critical path.
        _conn = await asyncio.wait_for(
            aiosqlite.connect(str(_DB_PATH)), timeout=30.0)
        # R-F1449/R-F2242: open the READ-connection POOL (_READ_POOL_SIZE
        # connections, each its own aiosqlite thread) so concurrent reads run in
        # parallel instead of serializing on one thread. PRAGMAs go through the
        # shared _configure_read_conn helper (R-F2132: busy_timeout BEFORE
        # journal_mode — the boot-deadlock guard). _read_conn stays as pool[0] so
        # the existing close()/reconnect references keep working.
        _read_pool = []
        for _ in range(_READ_POOL_SIZE):
            _rc = await asyncio.wait_for(
                aiosqlite.connect(str(_DB_PATH)), timeout=30.0)
            await _configure_read_conn(_rc)
            _read_pool.append(_rc)
        _read_conn = _read_pool[0]
        # WAL mode → concurrent readers don't block writers. Crucial for
        # the chat path while autonomous tasks are also writing.
        # R-F2132: busy_timeout BEFORE journal_mode=WAL (see _read_conn above).
        await _conn.execute("PRAGMA busy_timeout=120000")
        await _conn.execute("PRAGMA journal_mode=WAL")
        await _conn.execute("PRAGMA synchronous=NORMAL")
        await _conn.execute("PRAGMA foreign_keys=OFF")
        # R-F2116: reclaim any WAL left by a previous UNCLEAN shutdown, at boot.
        # A SIGKILL/SIGTERM mid-write (crash-loop or contested-deploy) leaves the
        # -wal file un-checkpointed; once it grows across crashes sqlite's default
        # autocheckpoint can no longer truncate it. On 2026-06-28 aria_state.db-wal
        # reached 591 MB and EVERY boot's WAL handling exceeded fly's 1-min health
        # grace -> SIGTERM mid-recovery -> the next boot faced the same 591 MB WAL
        # -> an infinite crash loop that took aria-intel down for ~30 min. A
        # checkpoint(TRUNCATE) at connect is lossless (the frames are already in
        # the main DB) and fast (<1s even at 591 MB), so every boot starts from a
        # small, fast-opening DB and the loop can never seed itself again.
        try:
            await _conn.execute("PRAGMA wal_autocheckpoint=1000")
            await _read_conn.execute("PRAGMA wal_autocheckpoint=1000")
            _wal_file = _DB_PATH.with_name(_DB_PATH.name + "-wal")
            _wal_before = _wal_file.stat().st_size if _wal_file.exists() else 0
            await asyncio.wait_for(
                _bounded_wal_checkpoint("boot"),
                timeout=max(0.25, float(_WAL_CHECKPOINT_TIMEOUT_S)),
            )
            _wal_after = _wal_file.stat().st_size if _wal_file.exists() else 0
            if _wal_before > 50 * 1024 * 1024:
                logger.warning(
                    "state_store: R-F2116 reclaimed bloated WAL at boot: "
                    "%.1f MB -> %.1f MB (prevented crash-loop recurrence)",
                    _wal_before / 1e6, _wal_after / 1e6)
        except asyncio.TimeoutError:
            logger.warning(
                "state_store: R-F2378 boot WAL checkpoint exceeded %.1fs — "
                "continuing boot; runtime maintenance will retry",
                _WAL_CHECKPOINT_TIMEOUT_S,
            )
        except Exception as e:
            # Never let WAL housekeeping block boot — autocheckpoint is the fallback.
            logger.warning("state_store: R-F2116 boot WAL checkpoint skipped: %s", e)
        # R-F1541: _write_conn removed — all writes go through the bounded
        # async queue (_enqueue_write), which the background worker drains
        # through _conn. This avoids WAL lock contention between multiple
        # writer connections and eliminates the timeout-and-drop failure class.
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
        # R-F1541: start the background write worker
        try:
            await _start_write_worker()
        except Exception as e:
            logger.warning("state_store: write worker start failed: %s", e)
        # R-F2290: open the cold store ONLY when the split is explicitly enabled.
        # A cold-open failure must NOT break boot — degrade to hot-only.
        if _HOTCOLD_SPLIT:
            try:
                await _open_cold_store()
            except Exception as e:
                # R-F2563 — FAIL LOUD. Post the R-F2504 reclaim, the cold store is the ONLY
                # home of verified_facts / audit:by_hash / verified_intel:fact /
                # reasoning_library. A silent degrade-to-hot-only would BLIND the app to all
                # of that data — a visibility outage, not a soft degrade. Flag + escalate.
                global _cold_open_failed
                _cold_open_failed = True
                logger.critical("state_store: COLD STORE OPEN FAILED — verified_facts/audit/"
                                "reasoning_library (cold-only since the R-F2504 reclaim) are "
                                "NOW BLIND. Data-visibility outage, not a soft degrade: %s", e)
        logger.info("state_store: SQLite ready at %s (WAL mode)", _DB_PATH)
        return True
    except Exception as e:
        logger.error("state_store: connect failed: %s", e)
        _conn = None
        return False


async def close() -> None:
    """Close the database connection.
    
    R-F1541: flushes pending writes before closing."""
    global _conn, _read_conn, _read_pool, _cold_conn, _cold_read_conn, _cold_queue
    close_timeout_s = max(0.25, float(os.getenv("ARIA_STATE_CLOSE_TIMEOUT_S", "2.0") or "2.0"))

    async def _close_aiosqlite_conn(conn: Any, label: str) -> None:
        if conn is None:
            return
        try:
            await asyncio.wait_for(conn.close(), timeout=close_timeout_s)
        except (asyncio.TimeoutError, asyncio.CancelledError):
            logger.warning(
                "state_store: %s close exceeded %.1fs — queued stop sentinel and continuing teardown",
                label,
                close_timeout_s,
            )
            try:
                fut = conn.stop()
                if fut is not None:
                    await asyncio.wait_for(fut, timeout=0.25)
            except Exception:
                pass
        except Exception as exc:
            logger.warning("state_store: %s close failed: %s", label, exc)

    # Flush any pending writes first
    try:
        await _flush_write_queue()
    except Exception:
        pass
    # Stop the write worker
    try:
        await _stop_write_worker()
    except Exception:
        pass
    # R-F2290: flush + stop + close the cold store (no-op when split is off).
    try:
        await _flush_cold_queue()
        await _stop_cold_write_worker()
    except Exception:
        pass
    for _cc in (_cold_conn, _cold_read_conn):
        await _close_aiosqlite_conn(_cc, "cold connection")
    _cold_conn = None
    _cold_read_conn = None
    _cold_queue = None
    # R-F2242: close every pool member (was the single _read_conn).
    for _idx, _rc in enumerate(_read_pool or ([_read_conn] if _read_conn else [])):
        await _close_aiosqlite_conn(_rc, f"read connection {_idx}")
    _read_pool = []
    _read_conn = None
    # R-F1541: _write_conn removed — all writes go through the bounded queue
    await _close_aiosqlite_conn(_conn, "write connection")
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
    # R-F2413: sweep the cold store's expiring `state` rows when the split is on.
    # reasoning_library keys route cold WITH a TTL (ex=TTL_SECONDS,
    # reasoning_library.py:744) — without a cold sweeper they'd only lazy-expire
    # on read and accumulate. The cold DB has only the `state` table
    # (_open_cold_store), so there are no list/hash entries to sweep there.
    if _HOTCOLD_SPLIT and _cold_conn is not None:
        try:
            cur = await _cold_conn.execute(
                "DELETE FROM state WHERE expires_at IS NOT NULL AND expires_at <= ?",
                (_now(),),
            )
            await _cold_conn.commit()
            total += cur.rowcount or 0
        except Exception as e:
            logger.warning("state_store[cold]: sweep state failed: %s", e)
    if total > 0:
        logger.debug("state_store: swept %d expired rows", total)
    return total


# ─────────────────────────────────────────────────────────────────────────
# R-F2415 — hot→cold PHASE 1 backfill (COPY ONLY; flag-independent; reversible)
#
# Copies every cold-prefix row from the HOT db to the COLD db (idempotent upsert),
# paged by rowid, resumable via a checkpoint key, rate-limited by a sleep between
# pages so it can NEVER saturate the single writer. The hot rows are NEVER deleted
# (Phase 1 is fully reversible — reclaim is Phase 3). Runs IN-APP (R-F2277: a
# separate process cannot write the live state_store) and is driven by an
# operator-gated admin endpoint.
#
# CRITICAL for "live traffic unaffected": the page READ uses the hot READ pool
# (its own threads), the cold WRITE uses the cold conn's own thread, and only the
# once-per-page checkpoint touches the hot write queue. The hot WRITER thread —
# the wedge-prone one that serialises all live writes — is never used by the copy
# loop. Combined with the inter-page sleep, live reads/writes see the backfill only
# as a trickle of checkpoint writes. The cold store is opened EVEN WHEN
# ARIA_STATE_HOTCOLD_SPLIT is OFF; that is safe because every live accessor
# short-circuits on `not _HOTCOLD_SPLIT`, so nothing routes to the cold conn — the
# backfill just gets a destination handle. The flip stays operator-gated.
# ─────────────────────────────────────────────────────────────────────────

_BACKFILL_CURSOR_KEY = "crucix:state_store:hotcold_backfill:cursor"
_backfill_state: dict = {
    "running": False, "done": False, "copied": 0, "copied_this_run": 0,
    "scanned": 0, "last_rowid": 0, "started_at": None, "updated_at": None,
    "error": None,
}
_backfill_lock: asyncio.Lock | None = None


def _get_backfill_lock() -> asyncio.Lock:
    global _backfill_lock
    if _backfill_lock is None:
        _backfill_lock = asyncio.Lock()
    return _backfill_lock


async def ensure_cold_open() -> bool:
    """R-F2415 — open the cold store so the backfill has a destination, EVEN WHEN
    _HOTCOLD_SPLIT is OFF. Safe: with the flag off no live read/write routes to
    cold (all accessors short-circuit on `not _HOTCOLD_SPLIT`), so opening the
    cold conn cannot affect live traffic. Idempotent. Returns True iff the cold
    write conn is available afterwards."""
    if _cold_conn is not None:
        return True
    try:
        await _open_cold_store()
    except Exception as e:
        logger.error("state_store: R-F2415 ensure_cold_open failed: %s", e)
    return _cold_conn is not None


async def backfill_cold(*, page_size: int = 500, sleep_s: float = 0.2,
                        max_pages: int | None = None, reset: bool = False) -> dict:
    """R-F2415 — copy cold-prefix rows HOT→COLD. Idempotent (upsert on key,
    keyed by rowid cursor so a completed run re-runs as a no-op), resumable
    (checkpoint in _BACKFILL_CURSOR_KEY survives restart), rate-limited
    (sleep_s between pages). COPY ONLY — hot is never modified. Single-flight.

    page_size: rows scanned per page. sleep_s: pause between pages (writer
    breathing room). max_pages: stop after N pages (used by tests to simulate an
    interruption). reset: restart the cursor at 0 (full re-copy).
    """
    lock = _get_backfill_lock()
    if lock.locked():
        return {**_backfill_state, "note": "already running"}
    async with lock:
        if _conn is None:
            return {**_backfill_state, "error": "hot connection unavailable", "running": False}
        if not await ensure_cold_open():
            return {**_backfill_state, "error": "cold store unavailable", "running": False}

        if reset:
            cursor, copied = 0, 0
        else:
            # Bypass the R-F2156 get()-cache: the cursor changes as we page, and a
            # stale cached value (e.g. the None cached when it was absent at the
            # start of a prior run) would silently restart the copy from rowid 0.
            _error_log_cache.pop(_BACKFILL_CURSOR_KEY, None)
            ck = await get_json(_BACKFILL_CURSOR_KEY)
            cursor = int(ck.get("last_rowid", 0)) if isinstance(ck, dict) else 0
            copied = int(ck.get("copied", 0)) if isinstance(ck, dict) else 0
        copied_this_run = 0
        scanned = 0
        pages = 0
        _backfill_state.update(running=True, done=False, error=None,
                               started_at=_now(), updated_at=_now(),
                               last_rowid=cursor, copied=copied,
                               copied_this_run=0, scanned=0)
        try:
            while True:
                # Read a page from HOT via the READ pool (own threads) — the hot
                # WRITER thread is never touched, so live writes are unaffected.
                rconn = _get_read_conn()
                if rconn is None:
                    _backfill_state["error"] = "hot read conn unavailable"
                    break
                cur = await asyncio.wait_for(rconn.execute(
                    "SELECT rowid, key, value, kind, expires_at FROM state "
                    "WHERE rowid > ? ORDER BY rowid LIMIT ?",
                    (cursor, page_size)), timeout=30.0)
                rows = await cur.fetchall()
                await cur.close()
                if not rows:
                    _backfill_state["done"] = True
                    break
                cold_batch = [(k, v, kind, exp)
                              for (_rid, k, v, kind, exp) in rows
                              if _route_db(k) == "cold"]
                for (k, v, kind, exp) in cold_batch:
                    # Idempotent upsert into the COLD conn's own thread.
                    await asyncio.wait_for(_cold_conn.execute(
                        "INSERT INTO state(key, value, kind, expires_at) "
                        "VALUES(?, ?, ?, ?) "
                        "ON CONFLICT(key) DO UPDATE SET value=excluded.value, "
                        "kind=excluded.kind, expires_at=excluded.expires_at",
                        (k, v, kind, exp)), timeout=30.0)
                if cold_batch:
                    await asyncio.wait_for(_cold_conn.commit(), timeout=30.0)
                copied += len(cold_batch)
                copied_this_run += len(cold_batch)
                scanned += len(rows)
                cursor = rows[-1][0]  # highest rowid this page
                pages += 1
                # Checkpoint once per page (hot key; rate-limited by sleep_s →
                # not a hot-path RMW). Survives restart → resumable.
                await set_json(_BACKFILL_CURSOR_KEY, {
                    "last_rowid": cursor, "copied": copied, "scanned": scanned,
                    "done": False, "updated_at": _now()})
                _backfill_state.update(last_rowid=cursor, copied=copied,
                                       copied_this_run=copied_this_run,
                                       scanned=scanned, updated_at=_now())
                if max_pages is not None and pages >= max_pages:
                    break
                await asyncio.sleep(sleep_s)  # rate-limit — writer breathing room
            await set_json(_BACKFILL_CURSOR_KEY, {
                "last_rowid": cursor, "copied": copied, "scanned": scanned,
                "done": bool(_backfill_state.get("done")), "updated_at": _now()})
        except Exception as e:
            _backfill_state["error"] = str(e)[:200]
            logger.error("state_store: R-F2415 backfill failed: %s", e)
        finally:
            _backfill_state["running"] = False
            _backfill_state["updated_at"] = _now()
        return dict(_backfill_state)


async def backfill_status() -> dict:
    """R-F2415 — current backfill progress (in-memory live state merged with the
    persisted checkpoint so it is meaningful even right after a restart)."""
    out = dict(_backfill_state)
    try:
        _error_log_cache.pop(_BACKFILL_CURSOR_KEY, None)  # fresh checkpoint read (see backfill_cold)
        ck = await get_json(_BACKFILL_CURSOR_KEY)
        if isinstance(ck, dict):
            out["checkpoint"] = ck
    except Exception:
        pass
    out["cold_open"] = _cold_conn is not None
    out["hotcold_split_live"] = _HOTCOLD_SPLIT
    return out


async def reconcile_cold(sample_n: int = 50) -> dict:
    """R-F2415 — go/no-go gate before any flip: for EACH cold prefix, COUNT(*) of
    cold-route keys in HOT vs COUNT(*) in COLD, plus an N-random-key read-equal
    spot-check across both files. ok=True iff every prefix count matches AND no
    spot-check value mismatch. Read-only (uses the read pools); never mutates."""
    if _conn is None:
        return {"ok": False, "error": "hot connection unavailable"}
    if not await ensure_cold_open():
        return {"ok": False, "error": "cold store unavailable"}
    hot = _get_read_conn()
    cold = _cold_read_conn if _cold_read_conn is not None else _cold_conn
    prefixes: dict = {}
    all_match = True
    for p in _COLD_KEY_PREFIXES:
        try:
            c1 = await hot.execute("SELECT COUNT(*) FROM state WHERE key GLOB ?", (p + "*",))
            hot_n = (await c1.fetchone())[0]
            await c1.close()
            c2 = await cold.execute("SELECT COUNT(*) FROM state WHERE key GLOB ?", (p + "*",))
            cold_n = (await c2.fetchone())[0]
            await c2.close()
        except Exception as e:
            prefixes[p] = {"error": str(e)[:120]}
            all_match = False
            continue
        match = (cold_n == hot_n)
        prefixes[p] = {"hot": hot_n, "cold": cold_n, "match": match,
                       "missing_in_cold": max(0, hot_n - cold_n)}
        all_match = all_match and match

    # Read-equal spot-check: sample random cold-route keys from HOT, compare COLD.
    mismatches: list[str] = []
    checked = 0
    try:
        c = await hot.execute(  # nosec B608 - dynamic clause is generated placeholders for fixed cold-prefix values.
            "SELECT key, value FROM state WHERE (" +  # nosec B608 - dynamic clause is generated placeholders for fixed prefixes.
            " OR ".join("key GLOB ?" for _ in _COLD_KEY_PREFIXES) +
            ") ORDER BY RANDOM() LIMIT ?",
            (*[p + "*" for p in _COLD_KEY_PREFIXES], int(sample_n)))
        samples = await c.fetchall()
        await c.close()
        for (k, v) in samples:
            _ccur = await cold.execute("SELECT value FROM state WHERE key = ?", (k,))
            cr = await _ccur.fetchone()
            await _ccur.close()
            checked += 1
            if cr is None or cr[0] != v:
                mismatches.append(k)
    except Exception as e:
        return {"ok": False, "prefixes": prefixes,
                "error": f"spot-check failed: {str(e)[:120]}"}

    ok = all_match and not mismatches
    return {"ok": ok, "prefixes": prefixes,
            "spot_check": {"checked": checked, "mismatches": mismatches[:20],
                           "mismatch_count": len(mismatches)}}


_reclaim_running = False
_reclaim_state: dict = {"running": False, "phase": None, "deleted": 0, "scanned": 0,
                        "would_delete": 0, "vacuumed": False, "done": False,
                        "dry_run": None, "error": None, "updated_at": None}


async def reclaim_status() -> dict:
    """R-F2504 — live reclaim progress (fire-and-forget; poll this)."""
    return dict(_reclaim_state)


async def reclaim_hot(*, batch: int = 1000, sleep_s: float = 0.05,
                      do_vacuum: bool = True, dry_run: bool = False) -> dict:
    """R-F2504 Phase 3 — after backfill_cold + a GREEN reconcile_cold, DELETE the
    migrated cold-prefix rows from the HOT db and VACUUM to physically shrink the file
    (the 1GB → tens-of-MB payoff → fast hot writes). Safety, layered:
      1. refuses unless the split flag is ON (so live routing is correct);
      2. per batch, deletes a hot key ONLY if it is CONFIRMED present in COLD — never
         loses data even if backfill is partial (not-in-cold keys are skipped);
      3. rowid cursor → a skipped/deleted key is never re-scanned (no loop);
      4. VACUUM is ATOMIC (interrupt/failure leaves the db unchanged).
    Runs IN-APP on the hot WRITER (a separate process can't write the live store,
    R-F2277). dry_run counts what WOULD delete without mutating. Single-flight."""
    global _reclaim_running, _reclaim_state
    if _reclaim_running:
        return {"error": "reclaim already running"}
    if not _HOTCOLD_SPLIT:
        return {"error": "split flag OFF — refusing (live routing would be wrong)"}
    if _conn is None or not await ensure_cold_open():
        return {"error": "hot/cold connection unavailable"}
    _reclaim_running = True
    _reclaim_state = {"running": True, "phase": "delete", "deleted": 0, "scanned": 0,
                      "would_delete": 0, "vacuumed": False, "done": False,
                      "dry_run": dry_run, "error": None, "updated_at": _now()}
    deleted = would = scanned = 0
    cursor = 0
    _batches = 0
    _clauses = " OR ".join("key GLOB ?" for _ in _COLD_KEY_PREFIXES)
    _globs = [p + "*" for p in _COLD_KEY_PREFIXES]
    try:
        while True:
            rconn = _get_read_conn()
            if rconn is None:
                break
            cur = await asyncio.wait_for(rconn.execute(
                "SELECT rowid, key FROM state WHERE rowid > ? AND (" + _clauses +  # nosec B608 - fixed cold-prefix placeholders
                ") ORDER BY rowid LIMIT ?",
                (cursor, *_globs, int(batch))), timeout=30.0)
            rows = await cur.fetchall()
            await cur.close()
            if not rows:
                break
            cursor = rows[-1][0]
            scanned += len(rows)
            keys = [k for (_r, k) in rows]
            _qm = ",".join("?" for _ in keys)
            ccur = await asyncio.wait_for((_cold_read_conn or _cold_conn).execute(
                "SELECT key FROM state WHERE key IN (" + _qm + ")", keys), timeout=30.0)  # nosec B608 - generated placeholders
            in_cold = {r[0] for r in await ccur.fetchall()}
            await ccur.close()
            safe = [k for k in keys if k in in_cold]
            would += len(safe)
            if safe and not dry_run:
                _dq = ",".join("?" for _ in safe)
                # R-F2504 — RETRY transient contention (SQLITE_BUSY 'database is locked',
                # wait_for timeout) instead of aborting: a large delete on a LIVE db WILL
                # hit momentary locks against the read-pool scans + residual writes. Six
                # attempts with backoff drains the whole 376k without giving up (observed
                # live: prior runs aborted at ~75k on timeout / ~51k on 'database is locked').
                for _att in range(6):
                    try:
                        await asyncio.wait_for(_conn.execute(
                            "DELETE FROM state WHERE key IN (" + _dq + ")", safe), timeout=90.0)  # nosec B608 - generated placeholders
                        await asyncio.wait_for(_conn.commit(), timeout=90.0)
                        deleted += len(safe)
                        break
                    except Exception as _de:
                        if _att == 5:
                            raise
                        logger.debug("state_store: R-F2504 delete retry %d (%s)", _att, str(_de)[:60])
                        await asyncio.sleep(1.5 * (_att + 1))
            _reclaim_state.update(deleted=deleted, scanned=scanned, would_delete=would,
                                  updated_at=_now())
            # R-F2504 — checkpoint the WAL periodically so accumulating deletes don't bloat
            # the -wal file → slow commits. PASSIVE (not TRUNCATE): it checkpoints what it
            # can WITHOUT an exclusive lock, so it never fights the read-pool scans (TRUNCATE
            # caused 'database is locked'). Best-effort; keeps the WAL bounded/reusable.
            _batches += 1
            if _batches % 20 == 0:
                try:
                    await asyncio.wait_for(_conn.execute("PRAGMA wal_checkpoint(PASSIVE)"),
                                           timeout=60.0)
                except Exception as _ck:
                    logger.debug("state_store: R-F2504 mid-delete checkpoint skipped: %s", _ck)
            await asyncio.sleep(sleep_s)
        vacuumed = False
        vac_err = None
        if do_vacuum and not dry_run and deleted > 0:
            _reclaim_state.update(phase="vacuum", updated_at=_now())
            # Flush the WAL first (PASSIVE) so VACUUM has less to fight.
            try:
                await asyncio.wait_for(_conn.execute("PRAGMA wal_checkpoint(PASSIVE)"), timeout=60.0)
            except Exception:
                pass
            # VACUUM needs exclusivity vs the open read pool → may hit 'database is
            # locked'. Retry a few times; if it still can't get the lock, that's OK —
            # the DELETE already reclaimed the WORKING SET (freed pages reused, smaller
            # B-tree → faster writes). VACUUM only reclaims DISK, and is atomic (fails safe).
            for _vatt in range(4):
                try:
                    await asyncio.wait_for(_conn.execute("VACUUM"), timeout=900.0)
                    await _conn.commit()
                    vacuumed = True
                    break
                except Exception as e:
                    vac_err = str(e)[:200]
                    if _vatt == 3:
                        logger.warning("state_store: R-F2504 VACUUM could not get exclusive "
                                       "lock (db unchanged — atomic); deletes still applied: %s", e)
                        break
                    await asyncio.sleep(3.0 * (_vatt + 1))
        _reclaim_state.update(phase="done", done=True, deleted=deleted, scanned=scanned,
                              would_delete=would, vacuumed=vacuumed, updated_at=_now())
        return {"deleted": deleted, "would_delete": would, "scanned": scanned,
                "dry_run": dry_run, "vacuumed": vacuumed, "vacuum_error": vac_err}
    except Exception as e:
        logger.error("state_store: R-F2504 reclaim failed: %s", e)
        _reclaim_state.update(error=str(e)[:200], done=True, updated_at=_now())
        return {"error": str(e)[:200], "deleted": deleted, "scanned": scanned}
    finally:
        _reclaim_running = False
        _reclaim_state["running"] = False


# ─────────────────────────────────────────────────────────────────────────
# Internal helpers
# ─────────────────────────────────────────────────────────────────────────


async def _configure_read_conn(conn) -> None:
    """R-F2242/R-F2132 — the ONE place read-connection PRAGMAs are set, so every
    pool member (and the reconnect path) gets busy_timeout BEFORE journal_mode.

    Setting busy_timeout FIRST is the boot-deadlock guard (R-F2132): journal_mode
    can trigger a WAL recovery that needs a database lock, and Python sqlite3's
    ~5s default would raise 'database is locked' on a bloated WAL before boot
    completes (the 2026-06-29 outage). NEVER hand-write these PRAGMAs at a call
    site — always go through this helper so a new connection can't reintroduce
    the deadlock class.
    """
    await conn.execute("PRAGMA busy_timeout=120000")
    await conn.execute("PRAGMA journal_mode=WAL")
    await conn.execute("PRAGMA synchronous=NORMAL")
    await conn.execute("PRAGMA foreign_keys=OFF")
    await conn.execute("PRAGMA wal_autocheckpoint=1000")
    await conn.commit()


def _get_read_conn() -> Any:
    """R-F1449/R-F2242: return a read connection from the pool (round-robin).

    Read connections are NEVER touched by _reconnect(), so a write-side reset
    cannot kill concurrent reads. Under WAL mode, reads see a consistent snapshot
    without blocking the writer. R-F2242: round-robins over _read_pool so a burst
    of concurrent reads spreads across N connection-threads instead of serializing
    on one. Falls back to _read_conn / _conn during early boot before the pool is
    built (graceful degradation).
    """
    global _read_pool_rr
    if _read_pool:
        _read_pool_rr = (_read_pool_rr + 1) % len(_read_pool)
        return _read_pool[_read_pool_rr]
    return _read_conn if _read_conn is not None else _conn



async def _ensure_read_conn() -> None:
    """R-F1449: ensure the dedicated read connection is open.

    Called by _row() retry-once path when the read connection was closed
    by a write-side reset. Opens a new read connection without touching
    _conn or _reconnect().
    """
    global _read_conn, _read_pool
    if _DB_PATH is None:
        return
    try:
        import aiosqlite
        _ensure_aiosqlite_daemon_workers(aiosqlite)
        # R-F2242: rebuild the whole read pool (a write-side reset / 'closed
        # database' typically kills all read connections together). PRAGMAs via
        # the shared helper (R-F2132 boot-deadlock guard).
        _old_pool = list(_read_pool)  # R-F2754: capture BEFORE swap to reap after
        new_pool = []
        for _ in range(_READ_POOL_SIZE):
            _rc = await aiosqlite.connect(str(_DB_PATH))
            # R-F3251 — TRACK IT BEFORE CONFIGURING. `_configure_read_conn` runs
            # PRAGMAs, so it can raise on a stressed store; appending afterwards
            # meant a connection that failed to configure was already open and
            # already referenced by nothing.
            new_pool.append(_rc)
            await _configure_read_conn(_rc)
        _read_pool = new_pool
        _read_conn = new_pool[0]
        # R-F2754: close the superseded read connections so their worker threads
        # terminate (this rebuild leaked _READ_POOL_SIZE threads on every call —
        # the dominant contributor to the 54-thread oversubscription).
        _reap_old_conns(*_old_pool)
    except Exception as e:
        # ── R-F3251: reap what we built before we failed ──────────────────
        #
        # R-F2754 fixed the SUCCESS path and left this one. On failure the
        # partially-built pool was never assigned to `_read_pool` and never
        # closed, so its worker threads ran on unreferenced — up to
        # _READ_POOL_SIZE per failed rebuild.
        #
        # And this function is called BECAUSE the store is struggling, so the
        # failure path is the HOT one exactly when the box can least afford
        # more threads. Measured on the live box: 56 live connection worker
        # threads (peak 140 in an earlier capture) against a design of ~6, with
        # the main thread parked in a bare `asyncio.runners.run` — the loop was
        # never blocked by a coroutine; it was starved by thread contention.
        #
        # The old pool is deliberately NOT reaped here: the swap did not happen,
        # so `_read_pool` still points at it and it is still in use.
        _reap_old_conns(*new_pool)
        logger.warning("[R-F1449/R-F2242] _ensure_read_conn failed: %s", e)
async def _flush_pending_for_read(key: str) -> None:
    """Make this key's pending write visible to a POOL read. Never raises.

    R-F3679 — extracted from ``_row`` so ``get_strict`` shares it. The strict
    reader used to execute on ``_conn``, the write connection, which saw queued
    writes implicitly; moving it to the read pool would otherwise have broken
    read-after-write for all 30 strict callers (a ``set_json`` immediately
    followed by ``get_json_strict`` would read None — "genuinely absent" — which
    is the precise lie the strict readers exist to prevent). Caught in Pass 1
    verification by a test that wrote a key and read it straight back.

    R-F1973: reads NEVER flush the queue in production. The background worker
    drains it every 100ms, so writes are visible within 100ms. Previously
    (R-F1541) every read drained it synchronously, causing 12+ second hangs when
    the queue backlogged to 1500+ items during boot.

    For SMALL queues (<=10 items) we still flush synchronously to guarantee
    read-after-write in the common case (single writes, tests). Large backlogs
    are left to the background worker.
    """
    try:
        # R-F2290: flush the queue this key actually routes to (cold or hot), so
        # read-after-write holds for cold keys too. Flag OFF → the hot queue.
        _routed_cold = _HOTCOLD_SPLIT and _cold_queue is not None and _route_db(key) == "cold"
        queue = _cold_queue if _routed_cold else _QUEUED_WRITES
        if queue is not None and not queue.empty() and queue.qsize() <= 10:
            # R-F2154: bounded flush — a bloated DB or contested WAL can
            # make _conn.execute() block past busy_timeout; cap it so
            # a slow flush never hangs a read (and thus boot) indefinitely.
            await asyncio.wait_for(
                _flush_cold_queue() if _routed_cold else _flush_write_queue(),
                timeout=_READ_FLUSH_BUDGET_S)  # R-F2477: never let the flush eat the read budget
    except (asyncio.TimeoutError, Exception):
        pass


async def _row(key: str, expected_kind: str | None = None) -> tuple[str, str, float | None] | None:
    """Fetch (value, kind, expires_at) for a key. Returns None if missing
    or expired. If expected_kind is given and the kind mismatches, returns
    None (treat as missing — Redis raises WRONGTYPE; we degrade gracefully).

    R-F1541: flushes pending writes before reading so callers that do
    set() → get() see their own writes. The flush is a no-op when the
    queue is empty (the common case), so the read-path overhead is
    negligible in steady state.

    R-F1449: uses _get_read_conn() (separate read connection) so a write-side
    _reconnect() cannot kill concurrent reads. Retries ONCE on 'closed
    database' by reopening the read connection and re-executing.
    """
    # R-F1973: reads NEVER flush the write queue in production. The background
    # worker drains it every 100ms, so writes are visible within 100ms.
    # Previously (R-F1541) every read drained the queue synchronously, causing
    # 12+ second hangs when the queue backlogged to 1500+ items during boot.
    #
    # For SMALL queues (<=10 items) we still flush synchronously to guarantee
    # read-after-write consistency in the common case (single writes, tests).
    # Large backlogs are handled by the background worker.
    await _flush_pending_for_read(key)
    conn = _reader_conn_for(key)  # R-F2290: cold keys read from the cold conn
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
                            await asyncio.wait_for(conn.execute("DELETE FROM state WHERE key = ?", (key,)), timeout=5.0)
                            await asyncio.wait_for(conn.commit(), timeout=5.0)
                        except (asyncio.TimeoutError, Exception):
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
        # R-F2435 — cutover-safety: point-reads must be SYMMETRIC with the
        # scan_keys/scan_json hot+cold UNION (R-F2413). A cold-prefix key not yet
        # backfilled to the cold file — written after the backfill snapshot, or
        # backfill incomplete — still lives in HOT during migration; without this
        # fallback get()/get_json returns None for it at cutover even though scan
        # finds it, so verified_facts / reasoning_library / audit entries would
        # appear to VANISH at the flip. When the split is on and the COLD read
        # just missed, retry the HOT conn. Flag OFF → _cold_read_conn is None →
        # this block is skipped (byte-identical, dormant until flip).
        if _HOTCOLD_SPLIT and _cold_read_conn is not None and _route_db(key) == "cold":
            _hot = _get_read_conn()
            if _hot is not None and _hot is not conn:
                try:
                    cur = await _hot.execute(
                        "SELECT value, kind, expires_at FROM state WHERE key = ?",
                        (key,),
                    )
                    row = await cur.fetchone()
                    await cur.close()
                except Exception as e:
                    logger.warning("state_store: R-F2435 hot-fallback SELECT %s failed: %s", key, e)
                    row = None
        if not row:
            return None
    value, kind, expires_at = row
    if _expired(expires_at):
        # Lazy expiry — drop the row on read. No python-level lock needed:
        # aiosqlite serialises through a single worker thread, and DELETE
        # is idempotent so a parallel sweep_expired() can't double-fault.
        # R-F2154: bounded — 5s timeout so a contested WAL never hangs.
        try:
            await asyncio.wait_for(_conn.execute("DELETE FROM state WHERE key = ?", (key,)), timeout=5.0)
            await asyncio.wait_for(_conn.commit(), timeout=5.0)
        except (asyncio.TimeoutError, Exception):
            pass
        return None
    if expected_kind and kind != expected_kind:
        logger.debug("state_store: kind mismatch on %s — wanted %s, got %s",
                     key, expected_kind, kind)
        return None
    return value, kind, expires_at


# R-F1541: _UPSERT_TIMEOUT_S removed — _upsert no longer uses a timeout.
# Writes go through the bounded async queue (_enqueue_write) and are
# processed by a background worker. If the queue is full, the caller gets
# StateWriteError immediately (backpressure) instead of timing out after
# 30s and silently losing data. This eliminates the entire failure class
# of timeout → WARNING → error_log_handler → record_error → more timeouts.


async def _upsert(key: str, value: str, kind: str, expires_at: float | None,
                  keepttl: bool = False) -> None:
    """Upsert a key-value pair via the bounded write queue (R-F1541).
    
    Instead of calling _conn.execute() with a 30s timeout (which silently
    drops writes when the worker thread is saturated), we enqueue the write
    to a bounded async queue. If the queue is full, StateWriteError is raised
    immediately — the caller gets backpressure instead of silent data loss.
    
    This eliminates the entire failure class of:
      - 30s timeout → silent data loss
      - timeout WARNING → error_log_handler → record_error → more timeouts
      - brain_hook circuit trip → all learning stops
      - autonomous_engine blackout → heartbeat stale
    """
    if _conn is None:
        import sqlite3
        e = sqlite3.OperationalError(
            f"state_store: no connection (reconnect in progress) writing {key}")
        _schedule_reconnect_if_dead(e)
        raise e
    
    if keepttl and expires_at is None:
        sql = (
            "INSERT INTO state(key, value, kind, expires_at) "
            "VALUES(?, ?, ?, NULL) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value, kind = excluded.kind"
        )
        params = (key, value, kind)
    else:
        sql = (
            "INSERT INTO state(key, value, kind, expires_at) "
            "VALUES(?, ?, ?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value, "
            "kind = excluded.kind, expires_at = excluded.expires_at"
        )
        params = (key, value, kind, expires_at)

    await _enqueue_write(sql, params, key=key)  # R-F2290: route by key


# ─────────────────────────────────────────────────────────────────────────
# Public API — mirrors redis_store
# ─────────────────────────────────────────────────────────────────────────

# R-F2156: cooldown cache for error_log reads to prevent flood.
# When state_store is under load, every read of the error_log key times out
# and logs a WARNING. The error_log_handler now skips "timed out" messages
# (breaking the feedback loop), but the cooldown here prevents the rapid-fire
# reads themselves from hammering the DB. The cache is keyed on the full key
# string so it only affects the error_log key, not all reads.
_ERROR_LOG_COOLDOWN_S = 5.0
_error_log_cache: dict[str, tuple[float, str | None]] = {}  # key -> (last_attempt, cached_result)


# ── R-F4107 (C-140): the store must REMEMBER that it went blind ──────────────
#
# On 2026-08-17, 06:46:47-06:48:45Z, `get()` timed out on 25 distinct keys (26
# reads) and returned None for every one. Under the R-F1 None-on-error contract
# each became an absence indistinguishable from a real one — including
# `autonomous:paused:task:*` (a paused task reads as NOT paused), `aria:cost`
# (the meter enforcing the §17 cap) and `aria:error` (the ledger gate #3 reads).
#
# `/health` five minutes later: `state_backend: {reachable: true, status:
# green}`. The indicator is point-in-time reachability with NO MEMORY, so a
# surface cannot tell a quiet store from one that just failed 26 reads.
#
# THE RECORD IS DELIBERATELY IN-PROCESS ONLY. Persisting it would mean writing
# to a wedged store precisely when it is wedged — the R-F2157 self-DOS shape,
# deepening the outage it exists to report. The process that served the failed
# reads is the process that serves /health, so in-memory is the honest scope.
_READ_TIMEOUT_WINDOW_S = 900.0     # 15 min — long enough to survive a boot poll
_READ_TIMEOUT_DEGRADE_AT = 5       # a blip is noise; the live burst was 26
_READ_TIMEOUT_MAX = 500            # bounded: never a leak, never a lifetime tally
_read_timeouts: list[tuple[float, str]] = []


def note_read_timeout(key: Any) -> None:
    """Record ONE store read timeout. Pure, in-process, and never raises.

    Called from `get()`'s timeout branch, which is already on a failure path —
    so this must not do I/O, must not await, and must not be able to turn a
    degraded read into a crashed one.
    """
    try:
        k = key if isinstance(key, str) else str(key)
        _read_timeouts.append((time.monotonic(), k[:120]))
        if len(_read_timeouts) > _READ_TIMEOUT_MAX:
            del _read_timeouts[:-_READ_TIMEOUT_MAX]
    except Exception:      # pragma: no cover — observability never breaks a read
        pass


def read_timeout_report(window_s: float = _READ_TIMEOUT_WINDOW_S) -> dict:
    """How blind has this process been, recently?

    `keys_sample` names WHAT went dark: "a timeout happened" is not actionable
    when the key was the paused-task flag or the cost meter.
    """
    try:
        cutoff = time.monotonic() - float(window_s)
        recent = [(t, k) for (t, k) in _read_timeouts if t >= cutoff]
        last_age = None
        if recent:
            last_age = round(time.monotonic() - max(t for t, _ in recent), 1)
        # Distinct key families, newest first — 25 shard keys are one story.
        seen: list[str] = []
        for _, k in reversed(recent):
            if k not in seen:
                seen.append(k)
            if len(seen) >= 10:
                break
        return {
            "window_s": float(window_s),
            "count": len(recent),
            "distinct_keys": len({k for _, k in recent}),
            "last_age_s": last_age,
            "keys_sample": seen,
            "degraded": len(recent) >= _READ_TIMEOUT_DEGRADE_AT,
        }
    except Exception:      # pragma: no cover
        # Could not measure. Say so — never render an unknown as healthy (C-96).
        return {"window_s": float(window_s), "count": 0, "distinct_keys": 0,
                "last_age_s": None, "keys_sample": [], "degraded": False,
                "unmeasurable": True}


def _reset_read_timeouts_for_test() -> None:
    _read_timeouts.clear()


def _is_error_log_key(key: str) -> bool:
    """R-F3707 — the ONE predicate deciding what `get()` may cache.

    Named so the read path and the write-invalidation path cannot drift: an
    inline `"error_log" in key` duplicated at two sites is how a cache ends up
    populated on read and never cleared on write, which is precisely the bug
    this exists to close.
    """
    return "error_log" in (key or "")

# R-F2477 — the read-path write-queue flush in _row() is a best-effort read-after-
# write helper; it must NEVER consume the read's 5s budget. Under writer saturation
# (the R-F2277 storm) that flush queues behind the single writer thread and blocks
# the full 5s, so the actual read never runs → get() times out → returns None → the
# app reads LIVE data (e.g. the DD report_index, which is present in the DB) as
# EMPTY, producing the blank DD page / "running forever" symptom. Cap the flush hard;
# on cap we skip it and read COMMITTED data from the WAL read pool (any <100ms
# uncommitted write is healed by the 100ms background drain). Decouples reads from
# the write storm — reads stay fast even when writes are slow.
_READ_FLUSH_BUDGET_S = 0.3


async def get(key: str) -> str | None:
    """R-F2154: bounded read — wraps _row in a 5s timeout so a slow DB
    never hangs boot or a request forever. Returns None on timeout (same
    as key-not-found) — the caller degrades gracefully rather than
    blocking the event loop.

    R-F2156: cooldown for the error_log key. If the same key was read
    within _ERROR_LOG_COOLDOWN_S seconds, returns the cached result
    without hitting the DB. This prevents rapid-fire reads of the error_log
    from hammering the DB during load spikes."""
    # R-F2156 / R-F2477: the cooldown cache is ONLY for the hot error_log key. The
    # docstring above claimed that, but the code populated it for EVERY key — so a
    # single transient read timeout cached None for 5s and blinded live keys (the DD
    # report_index would read as EMPTY → blank page / "running forever", even though
    # the value is present in the DB). Scope the cache strictly to error_log keys;
    # for every other key a timeout is transient and MUST NOT be cached as None.
    _cacheable = _is_error_log_key(key)   # R-F3707 — shared with the write invalidation
    if _cacheable and key in _error_log_cache:
        last_attempt, cached = _error_log_cache[key]
        if time.monotonic() - last_attempt < _ERROR_LOG_COOLDOWN_S:
            return cached
    try:
        row = await asyncio.wait_for(
            _row(key, expected_kind="string"), timeout=5.0)
        result = row[0] if row else None
        if _cacheable:
            _error_log_cache[key] = (time.monotonic(), result)
        return result
    except asyncio.TimeoutError:
        logger.warning(
            "state_store.get(%s) timed out after 5s — DB may be "
            "bloated or under WAL recovery. Returning None.",
            key[:80],
        )
        # R-F4107 (C-140) — leave a trace /health can read. Without this the
        # None below is indistinguishable from a genuine absence and the whole
        # burst is invisible five minutes later.
        note_read_timeout(key)
        if _cacheable:
            _error_log_cache[key] = (time.monotonic(), None)
        return None


def _get_direct_sync(key: str) -> str | None:
    """R-F2500 — storm-proof read via a FRESH read-only sqlite connection. Bypasses the
    wedged writer, the contended read pool, AND the _row flush, so a DURABLE value stays
    readable at the peak of a write storm. Proven need: a completed DD's 21KB report blob
    was present in the DB but get()/_row timed out reading it (5s) while the DD's own
    writes saturated the single writer — the report showed "running forever". Mirrors
    get()'s lazy-TTL + 'string' kind semantics. Best-effort — None on any error."""
    if _DB_PATH is None:
        return None
    # R-F2707 — route cold-prefix keys to the COLD db file so the storm-proof direct
    # reader doesn't silently return a false None for a value that lives in
    # aria_knowledge_store.db when the hot/cold split is on. Latent today (the only
    # get_direct caller reads a HOT dd:report key), but a cold-key reader added later
    # would get "absent" for a present fact → data-blindness/fabrication. Byte-identical
    # when the split is off or the key is hot.
    _db = _DB_PATH
    if _HOTCOLD_SPLIT and _COLD_DB_PATH is not None and _route_db(key) == "cold":
        _db = _COLD_DB_PATH
    import sqlite3
    try:
        # NOT mode=ro: a read-only URI connection can't read UNCHECKPOINTED WAL data
        # (a just-written blob still in the -wal file). A normal connection + query_only
        # reads the WAL like any reader (WAL readers never block on the writer) while
        # still rejecting writes — so this is storm-proof for freshly-written values too.
        conn = sqlite3.connect(str(_db), timeout=2.0)
        try:
            conn.execute("PRAGMA query_only=ON")
            row = conn.execute(
                "SELECT value, expires_at FROM state WHERE key=? AND kind='string'",
                (key,),
            ).fetchone()
        finally:
            conn.close()
    except Exception:
        return None
    if not row:
        return None
    value, expires_at = row[0], row[1]
    if expires_at is not None and expires_at < time.time():
        return None
    return value


async def get_direct(key: str) -> str | None:
    """Async wrapper for _get_direct_sync — runs the blocking read-only read OFF the
    event loop. Use as a FALLBACK when get() returns None during a write storm and the
    value is known durable (e.g. get_report). Complements R-F2477 (which scoped the
    error_log cache) by giving critical readers a path immune to writer saturation."""
    try:
        return await asyncio.to_thread(_get_direct_sync, key)
    except Exception:
        return None


async def get_strict(key: str) -> str | None:
    """R-F1392: like get(), but a store-layer failure RAISES StateReadError
    instead of silently returning None. A None return therefore means the key
    is GENUINELY absent/expired — which is what the async job-poll endpoints
    need to honestly answer not_found vs 503-retry (see StateReadError).
    
    R-F2154: bounded — 5s timeout so a slow DB never hangs a request.

    R-F3679 — reads through the READ pool, like every other reader.

    This used to execute on ``_conn``, the single WRITE connection. Reads on it
    queue behind write traffic, so on a busy store every strict read hit the 5s
    timeout and raised, while the graceful ``get()`` — which goes through ``_row``
    -> ``_reader_conn_for`` -> ``_get_read_conn`` (R-F1449's dedicated read
    connection, round-robined over the pool by R-F2242, never touched by
    ``_reconnect``, WAL snapshot reads that do not block on the writer) — kept
    succeeding. R-F1449 moved the read path off the write connection and the
    STRICT path was never moved with it.

    Measured live on aria-intel 2026-08-04: six consecutive probes, strict reads
    failing 100% while graceful reads succeeded 100%, on keys whose values were
    present on disk. ``crucix:autonomous:enabled_override`` is a ONE-CHARACTER
    value and timed out too, so this was never about payload size.

    The blast radius is every ``*_strict`` caller — 30 production sites — and
    they are honesty-critical by construction, because the whole point of the
    strict readers (R-F1392) is to tell "genuinely absent" apart from "the store
    broke". When they raise, that distinction inverts into the wrong answer:
    Phase A gates 2/3/5/6 went unmeasurable (gate 5 could not see the autonomy
    override that is the only thing keeping autonomy ON, so it reported the env's
    `0`), ``_load_feed_health`` returned None so R-F2890's quarantine self-heal
    was SKIPPED on every poll, and ``dd_orchestrator``'s report-blob read — a
    customer-facing path — could not fetch a report that exists.

    Also routes hot/cold correctly now. ``_conn`` is the HOT write connection, so
    a cold-routed key was previously queried against the wrong database and would
    return a false "genuinely absent" — the exact answer this function exists to
    never give.
    """
    # R-F3679 — same read-after-write guarantee the graceful path has. Without
    # this a set() immediately followed by get_strict() reads None, i.e. reports
    # "genuinely absent" for a key that was just written.
    await _flush_pending_for_read(key)
    conn = _reader_conn_for(key)
    if conn is None:
        raise StateReadError(
            f"state_store: no read connection (reconnect in progress) reading {key}")
    try:
        cur = await asyncio.wait_for(conn.execute(
            "SELECT value, kind, expires_at FROM state WHERE key = ?", (key,)), timeout=5.0)
        row = await asyncio.wait_for(cur.fetchone(), timeout=5.0)
        await cur.close()
    except asyncio.TimeoutError:
        raise StateReadError(f"state_store: SELECT {key} timed out after 5s")
    except Exception as e:
        err_str = str(e)
        # R-F3679 — mirror _row's retry-once-on-closed. A read connection can be
        # swapped underneath us by _ensure_read_conn; raising on that would turn a
        # recoverable blip into "the store broke", which callers escalate.
        if ('closed' in err_str or 'Cannot operate' in err_str
                or 'no active connection' in err_str):
            try:
                await _ensure_read_conn()
                conn2 = _reader_conn_for(key)
                if conn2 is not None:
                    cur = await asyncio.wait_for(conn2.execute(
                        "SELECT value, kind, expires_at FROM state WHERE key = ?",
                        (key,)), timeout=5.0)
                    row = await asyncio.wait_for(cur.fetchone(), timeout=5.0)
                    await cur.close()
                else:
                    raise StateReadError(
                        f"state_store: no read connection after reopen for {key}")
            except StateReadError:
                raise
            except Exception as e2:
                _schedule_reconnect_if_dead(e2)
                raise StateReadError(
                    f"state_store: SELECT {key} failed after reopen: {e2}") from e2
        else:
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
    # R-F2622: the Phase A gate-#3 streak anchor + genesis marker. These
    # record that an ERROR happened (and when measurement began) — the
    # durable memory that stops the gate certifying on absence of evidence.
    # A TTL here would silently restore R-F560's false pass, so make
    # "never expires" an enforced invariant rather than a convention.
    "crucix:aria:error_streak",
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


async def set_key(key: str, value: str, ex: int | None = None,
                  keepttl: bool = False) -> None:
    """Set a string value. Renamed from `set` (R-F2133) to avoid shadowing
    builtins.set(), which triggered the pre-commit hook and required --no-verify
    bypass. All external callers go through redis_store.set() which dispatches
    to state_store.set_key()."""
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
    # ── R-F3707 — a WRITE must invalidate the read cache for that key ───────
    #
    # THE DEFECT: `get()` caches any key containing "error_log" for
    # _ERROR_LOG_COOLDOWN_S (5.0s), and NOTHING invalidated that cache on write.
    # `self_improve.record_error` does read → append → write of the whole
    # 200-entry blob, so two errors inside 5 seconds meant the second read the
    # PRE-APPEND cached snapshot, appended its own entry, and wrote it back —
    # ERASING THE FIRST. Bursts are exactly when errors cluster and exactly when
    # the evidence matters; the ledger was quietly losing the beginning of every
    # incident.
    #
    # One line, and it restores read-after-write for the only cached namespace.
    if _error_log_cache and _is_error_log_key(key):
        _error_log_cache.pop(key, None)


async def set_if_absent(key: str, value: str, ex: int) -> bool:
    """R-F2174 — atomic claim primitive for leader election. Sets key=value
    with TTL `ex` seconds ONLY if the key is absent OR its lease has EXPIRED;
    it NEVER steals a live lease. Returns True iff THIS call now owns the key.

    Executes immediately (not via the bounded write queue) because the caller
    needs the win/lose result synchronously. Cross-process safe: SQLite
    serialises the conditional upsert at the file level, so among N worker
    processes exactly one wins the claim. RAISES on any store error so the
    caller can distinguish "lost the race" (False) from "could not run"
    (exception → fail-safe) — critical for election correctness."""
    if _conn is None:
        raise StateWriteError(f"set_if_absent({key}): no connection")
    if _is_infinite_key(key):
        raise ValueError(f"R-F2174: refusing lease TTL on knowledge key {key!r}")
    now = _now()
    expires_at = now + max(1, int(ex))
    # Preserve program order vs queued writes (mirrors delete()).
    await _flush_write_queue()
    # Claim if absent, OR take over an EXPIRED lease; the WHERE clause makes the
    # ON CONFLICT a no-op when a live lease is held by someone else.
    await asyncio.wait_for(_conn.execute(
        "INSERT INTO state(key, value, kind, expires_at) VALUES(?,?,?,?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value, "
        "kind=excluded.kind, expires_at=excluded.expires_at "
        "WHERE state.expires_at IS NOT NULL AND state.expires_at <= ?",
        (key, value, "string", expires_at, now)), timeout=5.0)
    await _conn.commit()
    cur = await asyncio.wait_for(
        _conn.execute("SELECT value FROM state WHERE key=?", (key,)), timeout=5.0)
    row = await cur.fetchone()
    await cur.close()
    return bool(row and row[0] == value)


async def renew_lease(key: str, value: str, ex: int) -> bool:
    """R-F2174 — extend a lease's TTL by `ex` seconds ONLY if `value` still owns
    it. Returns True if renewed, False if we no longer own it (expired + taken
    over). Used by the engine heartbeat to keep its lease alive."""
    if _conn is None:
        return False
    now = _now()
    expires_at = now + max(1, int(ex))
    await _flush_write_queue()
    try:
        cur = await asyncio.wait_for(_conn.execute(
            "UPDATE state SET expires_at=? WHERE key=? AND value=?",
            (expires_at, key, value)), timeout=5.0)
        await _conn.commit()
        return (cur.rowcount or 0) > 0
    except Exception as e:
        logger.warning("state_store: renew_lease(%s) failed: %s", key, e)
        return False


async def delete(key: str) -> bool:
    if _conn is None:
        return False
    # R-F1933 (M4): drain queued set()s FIRST. set/set_json enqueue (R-F1541) while
    # delete executes immediately; without this a prior queued set(k) would flush on
    # the NEXT read and resurrect the key after this delete. Flush-first keeps the
    # FIFO program order (set→delete = deleted), and the bool return is preserved.
    await _flush_write_queue()
    deleted = False
    try:
        cur = await _conn.execute("DELETE FROM state WHERE key = ?", (key,))
        await _conn.commit()
        deleted = (cur.rowcount or 0) > 0
    except Exception as e:
        logger.warning("state_store: DELETE %s failed: %s", key, e)
    # R-F2413: dual-delete the cold store when the split is on. A cold-prefixed
    # key (reasoning_library case, reasoning_library.py:1030/1129) lands in the
    # cold file after cutover; deleting only from hot would leave it a zombie.
    # During migration a key may still be in EITHER file, so we delete from both
    # and OR the results. Flag OFF → cold conn is None → hot-only, unchanged.
    if _HOTCOLD_SPLIT and _cold_conn is not None:
        try:
            await _flush_cold_queue()
            cur = await _cold_conn.execute("DELETE FROM state WHERE key = ?", (key,))
            await _cold_conn.commit()
            deleted = deleted or (cur.rowcount or 0) > 0
        except Exception as e:
            logger.warning("state_store[cold]: DELETE %s failed: %s", key, e)
    return deleted


async def get_json(key: str) -> Any:
    raw = await get(key)
    if raw:
        try:
            return json.loads(raw)
        except Exception as e:
            logger.warning("state_store: JSON parse %s failed: %s", key, e)
    # R-F996 — wire to brain
    from .engine_wiring import wire_success, wire_failure
    wire_success(
        module="state_store",
        summary="Get Json",
        source_id="state_store:R-F996",
    )

    return None


async def set_json(key: str, obj: Any, ex: int | None = None,
                   keepttl: bool = False) -> None:
    await set_key(key, json.dumps(obj, default=str), ex=ex, keepttl=keepttl)


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
# R-F1520: use a dedicated lock for the dict itself to prevent race
# conditions when two concurrent calls create a lock for the same key.
_lpush_locks: dict[str, asyncio.Lock] = {}
_lpush_locks_lock = asyncio.Lock()


async def _get_lpush_lock(key: str) -> asyncio.Lock:
    """Get or create a per-list lock for lpush serialization."""
    async with _lpush_locks_lock:
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
    # R-F1933 (M4): flush queued writes first so the list seq-counter read/INSERT
    # below stays ordered relative to the enqueued set/set_json path.
    await _flush_write_queue()
    seq_key = _list_seq_counter(key)
    # R-F1518: per-list lock to serialize counter increment + INSERT.
    # This is a fast operation (microseconds) — the lock is never held
    # across I/O boundaries, so it cannot cause contention.
    # R-F1520: all writes go through _conn (single connection, single worker
    # thread). Multiple connections to the same SQLite file contend for the
    # WAL lock and make `database is locked` errors MORE likely, not less.
    lock = await _get_lpush_lock(key)
    async with lock:
        try:
            # R-F2470: derive seq AUTHORITATIVELY from the list itself, not a
            # separate counter that can drift below MAX(seq). The counter fell
            # behind — the INSERT-OR-IGNORE list-materialization path inserts seq
            # values without bumping it, and a wedge/restart can leave the read
            # stale — so a counter-derived seq collided with an existing row:
            #   "UNIQUE constraint failed: list_entries.list_key, seq"
            # -> the lpush was SILENTLY DROPPED (e.g. a lost DD watchlist alert).
            # MAX(seq)+1 under the per-list lock is authoritative and never collides.
            cur = await _conn.execute(
                "SELECT COALESCE(MAX(seq), 0) FROM list_entries WHERE list_key = ?", (key,)
            )
            row = await cur.fetchone()
            await cur.close()
            seq = (int(row[0]) if row and row[0] is not None else 0) + 1
            # Insert the entry, then keep the seq counter in sync (some callers /
            # future paths may still read it) — atomically in a single commit.
            await _conn.execute(
                "INSERT INTO list_entries(list_key, seq, value) VALUES(?, ?, ?)",
                (key, seq, value),
            )
            await _conn.execute(
                "INSERT INTO state(key, value, kind) VALUES(?, CAST(? AS TEXT), 'string') "
                "ON CONFLICT(key) DO UPDATE SET value = CAST(? AS TEXT)",
                (seq_key, seq, seq),
            )
            await _conn.commit()
        except Exception as e:
            logger.warning("[R-F1518/R-F2470] lpush %s failed: %s", key, e)
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
            await _conn.execute(  # nosec B608 - placeholders are generated for integer seq values; values stay parameterized.
                f"DELETE FROM list_entries WHERE list_key = ? AND seq IN ({placeholders})",  # nosec B608 - generated placeholders only; seqs are parameterized.
                (key, *delete),
            )
            await _conn.commit()
    except Exception as e:
        logger.warning("[R-F1515] ltrim %s failed: %s", key, e)


async def lrem(key: str, count: int, value: str) -> int:
    """Remove list entries equal to ``value`` using Redis LREM semantics."""
    if _conn is None:
        return 0
    try:
        await _migrate_list_if_needed(key)
        if count == 0:
            cur = await _conn.execute(
                "DELETE FROM list_entries WHERE list_key = ? AND value = ?",
                (key, value),
            )
        else:
            order = "DESC" if count > 0 else "ASC"
            cur = await _conn.execute(
                "DELETE FROM list_entries WHERE list_key = ? AND seq IN ("  # nosec B608 - order is selected from two fixed SQL keywords; all values remain parameterized.
                f"SELECT seq FROM list_entries WHERE list_key = ? AND value = ? "
                f"ORDER BY seq {order} LIMIT ?)",
                (key, key, value, abs(count)),
            )
        removed = max(0, int(cur.rowcount or 0))
        await cur.close()
        await _conn.commit()
        return removed
    except Exception as e:
        logger.warning("[R-F3225] lrem %s failed: %s", key, e)
        return 0


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
    # R-F1520: all writes go through _conn (single connection, single worker
    # thread). Multiple connections to the same SQLite file contend for the
    # WAL lock and make `database is locked` errors MORE likely, not less.
    if _conn is None:
        import sqlite3
        e = sqlite3.OperationalError(
            f"state_store: no connection (reconnect in progress) writing {key}")
        _schedule_reconnect_if_dead(e)
        if critical:
            raise StateWriteError(f"incr: no connection") from e
        return 0

    # R-F1933 (M4): flush queued set()s first. This UPSERT increments the CURRENT
    # DB row; a queued set(k) that hasn't landed would make incr compute on a stale
    # value (then the set flushes after and clobbers the increment).
    await _flush_write_queue()
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
    """Atomic float increment.

    R-F2148: uses a single SQL UPSERT (atomic at the SQLite level) instead
    of holding the Python-level lock for a read-modify-write cycle. Previously
    this held _get_lock() for the entire _row + _upsert sequence via _run_locked,
    which could block for 15+ seconds under write contention, causing the
    state_store lock storm that wedged the app and caused health-check timeouts.

    Falls back to the locked path only when the atomic UPSERT fails.
    """
    if _conn is None:
        import sqlite3
        e = sqlite3.OperationalError(
            f"state_store: no connection (reconnect in progress) writing {key}")
        _schedule_reconnect_if_dead(e)
        if critical:
            raise StateWriteError(f"incrbyfloat: no connection") from e
        return 0.0

    # R-F1933 (M4): flush queued set()s first. This UPSERT increments the CURRENT
    # DB row; a queued set(k) that hasn't landed would make incrbyfloat compute on
    # a stale value (then the set flushes after and clobbers the increment).
    await _flush_write_queue()
    try:
        # Atomic UPSERT: INSERT if missing (value=amount), else increment.
        # SQLite serialises writes through its single worker thread — no
        # Python-level lock needed. This avoids the _run_locked contention
        # that caused the 2026-06-29 state_store wedge.
        await _conn.execute(
            "INSERT INTO state(key, value, kind, expires_at) "
            "VALUES(?, CAST(? AS TEXT), 'string', NULL) "
            "ON CONFLICT(key) DO UPDATE SET "
            "  value = CAST(CAST(value AS REAL) + ? AS TEXT)",
            (key, amount, amount),
        )
        await _conn.commit()
        # Read back the new value
        cur = await _conn.execute(
            "SELECT value FROM state WHERE key = ?", (key,)
        )
        row = await cur.fetchone()
        await cur.close()
        return float(row[0]) if row else amount
    except Exception as e:
        err_str = str(e).lower()
        # Retry ONCE with a short delay on transient contention before
        # falling back to the locked path.
        if "database is locked" in err_str or "busy" in err_str:
            try:
                await asyncio.sleep(0.5)
                await _conn.execute(
                    "INSERT INTO state(key, value, kind, expires_at) "
                    "VALUES(?, CAST(? AS TEXT), 'string', NULL) "
                    "ON CONFLICT(key) DO UPDATE SET "
                    "  value = CAST(CAST(value AS REAL) + ? AS TEXT)",
                    (key, amount, amount),
                )
                await _conn.commit()
                cur = await _conn.execute(
                    "SELECT value FROM state WHERE key = ?", (key,)
                )
                row = await cur.fetchone()
                await cur.close()
                return float(row[0]) if row else amount
            except Exception:
                pass  # fall through to locked path below
        logger.debug("[R-F2148] atomic incrbyfloat failed for %s: %s — falling back to locked path", key, e)
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
        return await _run_locked("incrbyfloat", _op, default=0.0, critical=critical)


async def expire(key: str, seconds: int) -> bool:
    if _conn is None:
        return False
    # R-F1933 (M4): flush queued set()s first so this TTL update applies to the
    # latest value (a queued set(k) would otherwise land after and the row this
    # UPDATE targeted may not exist / be stale).
    await _flush_write_queue()
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
    # R-F1933 (M4): flush queued writes first so hash UPSERTs stay ordered relative
    # to the enqueued set/set_json path (one ordered write timeline, no reorder).
    await _flush_write_queue()
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


async def hget(key: str, field: str) -> str | None:
    """Get a single field from a hash. Returns None if the field is absent.

    R-F2486: the exact `hget` method was missing (only hset/hgetall/hdel
    existed). dd_trigger_pipeline called `rs.hget(...)`, which raised
    AttributeError; the callers' broad except swallowed it, so the DD trigger
    guard failed OPEN (repeated failing DD runs were not suppressed). Mirrors
    hgetall's single-SELECT + legacy JSON-blob fallback.
    """
    if _conn is None:
        return None
    try:
        cur = await _conn.execute(
            "SELECT value FROM hash_entries WHERE hash_key = ? AND field = ?",
            (key, field),
        )
        row = await cur.fetchone()
        await cur.close()
        if row is not None:
            return row[0]
        # Fallback: migrate a legacy JSON blob then retry once.
        await _migrate_hash_if_needed(key)
        cur = await _conn.execute(
            "SELECT value FROM hash_entries WHERE hash_key = ? AND field = ?",
            (key, field),
        )
        row = await cur.fetchone()
        await cur.close()
        return row[0] if row is not None else None
    except Exception as e:
        logger.warning("[R-F2486] hget %s.%s failed: %s", key, field, e)
        return None


async def hincrby(key: str, field: str, amount: int = 1, *, critical: bool = False) -> int:
    """Atomically increment an integer hash field, returning the new value.

    R-F2625: this method was missing (only hset/hgetall/hget/hdel existed), so
    `rs.hincrby(...)` at dd_orchestrator.py:8167 raised AttributeError; the
    finalizer's broad `except: pass` swallowed it and the DD per-layer stats
    (`crucix:dd:layer_stats:<layer>`) were NEVER written. The DD health endpoint
    (routes/aria.py:1699) therefore read {} for all 11 layers forever — which
    looks like "no failures" but means "never recorded". Same class as R-F2486.

    Single atomic UPSERT — no Python lock and no read-modify-write, per R-F1518.
    That matters because concurrent DD finalizers increment the SAME hash key,
    so a get+set compose would lose increments and under-report failures.

    SQLite `CAST('abc' AS INTEGER)` yields 0, so a non-numeric field resets to
    `amount` rather than raising (Redis HINCRBY would error). Acceptable here:
    these are best-effort observability counters, never money or verdicts.
    """
    if _conn is None:
        if critical:
            raise StateWriteError(f"hincrby {key}.{field}: no connection")
        return 0
    # R-F1933 (M4): keep hash UPSERTs ordered against the enqueued set/set_json path.
    await _flush_write_queue()
    try:
        await _conn.execute(
            "INSERT INTO hash_entries(hash_key, field, value) VALUES(?, ?, ?) "
            "ON CONFLICT(hash_key, field) DO UPDATE SET "
            "value = CAST(CAST(hash_entries.value AS INTEGER) + ? AS TEXT)",
            (key, field, str(int(amount)), int(amount)),
        )
        await _conn.commit()
        cur = await _conn.execute(
            "SELECT value FROM hash_entries WHERE hash_key = ? AND field = ?",
            (key, field),
        )
        row = await cur.fetchone()
        await cur.close()
        return int(row[0]) if row is not None else int(amount)
    except Exception as e:
        logger.warning("[R-F2625] hincrby %s.%s failed: %s", key, field, e)
        if critical:
            raise StateWriteError(f"hincrby {key}.{field}: {e}") from e
        return 0


# ── Glob scan ───────────────────────────────────────────────────────────

async def scan_keys(pattern: str, count: int = 200) -> list[str]:
    # R-F1871 — read-after-write consistency + use the dedicated READ connection,
    # mirroring _row(): flush the R-F1541 write queue (so set()->scan() sees its
    # own writes) and read via _get_read_conn() (R-F1449) instead of the write
    # `_conn`. The old scan_keys read `_conn` WITHOUT flushing, so it could miss
    # freshly-queued writes (latent bug, masked in prod where writes are drained).
    try:
        await _flush_write_queue()
        # R-F2413: also flush the cold queue so a cold-prefix scan sees its own
        # freshly-queued writes (mirrors the K/V read-after-write in _row).
        if _HOTCOLD_SPLIT and _cold_queue is not None:
            await _flush_cold_queue()
    except Exception:
        pass
    conn = _get_read_conn()
    if conn is None:
        return []
    # R-F1871 — push the pattern's LITERAL PREFIX into SQL so we don't fetch the
    # ENTIRE `state` keyspace on every call. The old query was
    # `SELECT key FROM state` (no filter) + a Python fnmatch over EVERY row —
    # O(all keys) on the event loop. absorption_quarantine.stats() runs scan_keys
    # on every /api/aria/health hit, so on a large brain it stalled the loop for
    # seconds (R-F703 wedge; web_integrity escalated /api/aria/health timing out
    # >215s — the health endpoint wedging itself). Almost all 23 callers use a
    # `prefix*` pattern; `key GLOB 'prefix*'` is a range scan on the `key` PRIMARY
    # KEY index (GLOB is case-sensitive so the planner uses the index, unlike
    # LIKE). The fnmatch below still runs for EXACT Redis-glob semantics
    # (?, [abc], mid-pattern metachars) over the now-narrow result.
    _meta = min((pattern.find(_c) for _c in "*?[" if _c in pattern), default=-1)
    _prefix = pattern if _meta < 0 else pattern[:_meta]

    async def _scan_conn(c) -> list[str]:
        """Run the prefix-narrowed SELECT against one connection; [] on error."""
        if c is None:
            return []
        try:
            if _prefix:
                cur = await c.execute(
                    "SELECT key FROM state WHERE key GLOB ? "
                    "AND (expires_at IS NULL OR expires_at > ?)",
                    (_prefix + "*", _now()),
                )
            else:
                cur = await c.execute(
                    "SELECT key FROM state WHERE (expires_at IS NULL OR expires_at > ?)",
                    (_now(),),
                )
            rows = await cur.fetchall()
            await cur.close()
            return [r[0] for r in rows]
        except Exception as e:
            logger.warning("state_store: SCAN failed: %s", e)
            _schedule_reconnect_if_dead(e)  # R-F1352: read path self-heals
            return []

    keys = await _scan_conn(conn)
    # R-F2413: UNION the cold store's keys when the split is on, so a scan over a
    # cold prefix (aria:verified_facts:* etc., verified_intel.py:1317/1356/1407)
    # still finds cold-file rows after cutover. Hot keys first; dedup by key (a
    # key may exist in BOTH files mid-migration). Flag OFF → cold conn is None →
    # byte-identical single-file scan.
    if _HOTCOLD_SPLIT and _cold_read_conn is not None:
        keys = keys + await _scan_conn(_cold_read_conn)
    matched: list[str] = []
    seen: set = builtins.set()
    for k in keys:
        if k in seen:
            continue
        seen.add(k)
        if fnmatch.fnmatch(k, pattern):
            matched.append(k)
            if len(matched) >= count:
                break
    return matched


async def scan_keys_strict(pattern: str, count: int = 200) -> list[str]:
    """C-38 — `scan_keys` that RAISES StateReadError instead of returning [] on failure.

    `scan_keys` swallows a dead connection and a failed SQL range scan alike, returning
    the same empty list a genuinely empty keyspace produces. A caller that reports on
    the result then cannot tell "nothing matched" from "the scan did not run", and
    publishes the first while meaning the second. Mirrors the `get_strict` contract.
    """
    conn = _get_read_conn()
    if conn is None:
        raise StateReadError("state_store: no read connection for SCAN")
    try:
        await _flush_write_queue()
        if _HOTCOLD_SPLIT and _cold_queue is not None:
            await _flush_cold_queue()
    except Exception:
        pass
    try:
        return await scan_keys(pattern, count)
    except Exception as e:  # pragma: no cover - scan_keys catches internally
        raise StateReadError(f"state_store: SCAN {pattern} failed: {e}") from e


async def scan_keys_null_ttl(pattern: str, count: int = 500) -> list[str]:
    """R-F2629 — keys matching `pattern` that carry NO TTL (expires_at IS NULL).

    Exists so a repair sweep can target ONLY the rows a non-atomic
    set+expire race stranded without an expiry, instead of deleting every
    key under a prefix and hoping. scan_keys() cannot express this: it
    returns keys but not TTLs, and it deliberately INCLUDES NULL-expiry rows
    as "live".

    Read-only, and mirrors _scan_conn's prefix-narrowing so it is a range
    scan on the key PRIMARY KEY index rather than a full keyspace walk
    (R-F1871). Returns [] on any failure — callers MUST therefore be
    idempotent and treat [] as "nothing to do THIS pass", never as proof the
    keyspace is clean. That conflation is precisely what let R-F2626's sweep
    declare victory over a failed read (R-F2629).
    """
    try:
        await _flush_write_queue()
        if _HOTCOLD_SPLIT and _cold_queue is not None:
            await _flush_cold_queue()
    except Exception:
        pass
    conn = _get_read_conn()
    if conn is None:
        return []

    _meta = min((pattern.find(_c) for _c in "*?[" if _c in pattern), default=-1)
    _prefix = pattern if _meta < 0 else pattern[:_meta]

    async def _scan(c) -> list[str]:
        if c is None:
            return []
        try:
            cur = await c.execute(
                "SELECT key FROM state WHERE key GLOB ? AND expires_at IS NULL",
                ((_prefix or "") + "*",),
            )
            rows = await cur.fetchall()
            await cur.close()
            return [r[0] for r in rows]
        except Exception as e:
            logger.warning("state_store: SCAN(null-ttl) failed: %s", e)
            _schedule_reconnect_if_dead(e)
            return []

    keys = await _scan(conn)
    if _HOTCOLD_SPLIT and _cold_read_conn is not None:
        keys = keys + await _scan(_cold_read_conn)

    matched: list[str] = []
    seen: set = builtins.set()
    for k in keys:
        if k in seen:
            continue
        seen.add(k)
        if fnmatch.fnmatch(k, pattern):
            matched.append(k)
            if len(matched) >= count:
                break
    return matched


async def scan_json(pattern: str, count: int = 200) -> list[tuple[str, "Any"]]:
    """R-F1885 — like scan_keys, but returns (key, parsed-JSON-value) for each
    match in ONE query, so callers don't fan out N separate get_json round-trips.
    absorption_quarantine.stats() did up to 500 sequential `await get_json(k)` on
    every /api/aria/health hit — the dominant health-endpoint cost. Same
    GLOB-prefix + fnmatch + flush + read-connection contract as scan_keys; values
    that aren't valid JSON are skipped (mirrors get_json returning None)."""
    try:
        await _flush_write_queue()
        # R-F2413: flush the cold queue too for cold-prefix read-after-write.
        if _HOTCOLD_SPLIT and _cold_queue is not None:
            await _flush_cold_queue()
    except Exception:
        pass
    conn = _get_read_conn()
    if conn is None:
        return []
    _meta = min((pattern.find(_c) for _c in "*?[" if _c in pattern), default=-1)
    _prefix = pattern if _meta < 0 else pattern[:_meta]

    async def _scan_conn(c) -> list[tuple]:
        if c is None:
            return []
        try:
            if _prefix:
                cur = await c.execute(
                    "SELECT key, value FROM state WHERE key GLOB ? "
                    "AND (expires_at IS NULL OR expires_at > ?)",
                    (_prefix + "*", _now()),
                )
            else:
                cur = await c.execute(
                    "SELECT key, value FROM state WHERE (expires_at IS NULL OR expires_at > ?)",
                    (_now(),),
                )
            rows = await cur.fetchall()
            await cur.close()
            return list(rows)
        except Exception as e:
            logger.warning("state_store: SCAN_JSON failed: %s", e)
            _schedule_reconnect_if_dead(e)  # read path self-heals
            return []

    rows = await _scan_conn(conn)
    # R-F2413: UNION cold rows when the split is on (see scan_keys). Hot first;
    # dedup by key so a mid-migration key present in both files isn't double-counted.
    if _HOTCOLD_SPLIT and _cold_read_conn is not None:
        rows = rows + await _scan_conn(_cold_read_conn)
    out: list[tuple[str, "Any"]] = []
    seen: set = builtins.set()
    for (k, v) in rows:
        if k in seen:
            continue
        if not fnmatch.fnmatch(k, pattern):
            continue
        seen.add(k)
        try:
            out.append((k, json.loads(v)))
        except Exception:
            continue  # not JSON / unparseable — skip, like get_json
        if len(out) >= count:
            break
    return out


# ─────────────────────────────────────────────────────────────────────────
# Diagnostics
# ─────────────────────────────────────────────────────────────────────────

def connection_gauge() -> dict:
    """R-F3263 — live aiosqlite connection cost, readable at ANY time.

    Until now this number could only be obtained from an R-F704 wedge stack,
    which is written only WHEN A STALL HAPPENS. So the one measurement that
    tells you a connection leak is building was available exclusively after it
    had already done its damage — and the 2026-07-27 investigation had to ssh
    to the box and grep a crash dump to get it.

    Two numbers, and the second is the important one:

      workers        live aiosqlite worker threads. Each connection runs
                     exactly one, so this IS the connection count.
      stuck_reaps    `_reap_old_conns` closes fire-and-forget with a 30s bound
                     because a WEDGED connection's close() queues behind the
                     stuck op and would block the self-heal path. The task is
                     held in `_reap_tasks` and discarded on completion — so a
                     close that never completes stays in that set FOREVER.
                     A non-zero, non-falling value here is a thread that will
                     never come back, which is the residual leak in its purest
                     form. It was already being tracked; nobody could see it.

    `expected` is the design cost, stated so a reader can judge the others
    without knowing the architecture: state_store's own writer + read pool +
    cold pair, plus the six modules that each hold one lazy singleton
    (brain_ingest_queue, dialogue_state, user_model, bookmarks, reading_queue,
    search_index). Counting only state_store's six — as the first pass of this
    investigation did — overstates the gap by half.

    Counted by THREAD NAME. aiosqlite's Connection is not a Thread subclass, so
    an isinstance check silently returns zero and every alarm built on it never
    fires.
    """
    import threading as _th

    # BOTH names, and this is not defensive padding — it is the whole
    # correctness of the gauge. `redis_store` replaces
    # `aiosqlite.core._connection_worker_thread` with its own `_patched_worker`
    # (the R-F "Event loop is closed" shutdown guard), and CPython names a
    # thread after its TARGET FUNCTION. So in any process that imports
    # redis_store — i.e. production — every worker is
    # "Thread-N (_patched_worker)" and matching only the aiosqlite name returns
    # ZERO FOREVER.
    #
    # Caught because this gauge's test passed alone and failed in the suite:
    # standalone there is no redis_store import, in-suite there is. That was
    # not flakiness. The wedge-stack counts taken during the investigation are
    # unaffected — they grep stack FRAMES, and the innermost frame is still
    # aiosqlite's own function, called by the patched wrapper.
    _WORKER_THREAD_NAMES = ("_connection_worker_thread", "_patched_worker")
    workers = sum(1 for t in _th.enumerate()
                  if t.is_alive() and any(n in t.name for n in _WORKER_THREAD_NAMES))
    expected = _READ_POOL_SIZE + 1 + 2 + 6
    return {
        "workers": workers,
        "stuck_reaps": len(_reap_tasks),
        "expected": expected,
        "excess": max(0, workers - expected),
    }


async def stats() -> dict:
    """Return basic backend stats for the /health endpoint."""
    hot_depth = _QUEUED_WRITES.qsize() if _QUEUED_WRITES is not None else 0
    cold_depth = _cold_queue.qsize() if _cold_queue is not None else 0
    total_depth = hot_depth + cold_depth
    total_capacity = _WRITE_QUEUE_MAX * (2 if _cold_queue is not None else 1)
    queue_stats = {
        "hot": hot_depth,
        "cold": cold_depth,
        "total": total_depth,
        "max": _WRITE_QUEUE_MAX,
        "capacity": total_capacity,
        "headroom": max(0, total_capacity - total_depth),
        "utilization": round(total_depth / total_capacity, 4) if total_capacity else 0.0,
        "hot_utilization": round(hot_depth / _WRITE_QUEUE_MAX, 4) if _WRITE_QUEUE_MAX else 0.0,
        "cold_utilization": round(cold_depth / _WRITE_QUEUE_MAX, 4) if _WRITE_QUEUE_MAX else 0.0,
        "hot_cold_split": bool(_HOTCOLD_SPLIT and _cold_queue is not None),
        "hot_worker_alive": bool(_WRITE_WORKER_TASK and not _WRITE_WORKER_TASK.done()),
        "cold_worker_alive": bool(_COLD_WORKER_TASK and not _COLD_WORKER_TASK.done()),
    }
    if _conn is None:
        return {
            "backend": "sqlite",
            "configured": False,
            "db_path": str(_DB_PATH) if _DB_PATH else None,
            "write_queue_depth": queue_stats["total"],
            "write_queue": queue_stats,
            # R-F3263 — the gauge belongs on THIS branch most of all. `_conn is
            # None` is the wedged/reconnecting state, which is exactly when
            # orphaned workers accumulate and exactly when someone is looking.
            # Reporting connections only on the healthy path would hide the
            # number in the one condition it exists to explain.
            "connections": connection_gauge(),
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
            "write_queue_depth": queue_stats["total"],
            "write_queue": queue_stats,
            # R-F3263 — the connection gauge has to be REACHABLE or it is just
            # a function nobody calls. `stats()` is what /health renders, so
            # this is the surface that makes `workers` and `stuck_reaps`
            # answerable without ssh-ing to the box during a stall.
            "connections": connection_gauge(),
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

# R-F2538: R-F2119 import-time wire_failure("module shutdown") block removed — it fired a FALSE engine_failure gap on every import (not at shutdown); do not re-add.
