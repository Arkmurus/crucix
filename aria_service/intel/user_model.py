"""R-F617 — Per-user mental model.

Theory-of-Mind foundation for the dialogue spec v2.1. Tracks what
ARIA knows about each team member so conversations adapt to the
reader, not the schema.

Why this exists
───────────────
A chatbot has no mental model of who it's talking to. A teammate
does. Antonio gets terse, deal-focused responses; a junior BD
analyst gets more explanation. The Saudi-contact question goes to
whoever has the GAMI relationship, not the operator by default.
That's the whole point.

Data model
──────────
Stored in /data/aria_dialogue.db (same file as R-F616 dialogue_state)
under a separate `user_model` table so the two stores share lifespan
+ backup discipline.

    user_id                TEXT PRIMARY KEY  — WA number / web userId
    display_name           TEXT              — first name preferred
    role                   TEXT              — operator | bd | analyst | technical
    domain_strengths       TEXT (JSON list)  — ISO2 codes / topic tags
    active_deals           TEXT (JSON list)  — deal IDs they're working
    preferred_channel      TEXT              — wa_dm | wa_group | web | email
    tone_preference        TEXT              — short | detailed | formal | informal
    last_seen_active       REAL              — unix seconds
    failure_log            TEXT (JSON list)  — ARIA's past mistakes with this user
    recency_window         TEXT (JSON list)  — facts heard <7d ago (anti-repeat)
    max_initiative         INTEGER           — clamp 0..4 (see spec §IV)
    created_at             REAL
    updated_at             REAL

Public API
──────────
    get_user(user_id)                       → dict | None
    upsert_user(user_id, **fields)          → bool
    touch_active(user_id)                   → bool
    add_domain_strength(user_id, iso2_or_tag) → bool
    record_failure(user_id, summary)        → bool
    add_to_recency_window(user_id, fact_key) → bool
    has_heard_recently(user_id, fact_key, max_age_seconds=604800) → bool
    best_user_for_domain(iso2_or_tag)       → user_id | None
    active_in_last(seconds=86400)           → list[user_id]
    list_all_users(limit=200)               → list[dict]
    stats()                                 → dict

Fail-open
─────────
Same discipline as dialogue_state: errors log + return safe defaults,
never raise. A user_model bug must NEVER break a chat turn.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger("aria.intel.user_model")

# R-F1166 — wire to brain on user model operations
from .engine_wiring import wire_success, wire_failure

# Path defaults to the same DB as dialogue_state so lifespan/backup
# discipline is shared. Overridable for tests.
_DEFAULT_DB = "/data/aria_dialogue.db"

_conn = None
_init_lock: asyncio.Lock | None = None
_schema_ready = False


def _resolve_db_path() -> Path:
    raw = (os.getenv("ARIA_DIALOGUE_DB_PATH", _DEFAULT_DB) or _DEFAULT_DB).strip()
    p = Path(raw)
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS user_model (
    user_id            TEXT PRIMARY KEY,
    display_name       TEXT DEFAULT '',
    role               TEXT DEFAULT 'unknown',
    domain_strengths   TEXT NOT NULL DEFAULT '[]',
    active_deals       TEXT NOT NULL DEFAULT '[]',
    preferred_channel  TEXT DEFAULT 'web',
    tone_preference    TEXT DEFAULT 'short',
    last_seen_active   REAL,
    failure_log        TEXT NOT NULL DEFAULT '[]',
    recency_window     TEXT NOT NULL DEFAULT '[]',
    max_initiative     INTEGER NOT NULL DEFAULT 2,
    created_at         REAL NOT NULL,
    updated_at         REAL NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_um_role ON user_model(role);
CREATE INDEX IF NOT EXISTS idx_um_active ON user_model(last_seen_active);
"""


async def _ensure_conn():
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
            await _conn.execute("PRAGMA journal_mode=WAL")
            await _conn.execute("PRAGMA synchronous=NORMAL")
        for stmt in _SCHEMA_SQL.split(";"):
            stmt = stmt.strip()
            if stmt:
                await _conn.execute(stmt)
        await _conn.commit()
        _schema_ready = True
        return _conn


