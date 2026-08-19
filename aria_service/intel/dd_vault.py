"""R-F1655 — DD Vault: persistent SQLite-backed storage for all DD cases.

Every DD run ever performed is recorded here with canonical_entity_id,
findings summary, risk score, and cross-references to related entities.
This is the single source of truth for "what companies have we DD'd?"

When a new DD is requested, the vault is checked first. If the company
has been investigated before, the existing report summary is returned
with an option to re-run. Cross-references link related companies so
findings from one DD surface in another.

Schema mirrors agent_signup_vault.py — same pattern, same reliability.
"""
from __future__ import annotations

import json
import logging
import os
import sqlite3
import time
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger("aria.dd_vault")

# R-F1166 — wire to brain on vault operations
from .engine_wiring import wire_success, wire_failure
from .wire import fail_wire  # R-F1789 §21 brain-wiring

# R-F4158 (C-179) — honour ARIA_DATA_DIR, like every sibling store.
#
# This hardcoded `Path("/data")`, which on a Windows dev box resolves to
# `C:\data\dd_vault.db` — a file OUTSIDE the repo, shared by every test run on
# the machine and never cleaned. `list_reports` reconciles against this vault on
# every read (R-F1973/R-F2485/R-F2652), so DD list tests silently merged whatever
# earlier runs had left there. Measured 2026-08-18: six residual fixture rows
# (`Risky Business SARL`, `dd_test_red`, ...) turned
# `test_rf2407_dd_rerun_unnamed` red on this box while it stayed green in CI,
# and the §16 baseline could not see it because the baseline machine's copy
# happened to be empty.
#
# `ARIA_DATA_DIR` is the established idiom — `agent_contract`, `agent_registry`,
# `brave_distill` and `brave_student` all resolve through it with a `/data`
# fallback. PRODUCTION IS UNCHANGED: the var is unset on Fly (R-F1692 records
# exactly that), so this still resolves to `/data`. What it adds is the ability
# to point tests at a throwaway directory instead of a machine-global file.
_VAULT_DIR = Path(os.getenv("ARIA_DATA_DIR") or "/data")
_VAULT_DB = _VAULT_DIR / "dd_vault.db"

_CREATE_SQL = """
CREATE TABLE IF NOT EXISTS dd_cases (
    canonical_entity_id TEXT PRIMARY KEY,
    entity_name         TEXT NOT NULL,
    entity_type         TEXT NOT NULL DEFAULT 'company',
    jurisdiction        TEXT DEFAULT '',
    registration_number TEXT DEFAULT '',
    last_run_at         REAL NOT NULL,
    run_count           INTEGER NOT NULL DEFAULT 1,
    latest_report_id    TEXT DEFAULT '',
    previous_report_ids TEXT DEFAULT '[]',
    findings_summary    TEXT DEFAULT '',
    risk_score          REAL DEFAULT 0.0,
    risk_level          TEXT DEFAULT 'unknown',
    -- R-F2683 — evidence-grade SNAPSHOT (dd_schema._dd_quality_assessment) as of the
    -- last run. This is evidence DEPTH, orthogonal to risk_score: a GREEN case can be
    -- Grade D. NULL/'' means UNGRADED (pre-R-F2683 rows, or a run that could not be
    -- graded) — never fabricate a grade for them.
    evidence_grade      TEXT DEFAULT '',
    evidence_score      REAL,
    grade_blockers      TEXT DEFAULT '[]',
    graded_at           REAL,
    status              TEXT NOT NULL DEFAULT 'active',
    tags                TEXT DEFAULT '[]',
    cross_references    TEXT DEFAULT '[]',
    created_at          REAL NOT NULL,
    updated_at          REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS dd_cross_references (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    source_entity   TEXT NOT NULL,
    target_entity   TEXT NOT NULL,
    relationship    TEXT NOT NULL,
    finding_summary TEXT DEFAULT '',
    discovered_at   REAL NOT NULL,
    user_id         TEXT,
    -- R-F2697 — user_id is IN the key. Without it the graph is physically single-tenant:
    -- add_cross_reference uses INSERT OR REPLACE, so tenant B writing the same
    -- (source,target,relationship) DELETES tenant A's row and its finding_summary —
    -- silent cross-tenant DATA LOSS (§7), not just a bad read. Two tenants running DD on
    -- the same company pair is the EXPECTED case. (SQLite treats NULLs as distinct in a
    -- UNIQUE index, so unattributed rows coexist rather than colliding.)
    UNIQUE(source_entity, target_entity, relationship, user_id)
);

CREATE TABLE IF NOT EXISTS vault_meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

-- R-F2322 — multi-jurisdiction financial/company profile registry. Keyed by the
-- same canonical_entity_id as dd_cases, but INDEPENDENT of the DD-case lifecycle so a
-- company can be registered with value-added financial info (from SEC EDGAR, search,
-- registries, uploaded docs — ANY jurisdiction) even without a full DD run. This is the
-- accumulation store: pay-once-remember-forever (§15) across jurisdictions.
CREATE TABLE IF NOT EXISTS financial_profiles (
    canonical_entity_id TEXT PRIMARY KEY,
    entity_name         TEXT NOT NULL DEFAULT '',
    jurisdiction        TEXT DEFAULT '',
    profile_json        TEXT NOT NULL DEFAULT '{}',
    updated_at          REAL NOT NULL
);

-- R-F2469 — per-RUN report ownership so ownership SURVIVES a state_store wipe.
-- The report index (which carries user_id) lives in the WIPEABLE state_store; this
-- vault DB (/data/dd_vault.db) survives. Keyed by run_id — PER-RUN and multi-user-
-- safe, unlike dd_cases which is per-entity (one row per canonical_entity_id, so it
-- cannot hold two tenants' ownership of the same entity). Written at persist time,
-- read on index rebuild to restore the REAL owner instead of fabricating one (the
-- R-F2466 cross-tenant-leak class). Owner-less runs write nothing → stay owner-less.
CREATE TABLE IF NOT EXISTS dd_report_owners (
    run_id              TEXT PRIMARY KEY,
    canonical_entity_id TEXT,
    user_id             TEXT,
    user_email_domain   TEXT,
    share_to_company    INTEGER NOT NULL DEFAULT 1,
    created_at          REAL NOT NULL
);

INSERT OR IGNORE INTO vault_meta (key, value) VALUES ('schema_version', '1');
"""


