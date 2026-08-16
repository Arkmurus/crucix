"""bookmarks — R-F663 controller bookmarks + persistent state.

R-F663 (2026-05-17). Final slice of the OSS-only learning-framework
buildout. **Phase B** (operator override granted 2026-05-17).

Why this exists
═══════════════
The Phase B controller (R-F662) is stateless per cycle. Each call to
run_cycle() re-picks candidates from FSRS-due + reading_queue and
re-reads the same top-K chunks from RAG. That's fine for correctness
but wastes work: a topic studied 10 minutes ago doesn't need another
full pass.

Bookmarks add a thin per-topic state layer so the controller can:
  - Skip topics studied within the last N seconds (debounce)
  - See how many cycles a topic has been through
  - See cumulative chunks_seen (a coarse "is this topic exhausted?"
    signal — once ARIA has read 200 chunks about Rostec, additional
    cycles probably aren't going to shift the EWMA much)

Storage
═══════
SQLite via aiosqlite, mirroring reading_queue.py. Survives restart,
no Redis, no third-party. Schema:

  controller_bookmarks(
    topic            TEXT PRIMARY KEY,
    last_studied_at  REAL NOT NULL,
    cycles_n         INTEGER NOT NULL DEFAULT 0,
    chunks_seen      INTEGER NOT NULL DEFAULT 0,
    last_agreement   REAL,
    notes            TEXT
  )

Single row per topic — last writer wins. The controller calls
`record_bookmark(topic, chunks_count, agreement)` at the end of every
study_topic. Read via `get_bookmark(topic)` or `list_recent(limit)`.

Public surface
══════════════
    async record_bookmark(topic, *, chunks_count, agreement, notes="") -> bool
    async get_bookmark(topic) -> dict | None
    async list_recent(limit=50) -> list[dict]
    async since_last_studied(topic) -> float | None
        Seconds since last study event, or None if never.
"""
from __future__ import annotations

import logging
import os
import time
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger("aria.learning.bookmarks")

# R-F4052 (C-108) — §21a wiring for this module's WORK, not its import.
#
# The old R-F1319 block fired wire_success at IMPORT time under
# "learning.bookmarks" — a name brain_hook._MODULE_TOPICS does not contain, so it
# was invisible to `never_seen` (C-104) and proved only that the file had been
# imported. Outcomes are now reported under the REGISTERED name, on BOTH
# branches. Success is throttled because this module's work runs per item.


def _wire_bookmarks(ok: bool, summary: str) -> None:
    """Report a real outcome to the brain. Never raises."""
    try:
        from aria_service.intel.engine_wiring import (
            wire_failure, wire_success_throttled,
        )
        if ok:
            wire_success_throttled(
                "bookmarks", summary, source_id="bookmarks:work",
            )
        else:
            wire_failure(
                module="bookmarks", detail=summary,
                gap_type="engine_failure", source="bookmarks:work",
            )
    except Exception as _exc:   # pragma: no cover - telemetry never blocks
        # Swallowed deliberately: brain wiring must never break this
        # module's work. Logged rather than `pass`ed so the wiring
        # failure is not itself dark (bandit B110) — wiring the wiring
        # failure would be circular.
        logger.debug("[R-F4052] bookmarks outcome wiring failed: %s", _exc)


_DB_PATH_ENV = "ARIA_BOOKMARKS_DB_PATH"
_DEFAULT_DB_PATH = "/data/aria_bookmarks.db"

_conn = None


def _db_path() -> str:
    p = os.environ.get(_DB_PATH_ENV) or _DEFAULT_DB_PATH
    parent = Path(p).parent
    if not parent.exists():
        return str(Path.cwd() / "aria_bookmarks.db")
    return p


async def _ensure_conn():
    global _conn
    if _conn is not None:
        return _conn
    import aiosqlite
    path = _db_path()
    _conn = await aiosqlite.connect(path)
    await _conn.execute("""
        CREATE TABLE IF NOT EXISTS controller_bookmarks (
            topic            TEXT PRIMARY KEY,
            last_studied_at  REAL NOT NULL,
            cycles_n         INTEGER NOT NULL DEFAULT 0,
            chunks_seen      INTEGER NOT NULL DEFAULT 0,
            last_agreement   REAL,
            notes            TEXT
        )
    """)
    await _conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_bookmarks_last "
        "ON controller_bookmarks(last_studied_at DESC)"
    )
    await _conn.commit()
    logger.info("R-F663 bookmarks ready at %s", path)
    return _conn


