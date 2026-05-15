"""R-F529 — SQLite-backed retry buffer for failed Airtable syncs.

Live failure 2026-05-15 morning:
  airtable sync DROPPED action=4a19afaeb076c6cb reason=net:ConnectTimeout

The dropped action was a HIGH/operator_action notification recording a
brain-circuit-breaker trip — the single most operationally important
signal in the recovery window. The original pending_actions code path
logged a warning and moved on; the signal was silently lost from the
operator's Airtable Task Register.

R-F529 fix: when Airtable sync fails for a non-"disabled" reason,
enqueue the entry into a local SQLite buffer. A periodic drain
function (called from autonomous task + on-demand via admin endpoint)
re-tries each queued entry against Airtable; on success it's removed,
on continued failure it stays until either succeeding or hitting the
retention cap.

Never silently drop a HIGH/operator_action item.

Schema
══════
  airtable_buffer (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    action_id       TEXT NOT NULL,
    entry_json      TEXT NOT NULL,
    first_failed_at REAL NOT NULL,
    last_attempt_at REAL NOT NULL,
    attempts        INTEGER NOT NULL DEFAULT 1,
    last_reason     TEXT,
    UNIQUE(action_id)
  )

The UNIQUE constraint on action_id means re-enqueueing the same action
updates the existing row (incrementing attempts) rather than creating
duplicates — the live system can call enqueue() on every retry
without bloating the queue.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import sqlite3
import time
from contextlib import contextmanager
from typing import Any, Iterator

logger = logging.getLogger("aria.airtable_buffer")

# Cap to prevent runaway growth if Airtable is down for days. Once
# we hit the cap, the oldest entries are dropped (with an ERROR log)
# so they don't mask newer fails. Per [[aria_infinite_memory]] the
# COLD store should preserve the dropped record — see _archive() below.
_BUFFER_CAP = int(os.environ.get("ARIA_AIRTABLE_BUFFER_CAP", "1000"))


def _db_path() -> str:
    """Default: /data/airtable_buffer.db (fly volume).
    Override via ARIA_AIRTABLE_BUFFER_DB env."""
    p = os.environ.get("ARIA_AIRTABLE_BUFFER_DB", "").strip()
    if p:
        return p
    if os.path.isdir("/data"):
        return "/data/airtable_buffer.db"
    return os.path.join(os.path.dirname(__file__), "_local_airtable_buffer.db")


_SCHEMA = """
CREATE TABLE IF NOT EXISTS airtable_buffer (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    action_id       TEXT NOT NULL,
    entry_json      TEXT NOT NULL,
    first_failed_at REAL NOT NULL,
    last_attempt_at REAL NOT NULL,
    attempts        INTEGER NOT NULL DEFAULT 1,
    last_reason     TEXT,
    UNIQUE(action_id)
);
CREATE INDEX IF NOT EXISTS idx_atb_first_failed ON airtable_buffer(first_failed_at);

CREATE TABLE IF NOT EXISTS airtable_buffer_archive (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    action_id       TEXT NOT NULL,
    entry_json      TEXT NOT NULL,
    first_failed_at REAL NOT NULL,
    archived_at     REAL NOT NULL,
    attempts        INTEGER NOT NULL,
    last_reason     TEXT,
    archive_reason  TEXT   -- 'cap_exceeded' | 'manual_purge' | ...
);
"""


@contextmanager
def _connect() -> Iterator[sqlite3.Connection]:
    path = _db_path()
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.executescript(_SCHEMA)
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def enqueue(entry: dict, reason: str = "") -> dict:
    """Buffer a single Airtable-sync failure for later retry.

    Returns {ok: True, action_id, attempts} on success, or
    {ok: False, error} if the entry has no action_id or persisting
    failed.

    Idempotent: re-enqueueing the same action_id updates the existing
    row (attempts++, last_attempt_at=now, last_reason refreshed)."""
    aid = (entry or {}).get("action_id") or ""
    if not aid:
        return {"ok": False, "error": "no_action_id"}
    now = time.time()
    try:
        body = json.dumps(entry, default=str)[:50000]  # cap at 50KB
    except (TypeError, ValueError) as e:
        return {"ok": False, "error": f"unserialisable_entry: {e}"}

    with _connect() as conn:
        cur = conn.cursor()
        # Cap enforcement BEFORE insert — archive the oldest if at cap.
        cur.execute("SELECT COUNT(*) FROM airtable_buffer")
        n = int(cur.fetchone()[0])
        if n >= _BUFFER_CAP:
            _archive_oldest(cur, n - _BUFFER_CAP + 1, reason="cap_exceeded")
        cur.execute(
            """
            INSERT INTO airtable_buffer
              (action_id, entry_json, first_failed_at, last_attempt_at,
               attempts, last_reason)
            VALUES (?, ?, ?, ?, 1, ?)
            ON CONFLICT(action_id) DO UPDATE SET
              entry_json      = excluded.entry_json,
              last_attempt_at = excluded.last_attempt_at,
              attempts        = airtable_buffer.attempts + 1,
              last_reason     = excluded.last_reason
            """,
            (aid, body, now, now, reason[:200]),
        )
        cur.execute("SELECT attempts FROM airtable_buffer WHERE action_id = ?", (aid,))
        attempts_row = cur.fetchone()
        attempts = int(attempts_row[0]) if attempts_row else 1
    logger.warning(
        "[airtable_buffer] enqueued action=%s reason=%s attempts=%d "
        "(R-F529 — will retry on next drain)",
        aid, (reason or "")[:120], attempts,
    )
    return {"ok": True, "action_id": aid, "attempts": attempts}


def _archive_oldest(cur: sqlite3.Cursor, n: int, *, reason: str) -> None:
    """Move the oldest n rows to airtable_buffer_archive. The active
    buffer rotates; the archive grows monotonically for forensic
    audit (per [[aria_infinite_memory]] — never delete operational
    data even when capping the hot queue)."""
    now = time.time()
    cur.execute(
        """
        SELECT id, action_id, entry_json, first_failed_at, attempts, last_reason
        FROM airtable_buffer ORDER BY first_failed_at ASC LIMIT ?
        """,
        (n,),
    )
    rows = cur.fetchall()
    for r in rows:
        cur.execute(
            """
            INSERT INTO airtable_buffer_archive
              (action_id, entry_json, first_failed_at, archived_at,
               attempts, last_reason, archive_reason)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (r[1], r[2], r[3], now, r[4], r[5], reason),
        )
        cur.execute("DELETE FROM airtable_buffer WHERE id = ?", (r[0],))
    if rows:
        logger.error(
            "[airtable_buffer] archived %d oldest entries (reason=%s) — "
            "buffer cap %d reached. Forensic copy in airtable_buffer_archive.",
            len(rows), reason, _BUFFER_CAP,
        )


