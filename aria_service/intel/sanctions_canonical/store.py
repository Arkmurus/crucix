"""SQLite schema + helpers for the canonical sanctions cache.

One row per (source, entity, jurisdiction-country) combination.
Schema kept narrow on purpose — the canonical store is for FAST
fuzzy + entity-overlap matching, not for full audit-grade
reconstruction. Full evidence (raw XML/CSV blobs) is preserved
under data/sanctions_raw/.

Indexes
═══════
  - normalised_name      → primary fuzzy-match path
  - source               → per-source filters / staleness checks
  - countries (CSV)      → entity-overlap gate's address-country check

R-F518 inheritance: callers (lookup.py) intersect entity tokens
from the QUERY normalised name against entity tokens from each
CANDIDATE's normalised name + aliases. Empty intersection blocks
HARD STOP regardless of fuzzy score.
"""
from __future__ import annotations

import json
import logging
import os
import sqlite3
import time
from contextlib import contextmanager
from typing import Iterator

logger = logging.getLogger("aria.sanctions_canonical.store")


def _db_path() -> str:
    """Default: /data/sanctions_canonical.db (fly volume).
    Override via ARIA_SANCTIONS_CANONICAL_DB env."""
    p = os.environ.get("ARIA_SANCTIONS_CANONICAL_DB", "").strip()
    if p:
        return p
    if os.path.isdir("/data"):
        return "/data/sanctions_canonical.db"
    # Local dev fallback
    return os.path.join(os.path.dirname(__file__), "_local_sanctions.db")


_SCHEMA = """
CREATE TABLE IF NOT EXISTS entries (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    source          TEXT NOT NULL,                    -- 'ofac_sdn' | 'eu_consolidated' | ...
    source_uid      TEXT NOT NULL,                    -- stable id from upstream
    formatted_name  TEXT NOT NULL,                    -- canonical display name
    normalised_name TEXT NOT NULL,                    -- lowercased + stopword-stripped + sorted
    entity_type     TEXT,                             -- 'Person' | 'Entity' | 'Vessel' | ...
    countries       TEXT,                             -- JSON array of ISO-2 country codes
    addresses       TEXT,                             -- JSON array of free-text addresses
    aliases         TEXT,                             -- JSON array of all aliases (formatted)
    programs        TEXT,                             -- JSON array of sanctions programs
    designation_at  TEXT,                             -- ISO-8601 if available
    raw_excerpt     TEXT,                             -- ≤2000 chars of original source for audit
    last_refreshed  REAL NOT NULL,                    -- unix timestamp of parse
    UNIQUE(source, source_uid)
);

CREATE INDEX IF NOT EXISTS idx_entries_norm ON entries(normalised_name);
CREATE INDEX IF NOT EXISTS idx_entries_source ON entries(source);

CREATE TABLE IF NOT EXISTS aliases (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    entry_id        INTEGER NOT NULL REFERENCES entries(id) ON DELETE CASCADE,
    formatted       TEXT NOT NULL,
    normalised      TEXT NOT NULL,
    alias_type      TEXT                              -- 'a.k.a.' | 'f.k.a.' | 'name' | 'low-quality'
);
CREATE INDEX IF NOT EXISTS idx_aliases_norm ON aliases(normalised);
CREATE INDEX IF NOT EXISTS idx_aliases_entry ON aliases(entry_id);

CREATE TABLE IF NOT EXISTS refresh_log (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    source          TEXT NOT NULL,
    started_at      REAL NOT NULL,
    finished_at     REAL,
    rows_loaded     INTEGER,
    success         INTEGER NOT NULL DEFAULT 0,
    error           TEXT
);
CREATE INDEX IF NOT EXISTS idx_refresh_source ON refresh_log(source, started_at);
"""


