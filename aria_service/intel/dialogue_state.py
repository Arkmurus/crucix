"""R-F616 — dialogue_state SQLite store.

Per-user, per-goal open-question tracking. Foundation for Phase 2+
chase logic (R-F618 chase ladder, R-F624 autonomous push,
R-F629 DD chase scheduler).

Schema discipline
─────────────────
Dedicated SQLite file `/data/aria_dialogue.db` so the dialogue
state survives lifespan restarts and isolates from the generic
state_store table. Path is overridable via `ARIA_DIALOGUE_DB_PATH`
for tests + alternate deployments.

Data model
──────────
One row per ARIA-initiated question (or human-initiated tracked
ask). Lifecycle:
    open → answered      (user replied)
    open → chasing       (deadline passed, ARIA pinged again)
    open → expired       (closes_at reached without reply)
    open → closed        (operator manually closed / accepted residual)

Field semantics
───────────────
    user_id          who ARIA asked (the team member, NOT operator
                     unless operator is the addressee)
    goal_id          optional — links to a DDGoal (Phase 6) or any
                     other GoalContext that scoped the question
    channel          'wa_group' | 'wa_dm' | 'web' | 'email'
    question_text    the literal question ARIA emitted
    context_summary  short 'why I asked' note for audit log
    asked_at         unix seconds — never null
    expected_by      unix seconds, optional — when ARIA chases
    status           'open' | 'chasing' | 'answered' | 'expired' | 'closed'
    answered_at      unix seconds, set when status flips to answered
    answer_text      raw user reply that resolved the question
    chase_count      how many chase attempts have fired
    last_chased_at   unix seconds of the most recent chase
    closed_at        unix seconds when status flipped to closed/expired
    closed_by        user_id who closed (or 'aria' for auto-expire)
    closed_reason    'answered' | 'operator_accepted_residual' |
                     'expired' | 'duplicate' | 'no_longer_relevant'
    related_ask_id   optional — link to a Phase 6 DDGoal.AskItem id

Public API
──────────
    open_question(...)               → question_id
    mark_answered(qid, answer, by)   → bool
    mark_chased(qid)                 → bool
    mark_closed(qid, by, reason)     → bool
    get(qid)                         → dict | None
    list_open_for_user(user_id)      → list[dict]
    list_due_for_chase()             → list[dict]
    list_all_open(limit=100)         → list[dict]
    stats()                          → dict

Concurrency
───────────
aiosqlite serialises through one worker thread; single-statement
operations don't need extra locking. The store is fail-open: errors
log + return safe defaults rather than raising, so a dialogue
infrastructure bug NEVER breaks a chat turn.
"""
from __future__ import annotations

import asyncio
import logging
import os
import time
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger("aria.intel.dialogue_state")

# ─────────────────────────────────────────────────────────────────────
# Configuration
# ─────────────────────────────────────────────────────────────────────

# Resolve DB path lazily so tests can override via env var BEFORE the
# first connection. Default is /data/aria_dialogue.db on fly + seenode
# (where /data is the persistent volume mount).
_DEFAULT_DB = "/data/aria_dialogue.db"

_conn = None  # aiosqlite.Connection — lazy init
_init_lock: asyncio.Lock | None = None
_schema_ready: bool = False


def _resolve_db_path() -> Path:
    """Return the DB path, honouring ARIA_DIALOGUE_DB_PATH override.

    Tests set this to a temp dir; prod uses /data/aria_dialogue.db.
    """
    raw = os.getenv("ARIA_DIALOGUE_DB_PATH", _DEFAULT_DB).strip() or _DEFAULT_DB
    p = Path(raw)
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


# ─────────────────────────────────────────────────────────────────────
# Schema
# ─────────────────────────────────────────────────────────────────────

_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS dialogue_questions (
    question_id      INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id          TEXT NOT NULL,
    goal_id          TEXT,
    channel          TEXT NOT NULL DEFAULT 'web',
    question_text    TEXT NOT NULL,
    context_summary  TEXT DEFAULT '',
    asked_at         REAL NOT NULL,
    expected_by      REAL,
    status           TEXT NOT NULL DEFAULT 'open',
    answered_at      REAL,
    answer_text      TEXT,
    chase_count      INTEGER NOT NULL DEFAULT 0,
    last_chased_at   REAL,
    closed_at        REAL,
    closed_by        TEXT,
    closed_reason    TEXT,
    related_ask_id   TEXT
);

CREATE INDEX IF NOT EXISTS idx_dq_user_status
    ON dialogue_questions(user_id, status);

CREATE INDEX IF NOT EXISTS idx_dq_status_due
    ON dialogue_questions(status, expected_by);

CREATE INDEX IF NOT EXISTS idx_dq_goal
    ON dialogue_questions(goal_id);