def list_pending(limit: int = 50) -> list[dict]:
    """Return queued items oldest-first. Operator-facing diagnostic."""
    with _connect() as conn:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT action_id, first_failed_at, last_attempt_at, attempts,
                   last_reason, entry_json
            FROM airtable_buffer
            ORDER BY first_failed_at ASC
            LIMIT ?
            """,
            (limit,),
        )
        rows = cur.fetchall()
    out: list[dict] = []
    for r in rows:
        try:
            entry = json.loads(r[5])
        except json.JSONDecodeError:
            entry = {}
        out.append({
            "action_id": r[0],
            "first_failed_at": r[1],
            "last_attempt_at": r[2],
            "attempts": r[3],
            "last_reason": r[4] or "",
            "promise_preview": (entry.get("promise") or "")[:120],
            "severity": entry.get("severity"),
        })
    return out


def count_pending() -> int:
    with _connect() as conn:
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM airtable_buffer")
        return int(cur.fetchone()[0])


async def drain(max_items: int = 100) -> dict:
    """Re-attempt Airtable sync for up to `max_items` queued entries.
    Removes each on success. Updates attempts/last_reason on continued
    failure. Returns {drained, succeeded, still_failed, list of action_ids
    in each bucket}.

    Async so it can be called from autonomous task + admin endpoint
    without blocking the event loop on each sync's network call."""
    from . import airtable_sync as _as
    with _connect() as conn:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT id, action_id, entry_json FROM airtable_buffer
            ORDER BY first_failed_at ASC LIMIT ?
            """,
            (max_items,),
        )
        candidates = cur.fetchall()
    succeeded: list[str] = []
    still_failed: list[dict] = []
    for row in candidates:
        row_id, aid, body_json = row
        try:
            entry = json.loads(body_json)
        except json.JSONDecodeError:
            # Unparseable entry — archive it; can't retry blind data.
            with _connect() as conn:
                cur = conn.cursor()
                cur.execute("DELETE FROM airtable_buffer WHERE id = ?", (row_id,))
            logger.error(
                "[airtable_buffer] drain: unparseable entry_json for "
                "action=%s — dropped (was %d bytes)", aid, len(body_json),
            )
            continue
        try:
            result = await _as.sync_record(entry)
        except Exception as e:  # noqa: BLE001 — sync raised, treat as still-failed
            result = {"ok": False, "reason": f"raised: {type(e).__name__}"}
        if isinstance(result, dict) and result.get("ok"):
            with _connect() as conn:
                cur = conn.cursor()
                cur.execute("DELETE FROM airtable_buffer WHERE id = ?", (row_id,))
            succeeded.append(aid)
        else:
            reason = (result or {}).get("reason") or "unknown"
            now = time.time()
            with _connect() as conn:
                cur = conn.cursor()
                cur.execute(
                    """
                    UPDATE airtable_buffer
                       SET attempts = attempts + 1,
                           last_attempt_at = ?,
                           last_reason = ?
                     WHERE id = ?
                    """,
                    (now, reason[:200], row_id),
                )
            still_failed.append({"action_id": aid, "reason": reason})
    logger.info(
        "[airtable_buffer] drain: %d attempted, %d succeeded, %d still failing",
        len(candidates), len(succeeded), len(still_failed),
    )
    return {
        "drained": len(candidates),
        "succeeded": len(succeeded),
        "still_failed": len(still_failed),
        "succeeded_ids": succeeded,
        "still_failed_details": still_failed,
    }


def get_status() -> dict[str, Any]:
    """Operator-facing health snapshot."""
    return {
        "pending": count_pending(),
        "cap": _BUFFER_CAP,
        "buffer_db": _db_path(),
        "oldest_unsynced": (list_pending(limit=1) or [None])[0],
    }