def _row_to_case(row: sqlite3.Row) -> dict[str, Any]:
    """R-F2683 — decode a dd_cases row into its public dict.

    `grade_blockers` is stored as JSON and handed back as a real list so callers
    never re-parse it. A corrupt/absent value degrades to [] ("no blockers known"),
    never raises — a bad blocker list must not take down a DD read path. Other JSON
    columns (tags, previous_report_ids) keep their long-standing raw-string
    contract; callers already json.loads them and this is not the R-number to
    change that.
    """
    rec = dict(row)
    raw = rec.get("grade_blockers")
    if isinstance(raw, str):
        try:
            decoded = json.loads(raw or "[]")
            rec["grade_blockers"] = decoded if isinstance(decoded, list) else []
        except Exception:
            rec["grade_blockers"] = []
    elif raw is None:
        rec["grade_blockers"] = []
    return rec


class DDVault:
    """Persistent vault of every DD case ever run.

    Thread-safe via SQLite's built-in locking. Lazy-initializes the
    database on first use.
    """

    def __init__(self, db_path: str | Path | None = None):
        self._db_path = Path(db_path or _VAULT_DB)
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn: sqlite3.Connection | None = None

    def _get_conn(self) -> sqlite3.Connection:
        if self._conn is None:
            conn = sqlite3.connect(str(self._db_path), check_same_thread=False)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA foreign_keys=ON")
            # R-F2683 — a WAL reader/writer that finds the DB locked must WAIT for it
            # rather than instantly raising SQLITE_BUSY. The DD engine writes this vault
            # concurrently, and _migrate()'s ALTER TABLE needs a write lock, so a
            # zero-timeout connection would fail purely on collision timing.
            conn.execute("PRAGMA busy_timeout=10000")
            # R-F2683 — publish the connection ONLY once it is fully initialised and
            # migrated. Assigning self._conn BEFORE _init_db() meant a failed init
            # (now possible: _migrate() runs ALTER TABLE against the live DB) left a
            # non-None un-migrated connection that the `is None` guard above would
            # hand out forever — every subsequent write raising "no such column" with
            # no retry, i.e. vault persistence wedged until process restart. Failing
            # closed and resetting to None lets the NEXT call retry the migration.
            try:
                self._conn = conn
                self._init_db()
            except Exception:
                self._conn = None
                try:
                    conn.close()
                except Exception:
                    pass
                raise
        return self._conn

    def _init_db(self):
        self._conn.executescript(_CREATE_SQL)
        self._conn.commit()
        self._migrate()

    # R-F2683 — additive column migrations for ALREADY-EXISTING vault DBs.
    # `CREATE TABLE IF NOT EXISTS` is a no-op on a live table, so a column added to
    # _CREATE_SQL reaches fresh DBs ONLY. The prod vault (/data/dd_vault.db) predates
    # every entry below, so without this each new column would raise "no such column"
    # on the first write. Additive only — never drops/rewrites a column (§7: no data
    # loss), and idempotent: the PRAGMA check makes a re-open a no-op.
    _COLUMN_MIGRATIONS: tuple[tuple[str, str, str], ...] = (
        ("dd_cases", "evidence_grade", "TEXT DEFAULT ''"),
        ("dd_cases", "evidence_score", "REAL"),
        ("dd_cases", "grade_blockers", "TEXT DEFAULT '[]'"),
        ("dd_cases", "graded_at", "REAL"),
        # R-F2697 — WHO discovered this relationship. Added BEFORE the writer is wired
        # (the table is empty in prod, so there is nothing to backfill and no legacy
        # row to hide) — attribution is impossible to reconstruct after the fact, so
        # this is the only cheap moment. NULL = unattributed/legacy → visible ONLY to
        # internal callers, never to a tenant (fail closed; matches the operator's
        # binding rule on owner-less DD rows: "don't touch what other users created").
        ("dd_cross_references", "user_id", "TEXT"),
    )
    _SCHEMA_VERSION = "3"

    def _migrate_cross_ref_unique_key(self) -> None:
        """R-F2697 — rebuild dd_cross_references so user_id is IN the UNIQUE key.

        `ALTER TABLE ADD COLUMN` cannot change a constraint, and `CREATE TABLE IF NOT
        EXISTS` is a no-op on the live table — so the prod vault keeps the old 3-column
        key (source,target,relationship) unless we rebuild. With that key,
        `INSERT OR REPLACE` lets tenant B silently DELETE tenant A's edge + its
        finding_summary (cross-tenant data loss, §7). A Pass-2 review reproduced it.

        SQLite cannot alter a UNIQUE constraint, so this is the 12-step table rebuild —
        done NOW because the table is EMPTY in prod (nothing to copy, no downtime risk);
        after the R-F2698 writer lands it would be a real data migration. Idempotent: it
        inspects the existing unique index and returns if user_id is already in the key.
        """
        conn = self._conn
        try:
            for idx in conn.execute("PRAGMA index_list(dd_cross_references)").fetchall():
                if not idx["unique"]:
                    continue
                cols = {r["name"] for r in conn.execute(f"PRAGMA index_info({idx['name']})")}
                if "user_id" in cols:
                    return  # already rebuilt — no-op
        except Exception:
            return  # table absent (fresh DB gets the correct key from _CREATE_SQL)
        try:
            conn.executescript(
                """
                PRAGMA foreign_keys=off;
                BEGIN;
                CREATE TABLE dd_cross_references_new (
                    id              INTEGER PRIMARY KEY AUTOINCREMENT,
                    source_entity   TEXT NOT NULL,
                    target_entity   TEXT NOT NULL,
                    relationship    TEXT NOT NULL,
                    finding_summary TEXT DEFAULT '',
                    discovered_at   REAL NOT NULL,
                    user_id         TEXT,
                    UNIQUE(source_entity, target_entity, relationship, user_id)
                );
                INSERT INTO dd_cross_references_new
                    (source_entity, target_entity, relationship, finding_summary, discovered_at, user_id)
                    SELECT source_entity, target_entity, relationship, finding_summary, discovered_at, user_id
                    FROM dd_cross_references;
                DROP TABLE dd_cross_references;
                ALTER TABLE dd_cross_references_new RENAME TO dd_cross_references;
                COMMIT;
                PRAGMA foreign_keys=on;
                """
            )
            logger.info("dd_vault: R-F2697 rebuilt dd_cross_references — user_id is now in the UNIQUE key")
        except Exception as e:
            # Never brick the vault over this. The old key still WORKS; it is only unsafe
            # once the writer is multi-tenant, and R-F2698 must not ship until this holds.
            logger.error(
                "[R-F2697] dd_cross_references UNIQUE-key rebuild FAILED (%s) — the graph "
                "writer must NOT be wired until this succeeds: tenants would overwrite "
                "each other's edges", e,
            )

    def _migrate(self) -> None:
        conn = self._conn
        added: list[str] = []
        for table, column, decl in self._COLUMN_MIGRATIONS:
            cols = {r["name"] for r in conn.execute(f"PRAGMA table_info({table})")}
            if not cols:
                continue  # table absent (shouldn't happen post-_CREATE_SQL) — nothing to alter
            if column in cols:
                continue
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {decl}")
            added.append(f"{table}.{column}")
        self._migrate_cross_ref_unique_key()
        conn.execute(
            "INSERT INTO vault_meta (key, value) VALUES ('schema_version', ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (self._SCHEMA_VERSION,),
        )
        conn.commit()
        if added:
            logger.info("dd_vault: schema migrated to v%s — added %s",
                        self._SCHEMA_VERSION, ", ".join(added))

    @fail_wire(module="dd_vault", gap_type="engine_failure")
    def close(self):
        if self._conn:
            self._conn.close()
            self._conn = None

    # ── CRUD ─────────────────────────────────────────────────────────────

    @fail_wire(module="dd_vault", gap_type="engine_failure")
    def record_case(
        self,
        canonical_entity_id: str,
        entity_name: str,
        entity_type: str = "company",
        jurisdiction: str = "",
        registration_number: str = "",
        latest_report_id: str = "",
        findings_summary: str = "",
        risk_score: float = 0.0,
        risk_level: str = "unknown",
        tags: list[str] | None = None,
        evidence_grade: str = "",
        evidence_score: float | None = None,
        grade_blockers: list[str] | None = None,
    ) -> dict[str, Any]:
        """Record a DD case. If the entity already exists, update it.

        R-F2683 — `evidence_grade`/`evidence_score`/`grade_blockers` are the
        dd_schema._dd_quality_assessment SNAPSHOT for THIS run, stamped graded_at.
        They are history (what the evidence looked like when we ran it), NOT the
        authority: any live display must RECOMPUTE from the report (R-F2681 does),
        because the grade moves as sources are added. Callers that cannot grade
        pass nothing → the case stays honestly UNGRADED rather than defaulting to
        a grade nobody measured.

        Returns the case record with version info.
        """
        conn = self._get_conn()
        now = time.time()
        tags_json = json.dumps(tags or [])
        # Only stamp graded_at when a grade was actually supplied — an unstamped row
        # is "never graded", which must stay distinguishable from "graded just now".
        graded = bool(evidence_grade) or evidence_score is not None
        graded_at = now if graded else None
        blockers_json = json.dumps(grade_blockers or [])

        existing = self.get_case(canonical_entity_id)
        if existing:
            # Update existing — increment run count, archive old report
            prev_ids = json.loads(existing.get("previous_report_ids", "[]"))
            if existing.get("latest_report_id"):
                prev_ids.append(existing["latest_report_id"])
            conn.execute(
                """UPDATE dd_cases SET
                    entity_name = ?, entity_type = ?, jurisdiction = ?,
                    registration_number = ?, last_run_at = ?,
                    run_count = run_count + 1,
                    latest_report_id = ?,
                    previous_report_ids = ?,
                    findings_summary = ?, risk_score = ?, risk_level = ?,
                    evidence_grade = ?, evidence_score = ?,
                    grade_blockers = ?, graded_at = ?,
                    tags = ?, status = 'active', updated_at = ?
                WHERE canonical_entity_id = ?""",
                (
                    entity_name, entity_type, jurisdiction,
                    registration_number, now,
                    latest_report_id,
                    json.dumps(prev_ids[-20:]),  # keep last 20
                    findings_summary, risk_score, risk_level,
                    # The snapshot always describes the LATEST run, mirroring
                    # risk_score above. An ungraded re-run therefore CLEARS a prior
                    # grade rather than leaving last run's grade to masquerade as
                    # this one's — the grade is recomputable from the report, a
                    # silently stale one is not detectable.
                    evidence_grade, evidence_score, blockers_json, graded_at,
                    tags_json, now,
                    canonical_entity_id,
                ),
            )
            version = existing.get("run_count", 0) + 1
        else:
            conn.execute(
                """INSERT INTO dd_cases
                    (canonical_entity_id, entity_name, entity_type,
                     jurisdiction, registration_number,
                     last_run_at, run_count, latest_report_id,
                     previous_report_ids, findings_summary,
                     risk_score, risk_level,
                     evidence_grade, evidence_score, grade_blockers, graded_at,
                     status, tags,
                     cross_references, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, 1, ?, '[]', ?, ?, ?, ?, ?, ?, ?, 'active', ?, '[]', ?, ?)""",
                (
                    canonical_entity_id, entity_name, entity_type,
                    jurisdiction, registration_number,
                    now, latest_report_id,
                    findings_summary, risk_score, risk_level,
                    evidence_grade, evidence_score, blockers_json, graded_at,
                    tags_json, now, now,
                ),
            )
            version = 1

        conn.commit()

        # Wire to brain
        try:
            wire_success(
                module="dd_vault",
                summary=f"DD case recorded: {entity_name} (v{version})",
                source_id=f"dd_vault:{canonical_entity_id}",
            )
        except Exception:
            pass

        return {
            "canonical_entity_id": canonical_entity_id,
            "entity_name": entity_name,
            "version": version,
            "is_new": existing is None,
        }

    @fail_wire(module="dd_vault", gap_type="engine_failure")
    def record_report_owner(
        self,
        run_id: str,
        *,
        canonical_entity_id: str | None = None,
        user_id: str | None = None,
        user_email_domain: str | None = None,
        share_to_company: bool = True,
    ) -> None:
        """R-F2469 — persist per-RUN ownership so it SURVIVES a state_store wipe.

        Written at DD persist time. On an index rebuild from the vault (which has no
        per-user ownership on dd_cases), list_reports reads this to restore the REAL
        owner instead of fabricating one (the R-F2466 leak). No-op when user_id is
        empty — an owner-less run must STAY owner-less (fail-closed), never invented.
        """
        if not run_id or not (user_id or "").strip():
            return
        conn = self._get_conn()
        conn.execute(
            """INSERT INTO dd_report_owners
                 (run_id, canonical_entity_id, user_id, user_email_domain,
                  share_to_company, created_at)
               VALUES (?, ?, ?, ?, ?, ?)
               ON CONFLICT(run_id) DO UPDATE SET
                 canonical_entity_id = excluded.canonical_entity_id,
                 user_id             = excluded.user_id,
                 user_email_domain   = excluded.user_email_domain,
                 share_to_company    = excluded.share_to_company""",
            (
                run_id,
                canonical_entity_id,
                user_id.strip(),
                (user_email_domain or "").strip().lower() or None,
                1 if share_to_company else 0,
                time.time(),
            ),
        )
        conn.commit()

    @fail_wire(module="dd_vault", gap_type="engine_failure")
    def get_report_owner(self, run_id: str) -> dict[str, Any] | None:
        """R-F2469 — return {user_id, user_email_domain, share_to_company} for a
        run_id, or None when unknown (→ the caller keeps the row owner-less,
        fail-closed). Immune to a state_store wipe (this table is in the vault DB)."""
        if not run_id:
            return None
        conn = self._get_conn()
        row = conn.execute(
            "SELECT user_id, user_email_domain, share_to_company "
            "FROM dd_report_owners WHERE run_id = ?",
            (run_id,),
        ).fetchone()
        if not row or not row[0]:
            return None
        return {
            "user_id": row[0],
            "user_email_domain": row[1],
            "share_to_company": bool(row[2]),
        }

    @fail_wire(module="dd_vault", gap_type="engine_failure")
    def get_case(self, canonical_entity_id: str) -> dict[str, Any] | None:
        """Get a single DD case by canonical_entity_id."""
        conn = self._get_conn()
        row = conn.execute(
            "SELECT * FROM dd_cases WHERE canonical_entity_id = ?",
            (canonical_entity_id,),
        ).fetchone()
        if row is None:
            return None
        return _row_to_case(row)

    # ── R-F2322: multi-jurisdiction financial-profile registry ───────────────

    @fail_wire(module="dd_vault", gap_type="engine_failure")
    def get_financial_profile(self, canonical_entity_id: str) -> dict[str, Any] | None:
        """Return the stored financial profile for an entity (any jurisdiction), or None.
        Includes `updated_at` so callers can apply a freshness policy."""
        if not canonical_entity_id:
            return None
        conn = self._get_conn()
        row = conn.execute(
            "SELECT * FROM financial_profiles WHERE canonical_entity_id = ?",
            (canonical_entity_id,),
        ).fetchone()
        if row is None:
            return None
        rec = dict(row)
        try:
            profile = json.loads(rec.get("profile_json") or "{}")
        except Exception:
            profile = {}
        profile["_vault_updated_at"] = rec.get("updated_at")
        profile["_vault_entity_name"] = rec.get("entity_name")
        profile["_vault_jurisdiction"] = rec.get("jurisdiction")
        return profile

    @fail_wire(module="dd_vault", gap_type="engine_failure")
    def set_financial_profile(
        self,
        canonical_entity_id: str,
        profile: dict[str, Any],
        entity_name: str = "",
        jurisdiction: str = "",
    ) -> bool:
        """Register/update an entity's financial profile (upsert). This is how ARIA
        accumulates value-added multi-jurisdiction company info over time (§7/§15)."""
        if not canonical_entity_id or not isinstance(profile, dict):
            try:
                wire_failure(module="dd_vault",
                             detail="set_financial_profile rejected invalid input (no id / non-dict profile)",
                             gap_type="engine_failure", source="dd_vault:set_financial_profile")
            except Exception:
                pass
            return False
        conn = self._get_conn()
        now = time.time()
        # Don't persist transient vault-echo keys back into the blob.
        clean = {k: v for k, v in profile.items() if not k.startswith("_vault_")}
        conn.execute(
            """INSERT INTO financial_profiles
                 (canonical_entity_id, entity_name, jurisdiction, profile_json, updated_at)
               VALUES (?, ?, ?, ?, ?)
               ON CONFLICT(canonical_entity_id) DO UPDATE SET
                 entity_name = excluded.entity_name,
                 jurisdiction = excluded.jurisdiction,
                 profile_json = excluded.profile_json,
                 updated_at = excluded.updated_at""",
            (canonical_entity_id, entity_name or clean.get("entity", ""),
             jurisdiction, json.dumps(clean), now),
        )
        conn.commit()
        try:
            wire_success(
                module="dd_vault",
                summary=f"financial profile registered: {entity_name or canonical_entity_id}",
                source_id=f"dd_vault:fin:{canonical_entity_id}",
            )
        except Exception:
            pass
        return True

    @fail_wire(module="dd_vault", gap_type="engine_failure")
    def search_financial_profiles(self, query: str, limit: int = 20) -> list[dict[str, Any]]:
        """Search registered financial profiles by entity name or jurisdiction."""
        conn = self._get_conn()
        like = f"%{query}%"
        rows = conn.execute(
            """SELECT canonical_entity_id, entity_name, jurisdiction, updated_at
               FROM financial_profiles
               WHERE entity_name LIKE ? OR jurisdiction LIKE ?
               ORDER BY updated_at DESC LIMIT ?""",
            (like, like, limit),
        ).fetchall()
        return [dict(r) for r in rows]  # financial_profiles rows carry no grade columns

    @fail_wire(module="dd_vault", gap_type="engine_failure")
    def clear_all(self) -> dict[str, int]:
        """R-F2337 — DANGER: delete EVERY DD case, cross-reference, and financial
        profile. Used to wipe accumulated test data for a clean start. Irreversible."""
        conn = self._get_conn()
        counts: dict[str, int] = {}
        for tbl in ("dd_cases", "dd_cross_references", "financial_profiles"):
            try:
                before = conn.execute(f"SELECT COUNT(*) FROM {tbl}").fetchone()[0]  # nosec B608 - tbl comes from the fixed table tuple above.
                conn.execute(f"DELETE FROM {tbl}")  # nosec B608 - tbl comes from the fixed table tuple above.
                counts[tbl] = int(before)
            except Exception as e:
                logger.warning("[dd_vault] clear_all(%s) failed: %s", tbl, e)
                counts[tbl] = -1
        conn.commit()
        try:
            wire_success(module="dd_vault", summary=f"DD vault cleared: {counts}",
                         source_id="dd_vault:clear_all")
        except Exception:
            pass
        return counts

    @fail_wire(module="dd_vault", gap_type="engine_failure")
    def search(self, query: str, limit: int = 20) -> list[dict[str, Any]]:
        """Search cases by entity name, jurisdiction, or tags."""
        conn = self._get_conn()
        like = f"%{query}%"
        rows = conn.execute(
            """SELECT * FROM dd_cases
               WHERE entity_name LIKE ? OR jurisdiction LIKE ? OR tags LIKE ?
               ORDER BY last_run_at DESC LIMIT ?""",
            (like, like, like, limit),
        ).fetchall()
        return [_row_to_case(r) for r in rows]

    @fail_wire(module="dd_vault", gap_type="engine_failure")
    def list_by_status(self, status: str = "active", limit: int = 100) -> list[dict[str, Any]]:
        """List cases by status."""
        conn = self._get_conn()
        rows = conn.execute(
            "SELECT * FROM dd_cases WHERE status = ? ORDER BY last_run_at DESC LIMIT ?",
            (status, limit),
        ).fetchall()
        return [_row_to_case(r) for r in rows]

    @fail_wire(module="dd_vault", gap_type="engine_failure")
    def list_all(self, limit: int = 100) -> list[dict[str, Any]]:
        """List all cases, newest first."""
        conn = self._get_conn()
        rows = conn.execute(
            "SELECT * FROM dd_cases ORDER BY last_run_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [_row_to_case(r) for r in rows]

    @fail_wire(module="dd_vault", gap_type="engine_failure")
    def update_status(self, canonical_entity_id: str, status: str) -> None:
        """Update case status (active/dormant/archived)."""
        conn = self._get_conn()
        conn.execute(
            "UPDATE dd_cases SET status = ?, updated_at = ? WHERE canonical_entity_id = ?",
            (status, time.time(), canonical_entity_id),
        )
        conn.commit()

    @fail_wire(module="dd_vault", gap_type="engine_failure")
    def delete_case(self, canonical_entity_id: str) -> bool:
        """Delete a case and its cross-references."""
        conn = self._get_conn()
        case_cursor = conn.execute("DELETE FROM dd_cases WHERE canonical_entity_id = ?", (canonical_entity_id,))
        ref_cursor = conn.execute(
            "DELETE FROM dd_cross_references WHERE source_entity = ? OR target_entity = ?",
            (canonical_entity_id, canonical_entity_id),
        )
        conn.commit()
        return int(case_cursor.rowcount or 0) + int(ref_cursor.rowcount or 0) > 0

    @fail_wire(module="dd_vault", gap_type="engine_failure")
    def remove_report_from_case(self, canonical_entity_id: str, run_id: str) -> dict[str, Any]:
        """R-F2653 — remove ONE report from a case WITHOUT wiping the shared entity.

        dd_cases is keyed by ``canonical_entity_id`` and is SHARED across every
        tenant that has DD'd the entity — ``latest_report_id`` and each id in
        ``previous_report_ids`` can belong to a DIFFERENT user. ``delete_case()``
        dropped the whole row + ALL cross-references, so one tenant deleting THEIR
        report destroyed the case-file / version-chain / cross-refs for EVERY
        other tenant. This removes only ``run_id``:
          - if it is the latest, promote the newest previous run to latest;
          - else drop it from ``previous_report_ids``;
          - only when NO reports remain is the case (and its cross-references)
            deleted — matching the old delete for a genuine last-report delete.

        Returns ``{found, case_deleted, remaining_reports}``.
        """
        conn = self._get_conn()
        existing = self.get_case(canonical_entity_id)
        if not existing:
            return {"found": False, "case_deleted": False, "remaining_reports": 0}
        latest = (existing.get("latest_report_id") or "").strip()
        try:
            prev = [p for p in json.loads(existing.get("previous_report_ids") or "[]")
                    if isinstance(p, str) and p.strip()]
        except Exception:
            prev = []

        if run_id != latest and run_id not in prev:
            # run_id is not part of this case — never touch a shared row we don't own a slot in.
            return {"found": False, "case_deleted": False,
                    "remaining_reports": (1 if latest else 0) + len(prev)}

        if run_id == latest:
            # previous_report_ids is appended oldest→newest by record_case (:160-162),
            # so the tail is the newest prior run — promote it to latest.
            new_latest = prev.pop() if prev else ""
        else:
            prev = [p for p in prev if p != run_id]
            new_latest = latest

        remaining = (1 if new_latest else 0) + len(prev)
        if remaining == 0:
            # genuinely the entity's LAST report → same effect as delete_case.
            conn.execute("DELETE FROM dd_cases WHERE canonical_entity_id = ?", (canonical_entity_id,))
            conn.execute(
                "DELETE FROM dd_cross_references WHERE source_entity = ? OR target_entity = ?",
                (canonical_entity_id, canonical_entity_id),
            )
            conn.commit()
            return {"found": True, "case_deleted": True, "remaining_reports": 0}

        conn.execute(
            """UPDATE dd_cases SET
                 latest_report_id = ?,
                 previous_report_ids = ?,
                 run_count = MAX(1, run_count - 1),
                 updated_at = ?
               WHERE canonical_entity_id = ?""",
            (new_latest, json.dumps(prev[-20:]), time.time(), canonical_entity_id),
        )
        conn.commit()
        return {"found": True, "case_deleted": False, "remaining_reports": remaining}

    # ── Cross-references ─────────────────────────────────────────────────

    @fail_wire(module="dd_vault", gap_type="engine_failure")
    def add_cross_reference(
        self,
        source_entity: str,
        target_entity: str,
        relationship: str,
        finding_summary: str = "",
        user_id: str | None = None,
    ) -> None:
        """Record a cross-reference between two entities.

        Called after a DD run when the report mentions other entities
        that have their own DD cases in the vault.

        R-F2697 — `user_id` attributes the relationship to the tenant whose run
        discovered it. Pass it: a NULL row is visible to internal callers ONLY, so an
        unattributed write is invisible to the very tenant who produced it.
        """
        conn = self._get_conn()
        now = time.time()
        try:
            conn.execute(
                """INSERT OR REPLACE INTO dd_cross_references
                    (source_entity, target_entity, relationship, finding_summary, discovered_at, user_id)
                VALUES (?, ?, ?, ?, ?, ?)""",
                (source_entity, target_entity, relationship, finding_summary, now, user_id or None),
            )
            conn.commit()
        except Exception as e:
            logger.debug("[dd_vault] cross-reference failed: %s", e)

    @fail_wire(module="dd_vault", gap_type="engine_failure")
    def get_cross_references(
        self, canonical_entity_id: str, *, user_id: str | None = None,
    ) -> list[dict[str, Any]]:
        """Get cross-references for an entity (both directions).

        R-F2697 — `user_id` scopes to the relationships THAT TENANT's runs discovered.
        Omitting it returns every tenant's rows and is for INTERNAL/service callers
        only (mirrors `_dd_owned_entity_ids`, where None means "unrestricted internal
        caller"). Any user-facing route MUST pass it: a row carries the other party's
        `canonical_entity_id` + `finding_summary`, i.e. which entities another tenant
        investigated and what they found (the R-F2401/2402/2456/2458 leak class).
        """
        conn = self._get_conn()
        if user_id:
            rows = conn.execute(
                """SELECT * FROM dd_cross_references
                   WHERE (source_entity = ? OR target_entity = ?) AND user_id = ?
                   ORDER BY discovered_at DESC""",
                (canonical_entity_id, canonical_entity_id, user_id),
            ).fetchall()
        else:
            rows = conn.execute(
                """SELECT * FROM dd_cross_references
                   WHERE source_entity = ? OR target_entity = ?
                   ORDER BY discovered_at DESC""",
                (canonical_entity_id, canonical_entity_id),
            ).fetchall()
        return [dict(r) for r in rows]  # dd_cross_references rows carry no grade columns

    @fail_wire(module="dd_vault", gap_type="engine_failure")
    def get_related_cases(
        self,
        canonical_entity_id: str,
        *,
        user_id: str | None = None,
        visible_entity_ids: set[str] | None = None,
    ) -> list[dict[str, Any]]:
        """DD cases related to this entity via cross-references.

        R-F2697 — this is the sharp one. It returns FULL case dicts (risk_level,
        risk_score, and since R-F2683 evidence_grade), so an unscoped call hands the
        caller another tenant's verdicts. Previously it called `get_case()` on every
        linked id with NO ownership check — dormant only because no prod writer existed.
        Wiring the writer without this would have shipped the leak.

        `visible_entity_ids` = the caller's owned ids (route: `_dd_owned_entity_ids`).
        None means "unrestricted internal caller", matching that helper's contract.
        An EMPTY set is a real answer (an external caller who owns nothing) and
        correctly yields [] — do not confuse it with None (§1 tri-state).
        """
        refs = self.get_cross_references(canonical_entity_id, user_id=user_id)
        related_ids = set()
        for ref in refs:
            if ref["source_entity"] == canonical_entity_id:
                related_ids.add(ref["target_entity"])
            else:
                related_ids.add(ref["source_entity"])
        if visible_entity_ids is not None:
            related_ids &= set(visible_entity_ids)

        cases = []
        for rid in related_ids:
            case = self.get_case(rid)
            if case:
                cases.append(case)
        return cases

    # ── Stats ────────────────────────────────────────────────────────────

    @fail_wire(module="dd_vault", gap_type="engine_failure")
    def stats(self, entity_ids: "set[str] | None" = None) -> dict[str, Any]:
        """Get aggregate statistics about the DD vault.

        R-F3071 — ``entity_ids`` scopes every count to those canonical ids. The
        vault has no owner column (it is entity-keyed), so ownership is resolved
        by the caller and passed down, exactly as ``search``/``get_case`` do via
        R-F2097. Pass ``None`` ONLY for a genuinely unrestricted caller (the
        internal service token); a per-user caller that passes None publishes
        platform-wide case volume to that user.
        """
        conn = self._get_conn()
        if entity_ids is not None:
            ids = [str(i) for i in entity_ids if i]
            if not ids:
                # Owns nothing → all-zero stats. Never fall through to global.
                return {
                    "total_cases": 0, "by_status": {}, "by_type": {},
                    "total_cross_references": 0, "run_last_7d": 0,
                }
            # Chunk to stay under SQLite's variable limit on a large portfolio.
            def _counts(sql_tmpl: str, extra: tuple = ()) -> list:
                out = []
                for i in range(0, len(ids), 500):
                    chunk = ids[i:i + 500]
                    ph = ",".join("?" * len(chunk))
                    out.extend(conn.execute(
                        sql_tmpl.format(ph=ph), (*extra, *chunk)).fetchall())
                return out

            total = sum(r[0] for r in _counts(
                "SELECT COUNT(*) FROM dd_cases WHERE canonical_entity_id IN ({ph})"))
            by_status: dict[str, int] = {}
            for row in _counts("SELECT status, COUNT(*) FROM dd_cases "
                               "WHERE canonical_entity_id IN ({ph}) GROUP BY status"):
                by_status[row[0]] = by_status.get(row[0], 0) + row[1]
            by_type: dict[str, int] = {}
            for row in _counts("SELECT entity_type, COUNT(*) FROM dd_cases "
                               "WHERE canonical_entity_id IN ({ph}) GROUP BY entity_type"):
                by_type[row[0]] = by_type.get(row[0], 0) + row[1]
            total_refs = sum(r[0] for r in _counts(
                "SELECT COUNT(*) FROM dd_cross_references WHERE source_entity IN ({ph})"))
            recent = sum(r[0] for r in _counts(
                "SELECT COUNT(*) FROM dd_cases WHERE last_run_at > ? "
                "AND canonical_entity_id IN ({ph})", (time.time() - 7 * 86400,)))
            return {
                "total_cases": total,
                "by_status": by_status,
                "by_type": by_type,
                "total_cross_references": total_refs,
                "run_last_7d": recent,
            }

        total = conn.execute("SELECT COUNT(*) FROM dd_cases").fetchone()[0]
        by_status = {}
        for row in conn.execute("SELECT status, COUNT(*) FROM dd_cases GROUP BY status"):
            by_status[row[0]] = row[1]
        by_type = {}
        for row in conn.execute("SELECT entity_type, COUNT(*) FROM dd_cases GROUP BY entity_type"):
            by_type[row[0]] = row[1]
        total_refs = conn.execute("SELECT COUNT(*) FROM dd_cross_references").fetchone()[0]
        recent = conn.execute(
            "SELECT COUNT(*) FROM dd_cases WHERE last_run_at > ?",
            (time.time() - 7 * 86400,),
        ).fetchone()[0]

        return {
            "total_cases": total,
            "by_status": by_status,
            "by_type": by_type,
            "total_cross_references": total_refs,
            "run_last_7d": recent,
        }


# ── Module-level singleton ─────────────────────────────────────────────

_vault_instance: DDVault | None = None


@fail_wire(module="dd_vault", gap_type="engine_failure")
def get_vault() -> DDVault:
    """Get or create the DD vault singleton."""
    global _vault_instance
    if _vault_instance is None:
        _vault_instance = DDVault()
    return _vault_instance