async def _close_conn() -> None:
    global _conn
    if _conn is not None:
        try:
            await _conn.close()
        except Exception as _exc:
            # R-F4052 (C-108) — a teardown failure was silent (bandit B110).
            # Still swallowed (close must not raise on shutdown), but visible.
            logger.debug("[R-F4052] %s conn close failed: %s", "bookmarks", _exc)
    _conn = None


def _row_to_dict(row) -> dict[str, Any]:
    if not row:
        return {}
    return {
        "topic": row[0],
        "last_studied_at": row[1],
        "cycles_n": row[2],
        "chunks_seen": row[3],
        "last_agreement": row[4],
        "notes": row[5],
    }


async def record_bookmark(
    topic: str,
    *,
    chunks_count: int = 0,
    agreement: float | None = None,
    notes: str = "",
) -> bool:
    """UPSERT a bookmark row for `topic`. Increments cycles_n and
    accumulates chunks_seen. Returns True on success."""
    if not topic or not topic.strip():
        return False
    topic = topic.strip()
    try:
        conn = await _ensure_conn()
    except Exception as e:
        logger.warning("R-F663 record_bookmark ensure_conn failed: %s", e)
        return False
    now = time.time()
    try:
        await conn.execute(
            """
            INSERT INTO controller_bookmarks (
                topic, last_studied_at, cycles_n, chunks_seen,
                last_agreement, notes
            ) VALUES (?, ?, 1, ?, ?, ?)
            ON CONFLICT(topic) DO UPDATE SET
                last_studied_at = excluded.last_studied_at,
                cycles_n        = controller_bookmarks.cycles_n + 1,
                chunks_seen     = controller_bookmarks.chunks_seen
                                  + excluded.chunks_seen,
                last_agreement  = excluded.last_agreement,
                notes           = COALESCE(NULLIF(excluded.notes,''),
                                            controller_bookmarks.notes)
            """,
            (
                topic, now, max(0, int(chunks_count)),
                float(agreement) if agreement is not None else None,
                notes or "",
            ),
        )
        await conn.commit()
        _wire_bookmarks(True, f"bookmark recorded for topic {topic!r}")
        return True
    except Exception as e:
        logger.warning("R-F663 record_bookmark upsert failed: %s", e)
        _wire_bookmarks(False, f"record_bookmark failed: {type(e).__name__}: {e}")
        return False


async def get_bookmark(topic: str) -> Optional[dict]:
    if not topic:
        return None
    try:
        conn = await _ensure_conn()
    except Exception:
        return None
    try:
        cur = await conn.execute(
            "SELECT topic, last_studied_at, cycles_n, chunks_seen, "
            "last_agreement, notes "
            "FROM controller_bookmarks WHERE topic = ?",
            (topic.strip(),),
        )
        row = await cur.fetchone()
        await cur.close()
        return _row_to_dict(row) if row else None
    except Exception as e:
        logger.warning("R-F663 get_bookmark select failed: %s", e)
        return None


async def list_recent(limit: int = 50) -> list[dict]:
    """Most-recently-studied topics first."""
    try:
        conn = await _ensure_conn()
    except Exception:
        return []
    limit = max(1, min(int(limit), 500))
    try:
        cur = await conn.execute(
            "SELECT topic, last_studied_at, cycles_n, chunks_seen, "
            "last_agreement, notes FROM controller_bookmarks "
            "ORDER BY last_studied_at DESC LIMIT ?",
            (limit,),
        )
        rows = await cur.fetchall()
        await cur.close()
        return [_row_to_dict(r) for r in rows]
    except Exception as e:
        logger.warning("R-F663 list_recent select failed: %s", e)
        return []


async def since_last_studied(topic: str) -> Optional[float]:
    """Seconds since `topic` was last studied, or None if never."""
    bm = await get_bookmark(topic)
    if not bm or not bm.get("last_studied_at"):
        return None
    return max(0.0, time.time() - float(bm["last_studied_at"]))
