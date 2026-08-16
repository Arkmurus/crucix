"""reading_queue — R-F661 failed-quiz → reading-list auto-enrollment.

R-F661 (2026-05-17). Slice 4 of the OSS-only learning-framework
buildout. **Phase B** (explicit operator override granted 2026-05-17).

Why this exists
═══════════════
When ARIA's self_quiz cycle finds she failed to reproduce an answer
locally (similarity < 0.4 vs the original cloud response, OR local
stack couldn't answer at all), the topic she failed on becomes a
reading-list candidate. Pre-R-F661, failed quizzes only updated
mastery; there was no persistent backlog the controller (R-F662)
could pick from.

The reading queue closes that loop: every failed quiz auto-enrolls a
queue entry tagged with the topic, the source question, and the
case_id (so we can retrieve it later). The Phase B controller will
drain this queue at a deterministic rate using only local code paths
(RAG retrieval + neural-memory traversal + cross-corroboration) — no
cloud LLM calls inside the loop.

Storage
═══════
SQLite via aiosqlite, mirroring the pattern in dialogue_state.py.
Survives process restarts, no Redis dependency, no third-party
backend. Schema:

  reading_queue(
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    topic        TEXT NOT NULL,
    source_question  TEXT,
    source_case_id   TEXT,
    reason       TEXT NOT NULL DEFAULT 'failed_quiz',
    status       TEXT NOT NULL DEFAULT 'pending',
                 -- pending | processing | done | skipped
    enqueued_at  REAL NOT NULL,
    processed_at REAL,
    notes        TEXT
  )

Dedup: a (topic, source_case_id) pair that's already in 'pending' or
'processing' state will NOT be re-enqueued (returns the existing id).
Done/skipped entries CAN be re-enqueued — the controller may want a
re-read months later if mastery has drifted.

Public surface
══════════════
    async enqueue(topic, *, source_question="", source_case_id="",
                  reason="failed_quiz", notes="") -> int
    async pop_pending(limit=10) -> list[dict]
        Returns oldest-first pending entries. Does NOT flip status —
        the caller calls mark_processed when they're done.
    async mark_processed(item_id, *, status="done", notes="") -> bool
    async list_pending(limit=50) -> list[dict]
    async stats() -> dict
"""
from __future__ import annotations

import logging
import os
import time
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger("aria.learning.reading_queue")

# R-F4052 (C-108) — §21a wiring for this module's WORK, not its import.
#
# The old R-F1319 block fired wire_success at IMPORT time under
# "learning.reading_queue" — a name brain_hook._MODULE_TOPICS does not contain, so it
# was invisible to `never_seen` (C-104) and proved only that the file had been
# imported. Outcomes are now reported under the REGISTERED name, on BOTH
# branches. Success is throttled because this module's work runs per item.


def _wire_reading_queue(ok: bool, summary: str) -> None:
    """Report a real outcome to the brain. Never raises."""
    try:
        from aria_service.intel.engine_wiring import (
            wire_failure, wire_success_throttled,
        )
        if ok:
            wire_success_throttled(
                "reading_queue", summary, source_id="reading_queue:work",
            )
        else:
            wire_failure(
                module="reading_queue", detail=summary,
                gap_type="engine_failure", source="reading_queue:work",
            )
    except Exception as _exc:   # pragma: no cover - telemetry never blocks
        # Swallowed deliberately: brain wiring must never break this
        # module's work. Logged rather than `pass`ed so the wiring
        # failure is not itself dark (bandit B110) — wiring the wiring
        # failure would be circular.
        logger.debug("[R-F4052] reading_queue outcome wiring failed: %s", _exc)


_DB_PATH_ENV = "ARIA_READING_QUEUE_DB_PATH"
_DEFAULT_DB_PATH = "/data/aria_reading_queue.db"

_conn = None  # aiosqlite.Connection set lazily


def _db_path() -> str:
    """DB path with sensible fallback for local dev (no /data volume)."""
    p = os.environ.get(_DB_PATH_ENV) or _DEFAULT_DB_PATH
    parent = Path(p).parent
    if not parent.exists():
        # Local-dev fallback: stash next to the working tree.
        local = Path.cwd() / "aria_reading_queue.db"
        return str(local)
    return p


async def _ensure_conn():
    global _conn
    if _conn is not None:
        return _conn
    import aiosqlite
    path = _db_path()
    _conn = await aiosqlite.connect(path)
    await _conn.execute("""
        CREATE TABLE IF NOT EXISTS reading_queue (
            id               INTEGER PRIMARY KEY AUTOINCREMENT,
            topic            TEXT NOT NULL,
            source_question  TEXT,
            source_case_id   TEXT,
            reason           TEXT NOT NULL DEFAULT 'failed_quiz',
            status           TEXT NOT NULL DEFAULT 'pending',
            enqueued_at      REAL NOT NULL,
            processed_at     REAL,
            notes            TEXT
        )
    """)
    await _conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_rq_status_enq "
        "ON reading_queue(status, enqueued_at)"
    )
    await _conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_rq_topic_caseid "
        "ON reading_queue(topic, source_case_id, status)"
    )
    await _conn.commit()
    logger.info("R-F661 reading_queue ready at %s", path)
    return _conn


async def _close_conn() -> None:
    """Test-only helper to release the SQLite connection."""
    global _conn
    if _conn is not None:
        try:
            await _conn.close()
        except Exception as _exc:
            # R-F4052 (C-108) — a teardown failure was silent (bandit B110).
            # Still swallowed (close must not raise on shutdown), but visible.
            logger.debug("[R-F4052] %s conn close failed: %s", "reading_queue", _exc)
    _conn = None