"""


async def _ensure_conn():
    """Lazy-init the aiosqlite connection + schema. Idempotent."""
    global _conn, _init_lock, _schema_ready
    if _conn is not None and _schema_ready:
        return _conn
    if _init_lock is None:
        _init_lock = asyncio.Lock()
    async with _init_lock:
        if _conn is not None and _schema_ready:
            return _conn
        try:
            import aiosqlite
        except Exception as exc:
            logger.warning("aiosqlite import failed: %s", exc)
            raise
        if _conn is None:
            db_path = _resolve_db_path()
            _conn = await aiosqlite.connect(str(db_path))
            _conn.row_factory = aiosqlite.Row
            # Pragmas for write durability + reasonable performance
            await _conn.execute("PRAGMA journal_mode=WAL")
            await _conn.execute("PRAGMA synchronous=NORMAL")
        # Run schema (idempotent)
        for stmt in _SCHEMA_SQL.split(";"):
            stmt = stmt.strip()
            if stmt:
                await _conn.execute(stmt)
        await _conn.commit()
        _schema_ready = True
        return _conn


async def _reset_for_tests() -> None:
    """Test helper — drop the connection so the next call re-opens.

    Used by pytest fixtures that monkeypatch ARIA_DIALOGUE_DB_PATH
    between test runs.
    """
    global _conn, _schema_ready, _init_lock
    if _conn is not None:
        try:
            await _conn.close()
        except Exception:
            pass
    _conn = None
    _schema_ready = False
    _init_lock = None


# ─────────────────────────────────────────────────────────────────────
# Row helpers
# ─────────────────────────────────────────────────────────────────────


def _row_to_dict(row) -> dict[str, Any]:
    """aiosqlite.Row → plain dict for callers."""
    if row is None:
        return {}
    return {k: row[k] for k in row.keys()}


# ─────────────────────────────────────────────────────────────────────
# Public API — write paths
# ─────────────────────────────────────────────────────────────────────


async def open_question(
    *,
    user_id: str,
    question_text: str,
    goal_id: Optional[str] = None,
    channel: str = "web",
    context_summary: str = "",
    expected_by: Optional[float] = None,
    related_ask_id: Optional[str] = None,
) -> Optional[int]:
    """Open a new dialogue question. Returns the question_id, or None
    on failure (logged; never raises)."""
    if not user_id or not question_text:
        logger.warning("open_question: user_id + question_text required")
        return None
    try:
        conn = await _ensure_conn()
    except Exception as exc:
        logger.warning("open_question: ensure_conn failed: %s", exc)
        return None
    now = time.time()
    try:
        cur = await conn.execute(
            "INSERT INTO dialogue_questions ("
            "user_id, goal_id, channel, question_text, context_summary, "
            "asked_at, expected_by, status, related_ask_id"
            ") VALUES (?, ?, ?, ?, ?, ?, ?, 'open', ?)",
            (
                user_id, goal_id, channel, question_text, context_summary,
                now, expected_by, related_ask_id,
            ),
        )
        await conn.commit()
        qid = cur.lastrowid
        await cur.close()
        return qid
    except Exception as exc:
        logger.warning("open_question: insert failed: %s", exc)

        return None


async def mark_answered(
    question_id: int,
    *,
    answer_text: str,
    by_user_id: str = "",
) -> bool:
    """Flip status open|chasing → answered. Idempotent — re-marking an
    already-answered question is a no-op that returns True."""
    if not question_id:
        return False
    try:
        conn = await _ensure_conn()
    except Exception:
        return False
    now = time.time()
    try:
        await conn.execute(
            "UPDATE dialogue_questions "
            "SET status = 'answered', answered_at = ?, answer_text = ?, "
            "closed_at = ?, closed_by = ?, closed_reason = 'answered' "
            "WHERE question_id = ? AND status IN ('open','chasing','answered')",
            (now, answer_text, now, by_user_id or "", question_id),
        )
        await conn.commit()
        return True
    except Exception as exc:
        logger.warning("mark_answered: failed: %s", exc)
        return False


async def mark_chased(question_id: int) -> bool:
    """Increment chase_count + flip status to 'chasing'. Used by the
    R-F629 chase scheduler when a soft-chase fires."""
    if not question_id:
        return False
    try:
        conn = await _ensure_conn()
    except Exception:
        return False
    now = time.time()
    try:
        await conn.execute(
            "UPDATE dialogue_questions "
            "SET status = 'chasing', "
            "chase_count = chase_count + 1, "
            "last_chased_at = ? "
            "WHERE question_id = ? AND status IN ('open','chasing')",
            (now, question_id),
        )
        await conn.commit()
        return True
    except Exception as exc:
        logger.warning("mark_chased: failed: %s", exc)
        return False


async def mark_closed(
    question_id: int,
    *,
    by_user_id: str = "aria",
    reason: str = "expired",
) -> bool:
    """Manually close a question (operator override, expired, duplicate,
    no_longer_relevant). Does NOT set answer_text — that's only for
    actually-answered questions."""
    if not question_id:
        return False
    try:
        conn = await _ensure_conn()
    except Exception:
        return False
    now = time.time()
    try:
        await conn.execute(
            "UPDATE dialogue_questions "
            "SET status = 'closed', closed_at = ?, "
            "closed_by = ?, closed_reason = ? "
            "WHERE question_id = ?",
            (now, by_user_id or "aria", reason, question_id),
        )
        await conn.commit()
        return True
    except Exception as exc:
        logger.warning("mark_closed: failed: %s", exc)
        return False


# ─────────────────────────────────────────────────────────────────────
# Public API — read paths
# ─────────────────────────────────────────────────────────────────────


async def get(question_id: int) -> Optional[dict[str, Any]]:
    """Fetch one question by id."""
    if not question_id:
        return None
    try:
        conn = await _ensure_conn()
    except Exception:
        return None
    try:
        cur = await conn.execute(
            "SELECT * FROM dialogue_questions WHERE question_id = ?",
            (question_id,),
        )
        row = await cur.fetchone()
        await cur.close()
        return _row_to_dict(row) if row else None
    except Exception as exc:
        logger.warning("get: failed: %s", exc)
        return None


async def list_open_for_user(user_id: str, limit: int = 20) -> list[dict[str, Any]]:
    """All open / chasing questions for a given user, oldest first."""
    if not user_id:
        return []
    try:
        conn = await _ensure_conn()
    except Exception:
        return []
    try:
        cur = await conn.execute(
            "SELECT * FROM dialogue_questions "
            "WHERE user_id = ? AND status IN ('open','chasing') "
            "ORDER BY asked_at ASC LIMIT ?",
            (user_id, max(1, min(limit, 200))),
        )
        rows = await cur.fetchall()
        await cur.close()
        return [_row_to_dict(r) for r in rows]
    except Exception as exc:
        logger.warning("list_open_for_user: failed: %s", exc)
        return []


async def list_due_for_chase(now: Optional[float] = None) -> list[dict[str, Any]]:
    """Questions in 'open' status whose expected_by has elapsed.

    Used by R-F629 chase scheduler. Returns oldest-first so chases
    fire predictably.
    """
    now = now if now is not None else time.time()
    try:
        conn = await _ensure_conn()
    except Exception:
        return []
    try:
        cur = await conn.execute(
            "SELECT * FROM dialogue_questions "
            "WHERE status = 'open' "
            "AND expected_by IS NOT NULL AND expected_by <= ? "
            "ORDER BY expected_by ASC",
            (now,),
        )
        rows = await cur.fetchall()
        await cur.close()
        return [_row_to_dict(r) for r in rows]
    except Exception as exc:
        logger.warning("list_due_for_chase: failed: %s", exc)
        return []


async def list_all_open(limit: int = 100) -> list[dict[str, Any]]:
    """All open/chasing questions across all users."""
    try:
        conn = await _ensure_conn()
    except Exception:
        return []
    try:
        cur = await conn.execute(
            "SELECT * FROM dialogue_questions "
            "WHERE status IN ('open','chasing') "
            "ORDER BY asked_at ASC LIMIT ?",
            (max(1, min(limit, 500)),),
        )
        rows = await cur.fetchall()
        await cur.close()
        return [_row_to_dict(r) for r in rows]
    except Exception as exc:
        logger.warning("list_all_open: failed: %s", exc)
        return []


async def stats() -> dict[str, Any]:
    """Dashboard helper — counts per status + chase histogram."""
    try:
        conn = await _ensure_conn()
    except Exception:
        return {"error": "store_unavailable"}
    out: dict[str, Any] = {
        "total": 0,
        "by_status": {},
        "max_chase_count": 0,
        "avg_resolution_seconds": None,
    }
    try:
        cur = await conn.execute(
            "SELECT status, COUNT(*) AS n FROM dialogue_questions GROUP BY status"
        )
        for row in await cur.fetchall():
            out["by_status"][row["status"]] = int(row["n"])
            out["total"] += int(row["n"])
        await cur.close()
        cur = await conn.execute(
            "SELECT MAX(chase_count) AS m FROM dialogue_questions"
        )
        row = await cur.fetchone()
        if row and row["m"] is not None:
            out["max_chase_count"] = int(row["m"])
        await cur.close()
        cur = await conn.execute(
            "SELECT AVG(answered_at - asked_at) AS avg_s "
            "FROM dialogue_questions WHERE status = 'answered' "
            "AND answered_at IS NOT NULL"
        )
        row = await cur.fetchone()
        if row and row["avg_s"] is not None:
            out["avg_resolution_seconds"] = float(row["avg_s"])
        await cur.close()
    except Exception as exc:
        logger.warning("stats: failed: %s", exc)
        out["error"] = str(exc)[:120]
    return out