async def _reset_for_tests() -> None:
    """Test helper — drop connection so next call re-opens."""
    global _conn, _schema_ready, _init_lock
    if _conn is not None:
        try:
            await _conn.close()
        except Exception:
            pass
    _conn = None
    _schema_ready = False
    _init_lock = None


# ── Row helpers ──────────────────────────────────────────────────────


def _decode_json_field(raw: Any, fallback: list | dict | None = None) -> Any:
    """Defensive JSON decode — returns fallback on failure."""
    if raw is None:
        return fallback if fallback is not None else []
    if isinstance(raw, (list, dict)):
        return raw
    try:
        return json.loads(raw)
    except Exception:
        return fallback if fallback is not None else []


def _row_to_user(row) -> dict[str, Any]:
    if row is None:
        return {}
    out = {k: row[k] for k in row.keys()}
    # Decode JSON fields
    for f in ("domain_strengths", "active_deals", "failure_log", "recency_window"):
        out[f] = _decode_json_field(out.get(f), [])
    return out


# ── Public API — write paths ─────────────────────────────────────────


async def upsert_user(
    user_id: str,
    *,
    display_name: Optional[str] = None,
    role: Optional[str] = None,
    preferred_channel: Optional[str] = None,
    tone_preference: Optional[str] = None,
    max_initiative: Optional[int] = None,
) -> bool:
    """Create a user row if absent, else update the named fields.

    Only the provided kwargs overwrite. List fields (domain_strengths,
    active_deals, failure_log, recency_window) are not touched by
    upsert — use the dedicated append helpers.
    """
    if not user_id:
        return False
    try:
        conn = await _ensure_conn()
    except Exception:
        return False
    now = time.time()
    try:
        # Check if user exists
        cur = await conn.execute(
            "SELECT 1 FROM user_model WHERE user_id = ?", (user_id,)
        )
        exists = (await cur.fetchone()) is not None
        await cur.close()
        if not exists:
            await conn.execute(
                "INSERT INTO user_model ("
                "user_id, display_name, role, preferred_channel, "
                "tone_preference, max_initiative, created_at, updated_at"
                ") VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    user_id,
                    display_name or "",
                    role or "unknown",
                    preferred_channel or "web",
                    tone_preference or "short",
                    int(max_initiative) if max_initiative is not None else 2,
                    now, now,
                ),
            )
            await conn.commit()
            return True
        # Update only provided fields
        sets: list[str] = []
        vals: list[Any] = []
        for col, val in (
            ("display_name", display_name),
            ("role", role),
            ("preferred_channel", preferred_channel),
            ("tone_preference", tone_preference),
        ):
            if val is not None:
                sets.append(f"{col} = ?")
                vals.append(val)
        if max_initiative is not None:
            sets.append("max_initiative = ?")
            vals.append(int(max_initiative))
        if not sets:
            return True  # nothing to update
        sets.append("updated_at = ?")
        vals.append(now)
        vals.append(user_id)
        await conn.execute(
            f"UPDATE user_model SET {', '.join(sets)} WHERE user_id = ?",
            tuple(vals),
        )
        await conn.commit()
        return True
    except Exception as exc:
        logger.warning("upsert_user(%s): failed: %s", user_id, exc)
        return False


async def touch_active(user_id: str) -> bool:
    """Mark user as recently active (now). Used to power
    `active_in_last()` queries for routing decisions."""
    if not user_id:
        return False
    try:
        conn = await _ensure_conn()
    except Exception:
        return False
    now = time.time()
    try:
        # Upsert the user if they don't exist yet (chat audit may
        # surface a new user_id before any explicit upsert_user call)
        cur = await conn.execute(
            "SELECT 1 FROM user_model WHERE user_id = ?", (user_id,)
        )
        exists = (await cur.fetchone()) is not None
        await cur.close()
        if not exists:
            await conn.execute(
                "INSERT INTO user_model (user_id, created_at, updated_at, last_seen_active) "
                "VALUES (?, ?, ?, ?)",
                (user_id, now, now, now),
            )
        else:
            await conn.execute(
                "UPDATE user_model SET last_seen_active = ?, updated_at = ? "
                "WHERE user_id = ?",
                (now, now, user_id),
            )
        await conn.commit()
        return True
    except Exception as exc:
        logger.warning("touch_active(%s): failed: %s", user_id, exc)
        return False


