"""brain_ingest_queue — durable SQLite-backed priority queue for brain ingest.

R-F2507. Why this module exists
────────────────────────────────
ARIA's `state_store` is a SINGLE aiosqlite writer (R-F1541/R-F2277). Under
L3 autonomy load, every brain-ingest write (brain_hook.absorb → verified_facts,
audit, reasoning_library) competes for that one writer thread, and the
read-modify-write bursts are the DD-latency ceiling (see
`memory/writer_split_reclaim_and_brain_ingest_2026_07_08.md`). The fix is to
DECOUPLE ingest from the hot request path: enqueue the ingest payload here
(one cheap INSERT into a SEPARATE db file, so it never contends with the
state_store writer) and let a single background drain worker apply it later.

§6-compliant: native SQLite via aiosqlite only. No Redis/Kafka/Postgres.

Design conventions mirror `state_store.py`:
  - DB path from env (`ARIA_BRAIN_QUEUE_DB`) with a `/data`-missing local
    fallback to a temp dir, exactly like state_store's `/tmp` fallback.
  - WAL journal mode, `busy_timeout=30000`, `synchronous=NORMAL`.
  - A module-level aiosqlite connection guarded by an `asyncio.Lock` for the
    compound read-then-write (claim) operations.

Durability contract:
  - `enqueue` persists before returning — a crash after enqueue does not lose
    the item (it survives on the /data volume, replayed on next drain).
  - `dequeue_batch` atomically flips claimed rows to 'processing' under the
    write lock, so a row is handed to exactly one worker.
  - A worker that dies mid-flight leaves 'processing' rows; `recover_stuck()`
    (run at `connect()`) resets them to 'pending' so nothing is stranded.
  - Poison payloads (won't json-parse) go straight to the dead_letter table
    instead of blocking the queue forever.

Best-effort contract: every public coroutine except `connect()` logs and
swallows errors, NEVER raising into the caller — brain ingest must never take
down the request path. `connect()` may raise at boot (fail-loud there).
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import tempfile
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────────────
# Configuration
# ─────────────────────────────────────────────────────────────────────────

# Queue-depth safety valve. If pending depth reaches this, enqueue evicts the
# single oldest-lowest-priority row to make room (bounded disk, never blocks).
_QUEUE_MAX = int(os.getenv("ARIA_BRAIN_QUEUE_MAX", "50000"))

# Priority buckets reported by stats(). 0 = highest.
_PRIORITY_BUCKETS = (0, 1, 2, 3)

_DB_PATH: Path | None = None
_conn: Any = None  # aiosqlite.Connection — module-level, lazy-init in connect()
_write_lock: asyncio.Lock | None = None  # guards claim (SELECT+UPDATE) + evict


def _now() -> float:
    """Wall-clock epoch seconds. Production code — not resume-constrained."""
    return time.time()


def _get_lock() -> asyncio.Lock:
    """Lazy-bind the write lock inside the running loop.

    Mirrors state_store's `_reset_lock` intent: pytest's per-test
    `asyncio.run()` each gets a fresh loop, and a Lock bound to a dead loop
    would raise. connect() rebinds it, so under normal use this just returns
    the existing lock.
    """
    global _write_lock
    if _write_lock is None:
        _write_lock = asyncio.Lock()
    return _write_lock


def _ensure_aiosqlite_daemon_workers(aiosqlite_module: Any) -> None:
    """Ensure aiosqlite worker threads cannot keep the process alive.

    Mirrors state_store's helper so a hung drain thread never blocks shutdown.
    """
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
# Lifecycle
# ─────────────────────────────────────────────────────────────────────────

async def connect() -> bool:
    """Open the queue DB and create the schema. Idempotent — safe to call twice.

    Creates tables + indexes and runs `recover_stuck()` (crash recovery). This
    is the boot path and MAY raise on a genuinely unopenable DB, matching the
    fail-loud-at-boot convention; steady-state callers use the best-effort API
    below.
    """
    global _conn, _DB_PATH, _write_lock
    # Rebind the write lock to the current running loop (pytest re-entrancy).
    _write_lock = asyncio.Lock()

    import aiosqlite
    _ensure_aiosqlite_daemon_workers(aiosqlite)

    db_path = os.getenv("ARIA_BRAIN_QUEUE_DB", "/data/brain_ingest_queue.db")
    _DB_PATH = Path(db_path)
    try:
        _DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    except Exception as e:
        # Fall back to a local temp dir if /data is unmounted (state_store
        # falls back to /tmp/aria_state.db; we mirror that intent).
        logger.warning(
            "brain_ingest_queue: cannot create parent dir %s: %s — "
            "falling back to temp dir", _DB_PATH.parent, e)
        _DB_PATH = Path(tempfile.gettempdir()) / "brain_ingest_queue.db"
        try:
            _DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        except Exception:
            pass

    if _conn is not None:
        # Idempotent: a prior connect left a live handle. Close it so the new
        # one is bound to the current loop / path.
        try:
            await _conn.close()
        except Exception:
            pass
        _conn = None

    _conn = await asyncio.wait_for(
        aiosqlite.connect(str(_DB_PATH)), timeout=30.0)
    # R-F2132 ordering: busy_timeout BEFORE journal_mode (boot-deadlock guard).
    await _conn.execute("PRAGMA busy_timeout=30000")
    await _conn.execute("PRAGMA journal_mode=WAL")
    await _conn.execute("PRAGMA synchronous=NORMAL")
    await _conn.execute(
        """
        CREATE TABLE IF NOT EXISTS queue (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            priority        INTEGER NOT NULL DEFAULT 2,
            payload         TEXT NOT NULL,
            enqueued_at     REAL NOT NULL,
            attempts        INTEGER NOT NULL DEFAULT 0,
            next_attempt_at REAL NOT NULL DEFAULT 0,
            status          TEXT NOT NULL DEFAULT 'pending'
        )
        """
    )
    # Drives dequeue_batch's claim scan: WHERE status=? AND next_attempt_at<=?
    # ORDER BY priority, id.
    await _conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_queue_claim "
        "ON queue(status, priority, next_attempt_at, id)"
    )
    await _conn.execute(
        """
        CREATE TABLE IF NOT EXISTS dead_letter (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            payload     TEXT,
            error       TEXT,
            attempts    INTEGER,
            enqueued_at REAL,
            failed_at   REAL
        )
        """
    )
    await _conn.commit()
    # Crash recovery: reclaim any rows a died worker left mid-flight.
    try:
        recovered = await recover_stuck()
        if recovered:
            logger.info(
                "brain_ingest_queue: recovered %d stuck 'processing' rows at boot",
                recovered)
    except Exception as e:  # never let recovery block boot
        logger.warning("brain_ingest_queue: recover_stuck at boot failed: %s", e)
    logger.info("brain_ingest_queue: connected at %s (max=%d)",
                _DB_PATH, _QUEUE_MAX)
    return True


async def close() -> None:
    """Close the queue DB connection. Best-effort, never raises."""
    global _conn
    if _conn is None:
        return
    try:
        await asyncio.wait_for(_conn.close(), timeout=5.0)
    except Exception as e:
        logger.warning("brain_ingest_queue: close failed: %s", e)
    finally:
        _conn = None


# ─────────────────────────────────────────────────────────────────────────
# Producer
# ─────────────────────────────────────────────────────────────────────────

async def enqueue(payload: dict, priority: int = 2) -> bool:
    """Persist one ingest payload. Returns True on insert, False on error.

    priority: 0 = highest, drained first. Best-effort: never raises.

    Backpressure: if pending depth is at the `ARIA_BRAIN_QUEUE_MAX` cap, the
    single oldest-lowest-priority pending row is deleted first to make room
    (bounded disk; enqueue never blocks the hot path). Eviction is logged.
    """
    if _conn is None:
        logger.warning("brain_ingest_queue: enqueue before connect() — dropped")
        return False
    try:
        blob = json.dumps(payload, ensure_ascii=False, default=str)
    except Exception as e:
        logger.warning("brain_ingest_queue: payload not serialisable — dropped: %s", e)
        return False
    try:
        lock = _get_lock()
        async with lock:
            # Enforce the depth cap on PENDING rows only (processing/DLQ rows
            # are transient / already terminal). Evict oldest-lowest-priority.
            async with _conn.execute(
                "SELECT COUNT(*) FROM queue WHERE status='pending'"
            ) as cur:
                row = await cur.fetchone()
            depth = int(row[0]) if row else 0
            if depth >= _QUEUE_MAX:
                # priority DESC = highest priority NUMBER (lowest importance)
                # first; id ASC = oldest of those. Delete exactly one to fit.
                await _conn.execute(
                    "DELETE FROM queue WHERE id = ("
                    "  SELECT id FROM queue WHERE status='pending' "
                    "  ORDER BY priority DESC, id ASC LIMIT 1)"
                )
                logger.warning(
                    "brain_ingest_queue: at cap (%d) — evicted 1 oldest-lowest-"
                    "priority pending row to make room", _QUEUE_MAX)
            await _conn.execute(
                "INSERT INTO queue (priority, payload, enqueued_at, attempts, "
                "next_attempt_at, status) VALUES (?, ?, ?, 0, 0, 'pending')",
                (int(priority), blob, _now()),
            )
            await _conn.commit()
        return True
    except Exception as e:
        logger.warning("brain_ingest_queue: enqueue failed: %s", e)
        return False


# ─────────────────────────────────────────────────────────────────────────
# Consumer
# ─────────────────────────────────────────────────────────────────────────

async def dequeue_batch(limit: int = 20) -> list[dict]:
    """Claim up to `limit` due pending rows, flipping them to 'processing'.

    Returns `[{"id", "payload", "attempts", "priority"}, ...]` in drain order
    (priority ASC, id ASC = highest priority, oldest first). The payload is
    json-parsed; a row whose payload won't parse is moved straight to the
    dead_letter table and skipped (poison-message guard). The SELECT+UPDATE
    runs under the write lock so each row is claimed by exactly one caller.

    Best-effort: returns [] on error, never raises.
    """
    if _conn is None:
        return []
    try:
        now = _now()
        lock = _get_lock()
        async with lock:
            async with _conn.execute(
                "SELECT id, payload, attempts, priority FROM queue "
                "WHERE status='pending' AND next_attempt_at <= ? "
                "ORDER BY priority ASC, id ASC LIMIT ?",
                (now, int(limit)),
            ) as cur:
                rows = await cur.fetchall()
            if not rows:
                return []

            claimed_ids: list[int] = []
            out: list[dict] = []
            poison: list[tuple[int, str]] = []  # (id, payload_blob)
            for row_id, blob, attempts, priority in rows:
                try:
                    payload = json.loads(blob)
                except Exception:
                    poison.append((int(row_id), blob))
                    continue
                claimed_ids.append(int(row_id))
                out.append({
                    "id": int(row_id),
                    "payload": payload,
                    "attempts": int(attempts),
                    "priority": int(priority),
                })

            # Flip good rows to 'processing' so no one else claims them.
            if claimed_ids:
                marks = ",".join("?" for _ in claimed_ids)
                await _conn.execute(
                    f"UPDATE queue SET status='processing' WHERE id IN ({marks})",
                    claimed_ids,
                )
            # Route poison rows straight to the DLQ and drop them from queue.
            for row_id, blob in poison:
                await _conn.execute(
                    "INSERT INTO dead_letter (payload, error, attempts, "
                    "enqueued_at, failed_at) VALUES (?, ?, ?, ?, ?)",
                    (blob, "payload json-parse failed", 0, None, now),
                )
                await _conn.execute("DELETE FROM queue WHERE id = ?", (row_id,))
                logger.warning(
                    "brain_ingest_queue: dead-lettered unparseable payload id=%d",
                    row_id)
            await _conn.commit()
            return out
    except Exception as e:
        logger.warning("brain_ingest_queue: dequeue_batch failed: %s", e)
        return []


async def mark_done(ids: list[int]) -> None:
    """Delete completed rows. Best-effort, never raises.

    R-F2564: runs under the write lock so a completing worker's DELETE+commit
    never interleaves with another worker's in-flight dequeue_batch claim on the
    shared connection (safe when ARIA_BRAIN_QUEUE_WORKERS>1)."""
    if _conn is None or not ids:
        return
    try:
        async with _get_lock():
            clean = [int(i) for i in ids]
            marks = ",".join("?" for _ in clean)
            await _conn.execute(f"DELETE FROM queue WHERE id IN ({marks})", clean)
            await _conn.commit()
    except Exception as e:
        logger.warning("brain_ingest_queue: mark_done failed: %s", e)


async def mark_failed(row_id: int, payload: dict, attempts: int, error: str,
                      max_attempts: int = 5) -> None:
    """Record a failed drain attempt: retry with exponential backoff, or DLQ.

    If `attempts + 1 < max_attempts`: bump attempts, flip back to 'pending'
    and defer via `next_attempt_at = now + min(60, 2**(attempts+1))` seconds.
    Otherwise: move the row to the dead_letter table and delete it from queue.

    Best-effort, never raises.
    """
    if _conn is None:
        return
    try:
        # R-F2564: all _conn mutations here run under the write lock so they never
        # interleave with a concurrent worker's dequeue_batch claim (N>1 safe).
        async with _get_lock():
            now = _now()
            new_attempts = int(attempts) + 1
            if new_attempts < int(max_attempts):
                backoff = min(60.0, float(2 ** new_attempts))
                await _conn.execute(
                    "UPDATE queue SET attempts = attempts + 1, status='pending', "
                    "next_attempt_at = ? WHERE id = ?",
                    (now + backoff, int(row_id)),
                )
                await _conn.commit()
                return
            # Terminal: dead-letter it. Preserve enqueued_at from the queue row if
            # the row still exists, else fall back to now.
            enq_at: float | None
            async with _conn.execute(
                "SELECT enqueued_at FROM queue WHERE id = ?", (int(row_id),)
            ) as cur:
                r = await cur.fetchone()
            enq_at = float(r[0]) if r and r[0] is not None else now
            try:
                blob = json.dumps(payload, ensure_ascii=False, default=str)
            except Exception:
                blob = str(payload)
            await _conn.execute(
                "INSERT INTO dead_letter (payload, error, attempts, enqueued_at, "
                "failed_at) VALUES (?, ?, ?, ?, ?)",
                (blob, str(error)[:2000], new_attempts, enq_at, now),
            )
            await _conn.execute("DELETE FROM queue WHERE id = ?", (int(row_id),))
            await _conn.commit()
            logger.warning(
                "brain_ingest_queue: dead-lettered id=%d after %d attempts: %s",
                int(row_id), new_attempts, str(error)[:200])
    except Exception as e:
        logger.warning("brain_ingest_queue: mark_failed failed: %s", e)


async def recover_stuck() -> int:
    """Reset 'processing' rows back to 'pending' (crash recovery).

    A worker that died mid-flight leaves rows in 'processing'. Called at
    connect(); returns the number of rows reclaimed. Best-effort → 0 on error.
    """
    if _conn is None:
        return 0
    try:
        lock = _get_lock()
        async with lock:
            cur = await _conn.execute(
                "UPDATE queue SET status='pending' WHERE status='processing'")
            await _conn.commit()
            return int(cur.rowcount or 0)
    except Exception as e:
        logger.warning("brain_ingest_queue: recover_stuck failed: %s", e)
        return 0


async def stats() -> dict:
    """Return queue health metrics. Best-effort → safe defaults on error."""
    out: dict[str, Any] = {
        "depth": 0,
        "by_priority": {p: 0 for p in _PRIORITY_BUCKETS},
        "processing": 0,
        "dead_letter": 0,
        "oldest_age_s": 0.0,
    }
    if _conn is None:
        return out
    try:
        async with _conn.execute(
            "SELECT COUNT(*) FROM queue WHERE status='pending'") as cur:
            r = await cur.fetchone()
        out["depth"] = int(r[0]) if r else 0

        async with _conn.execute(
            "SELECT priority, COUNT(*) FROM queue WHERE status='pending' "
            "GROUP BY priority") as cur:
            for priority, n in await cur.fetchall():
                out["by_priority"][int(priority)] = int(n)

        async with _conn.execute(
            "SELECT COUNT(*) FROM queue WHERE status='processing'") as cur:
            r = await cur.fetchone()
        out["processing"] = int(r[0]) if r else 0

        async with _conn.execute("SELECT COUNT(*) FROM dead_letter") as cur:
            r = await cur.fetchone()
        out["dead_letter"] = int(r[0]) if r else 0

        async with _conn.execute(
            "SELECT MIN(enqueued_at) FROM queue WHERE status='pending'") as cur:
            r = await cur.fetchone()
        if r and r[0] is not None:
            out["oldest_age_s"] = max(0.0, _now() - float(r[0]))
        return out
    except Exception as e:
        logger.warning("brain_ingest_queue: stats failed: %s", e)
        return out


async def list_dead_letter(limit: int = 50) -> list[dict]:
    """R-F3712 — READ the dead-letter table. Nothing could, before this.

    THE DEFECT: rows were INSERTed here on a poison payload (:329) and on
    terminal failure after max_attempts (:402), and the ONLY reader in the whole
    tree was `SELECT COUNT(*)` in stats(). The `payload` column was never
    selected by anything. `recover_stuck()` sounds like the drain but only
    UPDATEs the `queue` table, which dead-lettered rows have already left.

    So 114 brain signals (measured live 2026-08-04) were parked permanently:
    knowledge that failed to reach the brain, with no reader, no replay, no
    prune and no alert. The module's own docstring promises items are "replayed
    on next drain", which was false for anything that reached here — and it is a
    §7 violation, since the contract is that ARIA never loses knowledge.
    """
    await connect()
    if _conn is None:
        return []
    try:
        async with _conn.execute(
            "SELECT id, payload, error, attempts, enqueued_at, failed_at "
            "FROM dead_letter ORDER BY failed_at DESC LIMIT ?", (int(limit),)
        ) as cur:
            rows = await cur.fetchall()
        return [
            {"id": r[0], "payload": r[1], "error": r[2], "attempts": r[3],
             "enqueued_at": r[4], "failed_at": r[5]}
            for r in rows
        ]
    except Exception as e:
        logger.warning("brain_ingest_queue: list_dead_letter failed: %s", e)
        return []


async def replay_dead_letter(limit: int = 50, *, ids: list[int] | None = None) -> dict:
    """R-F3712 — move dead-lettered rows back onto the queue.

    The exit that never existed. A row is re-enqueued with attempts reset and
    is DELETEd from dead_letter only after the queue insert succeeds, inside one
    transaction, so a failure here cannot lose the row — the whole point is that
    nothing in this path may drop knowledge.

    Poison payloads (unparseable JSON) are NOT replayed: they would fail
    identically and churn. They are reported so an operator can see them.
    """
    await connect()
    if _conn is None:
        return {"ok": False, "reason": "no connection", "replayed": 0}

    replayed, poison, failed = 0, 0, 0
    try:
        if ids:
            q = ("SELECT id, payload, enqueued_at FROM dead_letter WHERE id IN ("
                 + ",".join("?" * len(ids)) + ")")
            params: tuple = tuple(int(i) for i in ids)
        else:
            q = ("SELECT id, payload, enqueued_at FROM dead_letter "
                 "ORDER BY failed_at ASC LIMIT ?")
            params = (int(limit),)
        async with _conn.execute(q, params) as cur:
            rows = await cur.fetchall()

        for row_id, payload, enqueued_at in rows:
            try:
                json.loads(payload)          # poison check — do not re-enqueue
            except Exception:
                poison += 1
                continue
            try:
                await _conn.execute(
                    "INSERT INTO queue (payload, priority, status, attempts, "
                    "enqueued_at, next_attempt_at) VALUES (?, ?, 'pending', 0, ?, ?)",
                    (payload, 2, enqueued_at or _now(), _now()),
                )
                await _conn.execute("DELETE FROM dead_letter WHERE id = ?", (row_id,))
                replayed += 1
            except Exception as e:
                failed += 1
                logger.warning("replay_dead_letter: row %s failed: %s", row_id, e)
        await _conn.commit()
    except Exception as e:
        logger.warning("brain_ingest_queue: replay_dead_letter failed: %s", e)
        return {"ok": False, "reason": str(e)[:200], "replayed": replayed}

    logger.info("[R-F3712] dead-letter replay: %d requeued, %d poison, %d failed",
                replayed, poison, failed)
    return {"ok": True, "replayed": replayed, "poison_skipped": poison,
            "failed": failed}


# ─────────────────────────────────────────────────────────────────────────
# Self-test — python -m aria_service.intel.brain_ingest_queue
# ─────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import shutil

    async def _selftest() -> int:
        tmpdir = tempfile.mkdtemp(prefix="biq_selftest_")
        os.environ["ARIA_BRAIN_QUEUE_DB"] = os.path.join(tmpdir, "biq.db")
        failures = 0

        def check(name: str, ok: bool) -> None:
            nonlocal failures
            print(f"{'PASS' if ok else 'FAIL'}: {name}")
            if not ok:
                failures += 1

        try:
            await connect()

            # enqueue + priority-ordered dequeue
            await enqueue({"n": 1}, priority=2)
            await enqueue({"n": 2}, priority=0)  # highest
            await enqueue({"n": 3}, priority=2)
            batch = await dequeue_batch(limit=10)
            order = [b["payload"]["n"] for b in batch]
            check("priority + FIFO order (2 before 1 before 3)", order == [2, 1, 3])

            # mark_done removes
            await mark_done([batch[0]["id"]])
            s = await stats()
            check("mark_done removed one (processing back to 2)",
                  s["processing"] == 2)

            # mark_failed: retry then DLQ
            row = batch[1]
            attempts = row["attempts"]
            # attempts 1..4 → retry; the 5th failure (new_attempts=5) → DLQ
            for _ in range(5):
                await mark_failed(row["id"], row["payload"], attempts,
                                  "boom", max_attempts=5)
                # re-read the live attempts count for the next call
                async with _conn.execute(
                    "SELECT attempts FROM queue WHERE id=?", (row["id"],)) as cur:
                    rr = await cur.fetchone()
                if rr is None:
                    break
                attempts = int(rr[0])
            s = await stats()
            check("mark_failed retries then DLQs at max_attempts",
                  s["dead_letter"] == 1)

            # recover_stuck: the remaining 'processing' row (n=3) resets
            recovered = await recover_stuck()
            check("recover_stuck reset processing->pending", recovered >= 1)
            s = await stats()
            check("stats depth reflects recovered pending", s["depth"] >= 1)

            await close()
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)

        print(f"\n{'ALL PASS' if failures == 0 else f'{failures} FAILURE(S)'}")
        return failures

    raise SystemExit(asyncio.run(_selftest()))
