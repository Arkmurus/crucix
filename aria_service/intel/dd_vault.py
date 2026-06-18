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

_VAULT_DIR = Path(os.getenv("ARIA_DATA_DIR", str(Path(__file__).resolve().parent.parent.parent / "data")))
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
    UNIQUE(source_entity, target_entity, relationship)
);

CREATE TABLE IF NOT EXISTS vault_meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

INSERT OR IGNORE INTO vault_meta (key, value) VALUES ('schema_version', '1');
"""


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
            self._conn = sqlite3.connect(str(self._db_path), check_same_thread=False)
            self._conn.row_factory = sqlite3.Row
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA foreign_keys=ON")
            self._init_db()
        return self._conn

    def _init_db(self):
        self._conn.executescript(_CREATE_SQL)
        self._conn.commit()

    def close(self):
        if self._conn:
            self._conn.close()
            self._conn = None

    # ── CRUD ─────────────────────────────────────────────────────────────

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
    ) -> dict[str, Any]:
        """Record a DD case. If the entity already exists, update it.

        Returns the case record with version info.
        """
        conn = self._get_conn()
        now = time.time()
        tags_json = json.dumps(tags or [])

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
                    tags = ?, status = 'active', updated_at = ?
                WHERE canonical_entity_id = ?""",
                (
                    entity_name, entity_type, jurisdiction,
                    registration_number, now,
                    latest_report_id,
                    json.dumps(prev_ids[-20:]),  # keep last 20
                    findings_summary, risk_score, risk_level,
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
                     risk_score, risk_level, status, tags,
                     cross_references, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, 1, ?, '[]', ?, ?, ?, 'active', ?, '[]', ?, ?)""",
                (
                    canonical_entity_id, entity_name, entity_type,
                    jurisdiction, registration_number,
                    now, latest_report_id,
                    findings_summary, risk_score, risk_level,
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

    def get_case(self, canonical_entity_id: str) -> dict[str, Any] | None:
        """Get a single DD case by canonical_entity_id."""
        conn = self._get_conn()
        row = conn.execute(
            "SELECT * FROM dd_cases WHERE canonical_entity_id = ?",
            (canonical_entity_id,),
        ).fetchone()
        if row is None:
            return None
        return dict(row)

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
        return [dict(r) for r in rows]

    def list_by_status(self, status: str = "active", limit: int = 100) -> list[dict[str, Any]]:
        """List cases by status."""
        conn = self._get_conn()
        rows = conn.execute(
            "SELECT * FROM dd_cases WHERE status = ? ORDER BY last_run_at DESC LIMIT ?",
            (status, limit),
        ).fetchall()
        return [dict(r) for r in rows]

    def list_all(self, limit: int = 100) -> list[dict[str, Any]]:
        """List all cases, newest first."""
        conn = self._get_conn()
        rows = conn.execute(
            "SELECT * FROM dd_cases ORDER BY last_run_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]

    def update_status(self, canonical_entity_id: str, status: str) -> None:
        """Update case status (active/dormant/archived)."""
        conn = self._get_conn()
        conn.execute(
            "UPDATE dd_cases SET status = ?, updated_at = ? WHERE canonical_entity_id = ?",
            (status, time.time(), canonical_entity_id),
        )
        conn.commit()

    def delete_case(self, canonical_entity_id: str) -> bool:
        """Delete a case and its cross-references."""
        conn = self._get_conn()
        conn.execute("DELETE FROM dd_cases WHERE canonical_entity_id = ?", (canonical_entity_id,))
        conn.execute(
            "DELETE FROM dd_cross_references WHERE source_entity = ? OR target_entity = ?",
            (canonical_entity_id, canonical_entity_id),
        )
        conn.commit()
        return conn.total_changes > 0

    # ── Cross-references ─────────────────────────────────────────────────

    def add_cross_reference(
        self,
        source_entity: str,
        target_entity: str,
        relationship: str,
        finding_summary: str = "",
    ) -> None:
        """Record a cross-reference between two entities.

        Called after a DD run when the report mentions other entities
        that have their own DD cases in the vault.
        """
        conn = self._get_conn()
        now = time.time()
        try:
            conn.execute(
                """INSERT OR REPLACE INTO dd_cross_references
                    (source_entity, target_entity, relationship, finding_summary, discovered_at)
                VALUES (?, ?, ?, ?, ?)""",
                (source_entity, target_entity, relationship, finding_summary, now),
            )
            conn.commit()
        except Exception as e:
            logger.debug("[dd_vault] cross-reference failed: %s", e)

    def get_cross_references(self, canonical_entity_id: str) -> list[dict[str, Any]]:
        """Get all cross-references for an entity (both directions)."""
        conn = self._get_conn()
        rows = conn.execute(
            """SELECT * FROM dd_cross_references
               WHERE source_entity = ? OR target_entity = ?
               ORDER BY discovered_at DESC""",
            (canonical_entity_id, canonical_entity_id),
        ).fetchall()
        return [dict(r) for r in rows]

    def get_related_cases(self, canonical_entity_id: str) -> list[dict[str, Any]]:
        """Get all DD cases related to this entity via cross-references."""
        refs = self.get_cross_references(canonical_entity_id)
        related_ids = set()
        for ref in refs:
            if ref["source_entity"] == canonical_entity_id:
                related_ids.add(ref["target_entity"])
            else:
                related_ids.add(ref["source_entity"])

        cases = []
        for rid in related_ids:
            case = self.get_case(rid)
            if case:
                cases.append(case)
        return cases

    # ── Stats ────────────────────────────────────────────────────────────

    def stats(self) -> dict[str, Any]:
        """Get aggregate statistics about the DD vault."""
        conn = self._get_conn()
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


def get_vault() -> DDVault:
    """Get or create the DD vault singleton."""
    global _vault_instance
    if _vault_instance is None:
        _vault_instance = DDVault()
    return _vault_instance
