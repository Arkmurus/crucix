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


class CoverageDriftError(Exception):
    """R-F2570 — a refresh parsed implausibly FEW rows versus the prior list (partial
    schema drift, e.g. OFAC/EU rename a tag for a class of entries). replace_source rolls
    back and preserves the last-known-good rows rather than overwrite a healthy sanctions
    list with a thin one — overwriting it would screen the dropped sanctioned entities as
    CLEAR (a false clean, the worst DD/compliance output). Never-false-clean, fail-closed."""

    def __init__(self, source: str, new_count: int, prior_count: int):
        self.source = source
        self.new_count = new_count
        self.prior_count = prior_count
        super().__init__(
            f"coverage drift: source={source} parsed {new_count} rows "
            f"(< floor of prior {prior_count}); refusing to replace")


def _drift_min_fraction() -> float:
    """R-F2570 — a refresh must carry at least this fraction of the prior row count to be
    accepted (else it's treated as partial-drift and refused). Sanctions lists effectively
    never legitimately shrink >50% between refreshes. Env-tunable ARIA_SANCTIONS_DRIFT_MIN_FRACTION."""
    try:
        return max(0.0, min(1.0, float(os.getenv("ARIA_SANCTIONS_DRIFT_MIN_FRACTION", "0.5"))))
    except Exception:
        return 0.5


def replace_source(source: str, rows, batch_size: int = 500,
                   *, absolute_floor: int = 0) -> int:
    """Atomically replace all rows for a single source.

    R-F2576 — `absolute_floor`: refuse the replace if fewer than this many rows are
    parsed, EVEN on a first load (prior=0, where the R-F2570 fraction floor is exempt).
    This catches a broken FIRST load on a fresh container that would otherwise commit
    thin data and screen sanctioned entities CLEAR. Only the download pipeline
    (load_from_file) passes it; direct-seeded fixtures use the default 0 (no floor).

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
    # R-F2570 — baseline for the coverage-drift floor: the count BEFORE we delete/replace.
    try:
        prior_count = count_entries(source)
    except Exception:
        prior_count = 0
    try:
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
                # R-F2576 — absolute first-load floor: catches a broken FIRST load (prior=0,
                # where the fraction floor below is exempt) before it commits thin data.
                if absolute_floor > 0 and inserted < absolute_floor:
                    raise CoverageDriftError(source, inserted, absolute_floor)
                # R-F2570 — coverage-drift floor: refuse to overwrite a healthy list with an
                # implausibly small parse (partial schema drift). Raising here lets the outer
                # except ROLLBACK, preserving the last-known-good rows. prior_count==0 (first
                # load / empty store) is exempt — there is no baseline to protect.
                if prior_count > 0 and inserted < int(prior_count * _drift_min_fraction()):
                    raise CoverageDriftError(source, inserted, prior_count)
                cur.execute("COMMIT")
            except Exception:
                cur.execute("ROLLBACK")
                raise
        logger.info("[sanctions_canonical] replaced %d rows for source=%s", inserted, source)
        # R-F1304 — wire success to brain (§21a)
        try:
            from ..engine_wiring import wire_success
            wire_success(
                module="sanctions_canonical.store",
                summary=f"Replaced {inserted} rows for source={source}",
                source_id="sanctions_canonical:store:replace_source",
            )
        except Exception:
            pass
        return inserted
    except CoverageDriftError as drift:
        # R-F2570 — never-false-clean: a drifted refresh was REFUSED and the last-known-good
        # rows preserved. Wire it distinctly (not a generic crash) so the self-heal loop + the
        # operator see that a sanctions source's parser likely broke and coverage is frozen.
        logger.error("[sanctions_canonical] REFUSED drifted refresh — %s", drift)
        try:
            from ..engine_wiring import wire_failure
            wire_failure(
                module="sanctions_canonical.store",
                detail=str(drift),
                gap_type="sanctions_coverage_drift",
                source="sanctions_canonical:store:replace_source",
            )
        except Exception:
            pass
        raise
    except Exception as exc:
        # R-F1304 — wire failure to brain (§21a)
        try:
            from ..engine_wiring import wire_failure
            wire_failure(
                module="sanctions_canonical.store",
                detail=f"replace_source failed for {source}: {exc}",
                gap_type="source_failure",
                source="sanctions_canonical:store:replace_source",
            )
        except Exception:
            pass
        raise


def record_refresh(source: str, started_at: float, finished_at: float,
                   rows_loaded: int, success: bool, error: str = "") -> None:
    """Append to refresh_log for observability."""
    with connect() as conn:
        conn.execute(
            "INSERT INTO refresh_log (source, started_at, finished_at, rows_loaded, success, error) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (source, started_at, finished_at, rows_loaded, 1 if success else 0, error[:1000]),
        )
    # R-F1304 — wire to brain (§21a)
    try:
        from ..engine_wiring import wire_success, wire_failure as _wf
        if success:
            wire_success(
                module="sanctions_canonical.store",
                summary=f"Refresh {source}: {rows_loaded} rows loaded",
                source_id="sanctions_canonical:store:record_refresh",
            )
        else:
            _wf(
                module="sanctions_canonical.store",
                detail=f"Refresh {source} failed: {error[:200]}",
                gap_type="source_failure",
                source="sanctions_canonical:store:record_refresh",
            )
    except Exception:
        pass


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


def last_successful_rows_loaded(source: str) -> int:
    """R-F2570 — rows_loaded of the most recent SUCCESSFUL refresh for `source`, or 0 if
    there is no successful refresh on record. The self-calibrating baseline for the
    coverage-drift plausibility floor (a store now holding far fewer rows than its own last
    healthy load is implausibly thin). Because replace_source REFUSES a drifted refresh
    (it never records success), this baseline only ever reflects a healthy load."""
    try:
        with connect() as conn:
            cur = conn.cursor()
            cur.execute(
                "SELECT rows_loaded FROM refresh_log WHERE source = ? AND success = 1 "
                "ORDER BY finished_at DESC LIMIT 1",
                (source,),
            )
            row = cur.fetchone()
            return int(row[0]) if row and row[0] is not None else 0
    except Exception:
        return 0


def newest_entry_refresh(source: str | None = None) -> float | None:
    """Unix ts of the most recent successful data parse (``entries.last_refreshed``),
    optionally scoped to one source. ``None`` when the store holds no rows.

    R-F2417: this reflects the TRUE age of the rows currently being screened and
    is immune to failed-refresh-attempt masking. ``refresh_log`` records ATTEMPTS
    (which can keep FAILING over still-present old data, so the freshest
    *successful* refresh_log row can be absent), whereas ``entries.last_refreshed``
    is only ever written when rows are actually (re)parsed. The H1 staleness gate
    falls back to this so a sustained refresh outage over stale rows is judged on
    real data age instead of being treated as 'freshness unknown' → false-clean.
    """
    with connect() as conn:
        cur = conn.cursor()
        if source:
            cur.execute("SELECT MAX(last_refreshed) FROM entries WHERE source = ?", (source,))
        else:
            cur.execute("SELECT MAX(last_refreshed) FROM entries")
        row = cur.fetchone()
    if not row or row[0] is None:
        return None
    try:
        return float(row[0])
    except (TypeError, ValueError):
        return None