async def _append_to_json_list(
    user_id: str, column: str, value: Any, max_len: int = 50
) -> bool:
    """Generic helper: read current JSON list from `column`, append
    value, cap length to `max_len`, write back."""
    if not user_id or value is None:
        return False
    try:
        conn = await _ensure_conn()
    except Exception:
        return False
    now = time.time()
    try:
        cur = await conn.execute(
            f"SELECT {column} FROM user_model WHERE user_id = ?",
            (user_id,),
        )
        row = await cur.fetchone()
        await cur.close()
        if row is None:
            # Create user lazily
            await upsert_user(user_id)
            current: list = []
        else:
            current = _decode_json_field(row[column], [])
            if not isinstance(current, list):
                current = []
        # Avoid duplicates for simple scalar values
        if value not in current:
            current.append(value)
        # Cap to most-recent N
        if len(current) > max_len:
            current = current[-max_len:]
        await conn.execute(
            f"UPDATE user_model SET {column} = ?, updated_at = ? "
            f"WHERE user_id = ?",
            (json.dumps(current), now, user_id),
        )
        await conn.commit()
        return True
    except Exception as exc:
        logger.warning("append %s for %s: failed: %s", column, user_id, exc)
        return False


async def add_domain_strength(user_id: str, iso2_or_tag: str) -> bool:
    """Add a domain strength (ISO2 country code or topic tag). Used
    by the AskItem router (Phase 6) to pick who to ask."""
    if not iso2_or_tag:
        return False
    return await _append_to_json_list(
        user_id, "domain_strengths", iso2_or_tag.strip().upper()
    )


async def add_active_deal(user_id: str, deal_id: str) -> bool:
    """Record that a user is working on a specific deal."""
    if not deal_id:
        return False
    return await _append_to_json_list(user_id, "active_deals", deal_id)


async def record_failure(user_id: str, summary: str) -> bool:
    """Append a failure log entry — past mistakes with this user.
    Cap at 50 entries (most recent retained). Anti-repeat: ARIA reads
    this BEFORE responding to that user."""
    if not summary:
        return False
    entry = {"ts": time.time(), "summary": summary[:240]}
    return await _append_to_json_list(user_id, "failure_log", entry, max_len=50)


async def add_to_recency_window(user_id: str, fact_key: str) -> bool:
    """Record that this user has heard a given fact recently.
    R-F619 reads this to avoid re-explaining."""
    if not fact_key:
        return False
    entry = {"ts": time.time(), "key": fact_key[:120]}
    return await _append_to_json_list(user_id, "recency_window", entry, max_len=100)


# ── Public API — read paths ──────────────────────────────────────────


async def get_user(user_id: str) -> Optional[dict[str, Any]]:
    """Fetch the full user row. Returns None if user not yet known."""
    if not user_id:
        return None
    try:
        conn = await _ensure_conn()
    except Exception:
        return None
    try:
        cur = await conn.execute(
            "SELECT * FROM user_model WHERE user_id = ?", (user_id,)
        )
        row = await cur.fetchone()
        await cur.close()
        return _row_to_user(row) if row else None
    except Exception as exc:
        logger.warning("get_user(%s): failed: %s", user_id, exc)


        return None


async def has_heard_recently(
    user_id: str, fact_key: str, *, max_age_seconds: float = 7 * 24 * 3600,
) -> bool:
    """R-F619 anti-repeat helper. True if `fact_key` is in the user's
    recency_window within `max_age_seconds` (default 7 days)."""
    if not user_id or not fact_key:
        return False
    user = await get_user(user_id)
    if not user:
        return False
    cutoff = time.time() - max_age_seconds
    window = user.get("recency_window") or []
    for entry in window:
        try:
            if isinstance(entry, dict) and entry.get("key") == fact_key:
                ts = float(entry.get("ts", 0) or 0)
                if ts >= cutoff:
                    return True
        except Exception:
            continue
    return False