def _row_to_dict(row) -> dict[str, Any]:
    if not row:
        return {}
    return {
        "id": row[0],
        "topic": row[1],
        "source_question": row[2],
        "source_case_id": row[3],
        "reason": row[4],
        "status": row[5],
        "enqueued_at": row[6],
        "processed_at": row[7],
        "notes": row[8],
    }


async def enqueue(
    topic: str,
    *,
    source_question: str = "",
    source_case_id: str = "",
    reason: str = "failed_quiz",
    notes: str = "",
) -> Optional[int]:
    """Enqueue a reading-list entry. Returns the entry id, or None on
    failure (logged, never raises).

    Dedup: if a `pending` or `processing` entry already exists for the
    same (topic, source_case_id) pair, return the existing id without
    inserting a duplicate. Topics without a case_id (free-text) skip
    the case-id dedup but still dedup on topic alone within `pending`."""
    if not topic or not topic.strip():
        logger.warning("R-F661 enqueue: topic required")
        return None
    topic = topic.strip()
    try:
        conn = await _ensure_conn()
    except Exception as e:
        logger.warning("R-F661 enqueue: ensure_conn failed: %s", e)
        return None

    # Dedup pass
    try:
        if source_case_id:
            cur = await conn.execute(
                "SELECT id FROM reading_queue "
                "WHERE topic = ? AND source_case_id = ? "
                "AND status IN ('pending','processing') "
                "ORDER BY enqueued_at DESC LIMIT 1",
                (topic, source_case_id),
            )
        else:
            cur = await conn.execute(
                "SELECT id FROM reading_queue "
                "WHERE topic = ? AND (source_case_id IS NULL OR source_case_id = '') "
                "AND status = 'pending' "
                "ORDER BY enqueued_at DESC LIMIT 1",
                (topic,),
            )
        row = await cur.fetchone()
        await cur.close()
        if row:
            return int(row[0])
    except Exception as e:
        logger.debug("R-F661 enqueue: dedup query failed (proceeding): %s", e)

    # Insert
    now = time.time()
    try:
        cur = await conn.execute(
            "INSERT INTO reading_queue ("
            "topic, source_question, source_case_id, reason, status, "
            "enqueued_at, notes"
            ") VALUES (?, ?, ?, ?, 'pending', ?, ?)",
            (topic, source_question or "", source_case_id or "",
             reason or "failed_quiz", now, notes or ""),
        )
        await conn.commit()
        new_id = cur.lastrowid
        await cur.close()
        return int(new_id) if new_id else None
    except Exception as e:
        logger.warning("R-F661 enqueue: insert failed: %s", e)
        return None


async def pop_pending(limit: int = 10) -> list[dict]:
    """Return oldest-first pending entries. Does NOT flip status — the
    caller is responsible for calling mark_processed when they finish
    processing each one. This lets the controller (R-F662) batch-read
    and decide whether to skip / process based on local heuristics."""
    try:
        conn = await _ensure_conn()
    except Exception:
        return []
    limit = max(1, min(int(limit), 200))
    try:
        cur = await conn.execute(
            "SELECT * FROM reading_queue "
            "WHERE status = 'pending' "
            "ORDER BY enqueued_at ASC LIMIT ?",
            (limit,),
        )
        rows = await cur.fetchall()
        await cur.close()
        return [_row_to_dict(r) for r in rows]
    except Exception as e:
        logger.warning("R-F661 pop_pending: select failed: %s", e)
        return []


async def mark_processed(item_id: int, *, status: str = "done",
                          notes: str = "") -> bool:
    """Flip status to 'done' or 'skipped'. Idempotent — re-marking is a
    no-op that returns True."""
    if status not in ("done", "skipped", "processing"):
        logger.warning("R-F661 mark_processed: invalid status %r", status)
        return False
    try:
        conn = await _ensure_conn()
    except Exception as e:
        # R-F4056 (C-120) — a genuine failure that returned silently. §21a.
        _wire_reading_queue(False, f"mark_processed: no connection: {e}")
        return False
    now = time.time()
    try:
        cur = await conn.execute(
            "UPDATE reading_queue SET status = ?, processed_at = ?, "
            "notes = COALESCE(NULLIF(?,''), notes) "
            "WHERE id = ?",
            (status, now, notes, int(item_id)),
        )
        await conn.commit()
        ok = cur.rowcount > 0
        await cur.close()
        _wire_reading_queue(True, f"reading item {item_id} marked {status}")
        return ok
    except Exception as e:
        logger.warning("R-F661 mark_processed: update failed: %s", e)
        _wire_reading_queue(False, f"mark_processed failed: {type(e).__name__}: {e}")
        return False


async def list_pending(limit: int = 50) -> list[dict]:
    """Same as pop_pending but a more readable name for read-only use."""
    return await pop_pending(limit=limit)


async def stats() -> dict:
    """Aggregate counters per status."""
    try:
        conn = await _ensure_conn()
    except Exception:
        return {"pending": 0, "processing": 0, "done": 0, "skipped": 0}
    out = {"pending": 0, "processing": 0, "done": 0, "skipped": 0}
    try:
        cur = await conn.execute(
            "SELECT status, COUNT(*) FROM reading_queue GROUP BY status"
        )
        for row in await cur.fetchall():
            if row[0] in out:
                out[row[0]] = int(row[1])
        await cur.close()
    except Exception as e:
        logger.warning("R-F661 stats: select failed: %s", e)
    return out
