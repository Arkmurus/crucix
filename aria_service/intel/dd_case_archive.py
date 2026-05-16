"""R-F575 — DD case-file SQLite cold tier.

Redis stores DD reports + index for 7 days (REPORT_TTL_SECONDS). For
case-file continuity that's not enough — operators want a complete
chain of every DD they've run on the same canonical_entity_id, even
when one of the earlier versions was last touched ≥7 days ago.

This module mirrors index entries into SQLite at /data/dd_case_archive.db
(or wherever DATA volume is mounted). Every `_persist_report` writes the
new index entry to BOTH Redis and SQLite. `get_case_file` reads from
BOTH and merges (dedup by run_id), so the version chain survives Redis
eviction.

90-day retention on the SQLite side — long enough that quarterly DD
re-runs still surface the v(N-1) entry. Older entries are purged in
the background by `purge_expired()`.

The schema is intentionally narrow: just enough to render a case file
chain. Full report bodies stay in Redis (and can be regenerated on
demand via a re-run if they've expired).
"""
from __future__ import annotations

import json
import logging
import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Iterator

logger = logging.getLogger("aria.dd_case_archive")

# Default 90-day TTL — well beyond the Redis 7-day. Operator can
# extend via env if they want longer audit windows.
_DEFAULT_RETENTION_DAYS = int(os.environ.get("ARIA_DD_CASE_ARCHIVE_DAYS", "90"))


def _db_path() -> str:
    """Default: /data/dd_case_archive.db (fly volume).
    Override via ARIA_DD_CASE_ARCHIVE_DB env."""
    p = os.environ.get("ARIA_DD_CASE_ARCHIVE_DB", "").strip()
    if p:
        return p
    if os.path.isdir("/data"):
        return "/data/dd_case_archive.db"
    # Local dev fallback
    return os.path.join(os.path.dirname(__file__), "_local_dd_case_archive.db")


_SCHEMA = """
CREATE TABLE IF NOT EXISTS case_archive (
    run_id              TEXT PRIMARY KEY,
    canonical_entity_id TEXT,
    version_number      INTEGER,
    previous_run_id     TEXT,
    entity_name         TEXT,
    jurisdiction        TEXT,
    risk_classification TEXT,
    generated_at        TEXT NOT NULL,    -- ISO-8601
    archived_at         REAL NOT NULL     -- unix seconds when this row was written
);

CREATE INDEX IF NOT EXISTS idx_case_canonical_id ON case_archive(canonical_entity_id);
CREATE INDEX IF NOT EXISTS idx_case_archived_at ON case_archive(archived_at);
CREATE INDEX IF NOT EXISTS idx_case_generated_at ON case_archive(generated_at);
"""


@contextmanager
def _connect() -> Iterator[sqlite3.Connection]:
    """Yield a connection with schema ensured + WAL mode."""
    path = _db_path()
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.row_factory = sqlite3.Row
    conn.executescript(_SCHEMA)
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def archive_entry(entry: dict) -> None:
    """Persist (or upsert) a single index entry to the cold tier.

    Called from `_persist_report` right after the Redis write. Failure
    is non-fatal — the Redis copy is still authoritative for the
    7-day window; SQLite is the long-term mirror only.
    """
    if not entry or not entry.get("run_id"):
        return
    try:
        with _connect() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO case_archive (
                    run_id, canonical_entity_id, version_number,
                    previous_run_id, entity_name, jurisdiction,
                    risk_classification, generated_at, archived_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    entry.get("run_id"),
                    entry.get("canonical_entity_id"),
                    entry.get("version_number"),
                    entry.get("previous_run_id"),
                    entry.get("entity_name"),
                    entry.get("jurisdiction"),
                    entry.get("risk_classification"),
                    entry.get("generated_at") or datetime.now(timezone.utc).isoformat(),
                    datetime.now(timezone.utc).timestamp(),
                ),
            )
    except Exception as e:
        logger.debug("dd_case_archive.archive_entry failed: %s", e)


def lookup_by_canonical_id(canonical_entity_id: str) -> list[dict]:
    """Return all archived entries for a single case file, newest-first.

    Used by `get_case_file` to extend the Redis view with archived
    entries that have aged out.
    """
    if not canonical_entity_id:
        return []
    try:
        with _connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM case_archive
                WHERE canonical_entity_id = ?
                ORDER BY generated_at DESC
                """,
                (canonical_entity_id,),
            ).fetchall()
        return [dict(r) for r in rows]
    except Exception as e:
        logger.debug("dd_case_archive.lookup_by_canonical_id failed: %s", e)
        return []


def update_canonical_id(run_id: str, new_canonical_id: str | None) -> bool:
    """Rewrite the canonical_entity_id on a single archived entry.

    Used by the operator-override split / merge endpoints. Returns
    True if a row was updated.
    """
    if not run_id:
        return False
    try:
        with _connect() as conn:
            cur = conn.execute(
                "UPDATE case_archive SET canonical_entity_id = ? WHERE run_id = ?",
                (new_canonical_id, run_id),
            )
            return (cur.rowcount or 0) > 0
    except Exception as e:
        logger.debug("dd_case_archive.update_canonical_id failed: %s", e)
        return False


def purge_expired(retention_days: int | None = None) -> int:
    """Delete archive rows older than `retention_days` (default 90).

    Returns number of rows deleted. Safe to call from a periodic
    autonomous task; idempotent.
    """
    days = retention_days if retention_days is not None else _DEFAULT_RETENTION_DAYS
    cutoff = datetime.now(timezone.utc).timestamp() - (days * 24 * 3600)
    try:
        with _connect() as conn:
            cur = conn.execute(
                "DELETE FROM case_archive WHERE archived_at < ?",
                (cutoff,),
            )
            return int(cur.rowcount or 0)
    except Exception as e:
        logger.debug("dd_case_archive.purge_expired failed: %s", e)
        return 0


def stats() -> dict:
    """Diagnostic: row count + oldest/newest archived_at."""
    try:
        with _connect() as conn:
            row = conn.execute(
                """
                SELECT COUNT(*) AS n,
                       MIN(archived_at) AS oldest,
                       MAX(archived_at) AS newest
                FROM case_archive
                """
            ).fetchone()
            distinct_cases = conn.execute(
                "SELECT COUNT(DISTINCT canonical_entity_id) AS n FROM case_archive"
            ).fetchone()
        return {
            "total_rows": int(row["n"] if row else 0),
            "distinct_cases": int(distinct_cases["n"] if distinct_cases else 0),
            "oldest_archived_at": (row["oldest"] if row else None),
            "newest_archived_at": (row["newest"] if row else None),
            "retention_days_env": _DEFAULT_RETENTION_DAYS,
            "db_path": _db_path(),
        }
    except Exception as e:
        logger.debug("dd_case_archive.stats failed: %s", e)
        return {"error": str(e)}