async def best_user_for_domain(iso2_or_tag: str) -> Optional[str]:
    """Return the user_id with the named domain strength who was
    most recently active. None if no user has the strength."""
    if not iso2_or_tag:
        return None
    tag = iso2_or_tag.strip().upper()
    try:
        conn = await _ensure_conn()
    except Exception:
        return None
    try:
        # We can't filter inside JSON via SQLite without JSON1.
        # Scan all users (small N) and filter in Python.
        cur = await conn.execute(
            "SELECT user_id, domain_strengths, last_seen_active "
            "FROM user_model "
            "ORDER BY last_seen_active DESC NULLS LAST"
        )
        rows = await cur.fetchall()
        await cur.close()
        for row in rows:
            strengths = _decode_json_field(row["domain_strengths"], [])
            if tag in strengths:
                return row["user_id"]
        return None
    except Exception as exc:
        logger.warning("best_user_for_domain(%s): failed: %s", tag, exc)
        return None


async def active_in_last(seconds: float = 86400) -> list[str]:
    """Return user_ids whose last_seen_active is within `seconds` ago.

    Phase 4 (R-F624 autonomous push) uses this to decide who to ping.
    """
    cutoff = time.time() - seconds
    try:
        conn = await _ensure_conn()
    except Exception:
        return []
    try:
        cur = await conn.execute(
            "SELECT user_id FROM user_model "
            "WHERE last_seen_active IS NOT NULL AND last_seen_active >= ? "
            "ORDER BY last_seen_active DESC",
            (cutoff,),
        )
        rows = await cur.fetchall()
        await cur.close()
        return [r["user_id"] for r in rows]
    except Exception as exc:
        logger.warning("active_in_last(%s): failed: %s", seconds, exc)
        return []


async def list_all_users(limit: int = 200) -> list[dict[str, Any]]:
    """All users in the model. For dashboards / debugging."""
    try:
        conn = await _ensure_conn()
    except Exception:
        return []
    try:
        cur = await conn.execute(
            "SELECT * FROM user_model "
            "ORDER BY last_seen_active DESC NULLS LAST "
            "LIMIT ?",
            (max(1, min(limit, 500)),),
        )
        rows = await cur.fetchall()
        await cur.close()
        return [_row_to_user(r) for r in rows]
    except Exception as exc:
        logger.warning("list_all_users: failed: %s", exc)
        return []


async def stats() -> dict[str, Any]:
    """Dashboard helper."""
    try:
        conn = await _ensure_conn()
    except Exception:
        return {"error": "store_unavailable"}
    out: dict[str, Any] = {
        "total_users": 0,
        "by_role": {},
        "active_24h": 0,
        "active_7d": 0,
    }
    try:
        cur = await conn.execute("SELECT COUNT(*) AS n FROM user_model")
        row = await cur.fetchone()
        out["total_users"] = int(row["n"]) if row else 0
        await cur.close()
        cur = await conn.execute(
            "SELECT role, COUNT(*) AS n FROM user_model GROUP BY role"
        )
        for row in await cur.fetchall():
            out["by_role"][row["role"] or "unknown"] = int(row["n"])
        await cur.close()
        now = time.time()
        for label, window in (("active_24h", 86400), ("active_7d", 7 * 86400)):
            cur = await conn.execute(
                "SELECT COUNT(*) AS n FROM user_model "
                "WHERE last_seen_active IS NOT NULL AND last_seen_active >= ?",
                (now - window,),
            )
            row = await cur.fetchone()
            out[label] = int(row["n"]) if row else 0
            await cur.close()
    except Exception as exc:
        logger.warning("stats: failed: %s", exc)
        wire_failure(
            module="user_model",
            detail=f"stats failed: {exc}",
            gap_type="user_model_failure",
            source="user_model:stats",
        )
        out["error"] = str(exc)[:120]
    # R-F1166 — wire success to brain
    if "error" not in out:
        wire_success(
            module="user_model",
            summary=f"User model stats: {out['total_users']} users, {out['active_24h']} active 24h",
            source_id="user_model:stats",
        )
    return out