@contextmanager
def connect() -> Iterator[sqlite3.Connection]:
    """Yield a connection with schema ensured + WAL mode."""
    path = _db_path()
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA foreign_keys=ON;")
    conn.executescript(_SCHEMA)
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def replace_source(source: str, rows, batch_size: int = 500) -> int:
    """Atomically replace all rows for a single source.

    R-F527 (2026-05-15): `rows` may now be either a list OR an
    iterator/generator. Generators stream without materialising
    the whole batch in memory — critical for the 19k-entry OFAC
    SDN load (pre-R-F527 the full list peaked at ~95MB heap
    usage during load and contributed to the 08:56-09:08 BST
    production OOM/wedge).

    Each row must have:
      source_uid, formatted_name, normalised_name, entity_type,
      countries (list), addresses (list), aliases (list of dicts
      with formatted+normalised+alias_type), programs (list),
      designation_at (str|None), raw_excerpt (str)

    Returns number of (entries) rows inserted.
    """
    now = time.time()
    inserted = 0
    with connect() as conn:
        cur = conn.cursor()
        # Bracket the refresh in a transaction so a partial parse
        # never half-replaces the source's rows.
        cur.execute("BEGIN")
        try:
            cur.execute("DELETE FROM entries WHERE source = ?", (source,))
            for r in rows:
                cur.execute(
                    """
                    INSERT INTO entries
                      (source, source_uid, formatted_name, normalised_name,
                       entity_type, countries, addresses, aliases, programs,
                       designation_at, raw_excerpt, last_refreshed)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        source,
                        r["source_uid"],
                        r["formatted_name"],
                        r["normalised_name"],
                        r.get("entity_type", ""),
                        json.dumps(r.get("countries", [])),
                        json.dumps(r.get("addresses", [])),
                        json.dumps([
                            {
                                "formatted": a.get("formatted", ""),
                                "normalised": a.get("normalised", ""),
                                "alias_type": a.get("alias_type", ""),
                            }
                            for a in r.get("aliases", [])
                        ]),
                        json.dumps(r.get("programs", [])),
                        r.get("designation_at"),
                        (r.get("raw_excerpt") or "")[:2000],
                        now,
                    ),
                )
                entry_id = cur.lastrowid
                for a in r.get("aliases", []):
                    cur.execute(
                        """
                        INSERT INTO aliases
                          (entry_id, formatted, normalised, alias_type)
                        VALUES (?, ?, ?, ?)
                        """,
                        (
                            entry_id,
                            a.get("formatted", ""),
                            a.get("normalised", ""),
                            a.get("alias_type", ""),
                        ),
                    )
                inserted += 1
            cur.execute("COMMIT")
        except Exception:
            cur.execute("ROLLBACK")
            raise
    logger.info("[sanctions_canonical] replaced %d rows for source=%s", inserted, source)
    return inserted


def record_refresh(source: str, started_at: float, finished_at: float,
                   rows_loaded: int, success: bool, error: str = "") -> None:
    """Append to refresh_log for observability."""
    with connect() as conn:
        conn.execute(
            "INSERT INTO refresh_log (source, started_at, finished_at, rows_loaded, success, error) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (source, started_at, finished_at, rows_loaded, 1 if success else 0, error[:1000]),
        )


def get_last_refresh(source: str | None = None) -> list[dict]:
    """Return the most recent refresh record per source (or just one source if specified)."""
    with connect() as conn:
        cur = conn.cursor()
        if source:
            cur.execute(
                "SELECT source, started_at, finished_at, rows_loaded, success, error "
                "FROM refresh_log WHERE source = ? ORDER BY started_at DESC LIMIT 1",
                (source,),
            )
        else:
            cur.execute(
                """
                SELECT source, MAX(started_at) as latest, finished_at,
                       rows_loaded, success, error
                FROM refresh_log
                GROUP BY source
                """
            )
        rows = cur.fetchall()
    return [
        {
            "source": r[0],
            "started_at": r[1],
            "finished_at": r[2],
            "rows_loaded": r[3],
            "success": bool(r[4]),
            "error": r[5] or "",
        }
        for r in rows
    ]


def count_entries(source: str | None = None) -> int:
    """Row count, optionally scoped to a single source."""
    with connect() as conn:
        cur = conn.cursor()
        if source:
            cur.execute("SELECT COUNT(*) FROM entries WHERE source = ?", (source,))
        else:
            cur.execute("SELECT COUNT(*) FROM entries")
        return int(cur.fetchone()[0])
