"""R-F3137 — tenant-scoped persistent case store for the vetting module.

Replaces the `dict[str, VettingCase]` stand-in the engine shipped with. The
sqlite/WAL shape here deliberately mirrors `intel/dd_evidence_store.py`
(R-F3083) rather than inventing a second persistence idiom: same connection
setup, same busy_timeout, same schema-once-under-a-lock discipline.

── Why the tenant boundary is in the PRIMARY KEY ──────────────────────────
Every row is keyed `(tenant_id, case_id)`, so there is no way to express a
read that does not name a tenant. That is the point. The alternative — a
`case_id` primary key plus a `WHERE tenant_id = ?` that each call site
remembers to add — is exactly the shape that produced five cross-tenant DD
leaks: the guard lives in the caller, so a new caller ships without it.
Here the schema refuses to answer an un-scoped question.

Reads are FAIL-CLOSED: a case that exists under another tenant is reported
as absent (None → 404 at the route layer), never as a permission error. A
403 would confirm the case exists, which for vetting data leaks the fact
that a named person is under screening by a named employer — a disclosure
that is itself harmful even without the file contents.
"""

from __future__ import annotations

import json
import os
import sqlite3
import threading
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Iterator

from .models import CaseManifest, VettingCase
from .packs.base import PackRegistry


class CaseNotFound(LookupError):
    """Raised by mutating helpers; reads return None instead."""


class CasePersistenceError(RuntimeError):
    pass


def _default_db_path() -> Path:
    configured = os.environ.get("ARIA_VETTING_DB", "").strip()
    if configured:
        return Path(configured)
    if Path("/data").is_dir():
        return Path("/data/vetting_cases.db")
    return Path(__file__).with_name("_local_vetting_cases.db")


_SCHEMA = """
CREATE TABLE IF NOT EXISTS vetting_cases (
    tenant_id   TEXT NOT NULL,
    case_id     TEXT NOT NULL,
    case_json   TEXT NOT NULL,
    pack_id     TEXT NOT NULL,
    pack_version TEXT NOT NULL,
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL,
    PRIMARY KEY (tenant_id, case_id)
);
CREATE INDEX IF NOT EXISTS idx_vetting_cases_tenant
    ON vetting_cases(tenant_id, updated_at DESC);

-- R-F3155 — the deletable half of crypto-shredding (Art. 17). Document bytes
-- are encrypted with this key before they reach the append-only evidence
-- store; destroying the row makes that ciphertext irrecoverable, which is what
-- turns "we cannot delete from an append-only store" into an effective
-- erasure. Deliberately a SEPARATE table from vetting_cases: the key must be
-- destroyable independently, and must survive nothing.
CREATE TABLE IF NOT EXISTS vetting_case_keys (
    tenant_id   TEXT NOT NULL,
    case_id     TEXT NOT NULL,
    case_key    BLOB NOT NULL,
    created_at  TEXT NOT NULL,
    PRIMARY KEY (tenant_id, case_id)
);
"""
_SCHEMA_LOCK = threading.Lock()


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


class VettingCaseStore:
    """SQLite-backed, tenant-scoped case repository."""

    def __init__(self, db_path: str | os.PathLike[str] | None = None) -> None:
        self.db_path = Path(db_path) if db_path is not None else _default_db_path()
        self._ensure_schema()

    # ── connection plumbing (mirrors dd_evidence_store) ───────────────────
    def _new_connection(self) -> sqlite3.Connection:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(str(self.db_path), timeout=15)
        try:
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA busy_timeout=15000")
            connection.execute("PRAGMA synchronous=FULL")
            return connection
        except Exception:
            connection.close()
            raise

    def _ensure_schema(self) -> None:
        with _SCHEMA_LOCK:
            connection = self._new_connection()
            try:
                connection.execute("PRAGMA journal_mode=WAL")
                connection.executescript(_SCHEMA)
                connection.commit()
            finally:
                connection.close()

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        connection = self._new_connection()
        try:
            yield connection
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    # ── writes ────────────────────────────────────────────────────────────
    def create(
        self,
        case: VettingCase,
        registry: PackRegistry,
        pack_id: str,
    ) -> VettingCase:
        """Pin the case to the latest PRODUCTION pack and persist it.

        The manifest is set HERE, never by the caller — an API client that
        could choose its own pack could choose a DRAFT one, and a DRAFT pack
        is by definition not legally reviewed for the jurisdiction it claims.
        `latest_usable()` refuses anything that is not PRODUCTION.
        """
        if case.manifest is not None:
            raise ValueError("manifest is set by the store, not the caller")
        pack = registry.latest_usable(pack_id)
        pinned = case.model_copy(update={"manifest": CaseManifest(
            pack_id=pack.pack_id,
            pack_version=pack.version,
            pack_hash=pack.content_hash(),
        )})
        now = _now_iso()
        try:
            with self._connect() as connection:
                connection.execute(
                    "INSERT INTO vetting_cases (tenant_id, case_id, case_json, "
                    "pack_id, pack_version, created_at, updated_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (pinned.tenant_id, pinned.case_id, pinned.model_dump_json(),
                     pack.pack_id, pack.version, now, now),
                )
                connection.commit()
        except sqlite3.IntegrityError as exc:
            raise CasePersistenceError(
                f"case '{case.case_id}' already exists for this tenant"
            ) from exc
        return pinned

    def save(self, case: VettingCase) -> VettingCase:
        """Persist mutations (uploads, career entries) to an existing case.

        Scoped by (tenant_id, case_id): an UPDATE naming a case owned by
        another tenant matches zero rows and raises, rather than silently
        writing nothing and reporting success.
        """
        with self._connect() as connection:
            cursor = connection.execute(
                "UPDATE vetting_cases SET case_json = ?, updated_at = ? "
                "WHERE tenant_id = ? AND case_id = ?",
                (case.model_dump_json(), _now_iso(),
                 case.tenant_id, case.case_id),
            )
            if cursor.rowcount == 0:
                raise CaseNotFound(
                    f"case '{case.case_id}' not found for this tenant"
                )
            connection.commit()
        return case

    # ── reads (fail-closed) ───────────────────────────────────────────────
    def get(self, tenant_id: str, case_id: str) -> VettingCase | None:
        """Return the case, or None if it does not exist FOR THIS TENANT.

        A case belonging to someone else is indistinguishable from a case
        that does not exist. See the module docstring for why that is the
        correct disclosure, not merely the convenient one.
        """
        if not tenant_id:
            return None
        with self._connect() as connection:
            row = connection.execute(
                "SELECT case_json FROM vetting_cases "
                "WHERE tenant_id = ? AND case_id = ?",
                (tenant_id, case_id),
            ).fetchone()
        if row is None:
            return None
        return VettingCase.model_validate(json.loads(row["case_json"]))

    def list_cases(self, tenant_id: str, limit: int = 100) -> list[dict]:
        """Summaries for one tenant's case list. Never returns case bodies."""
        if not tenant_id:
            return []
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT case_id, pack_id, pack_version, created_at, updated_at, "
                "case_json FROM vetting_cases WHERE tenant_id = ? "
                "ORDER BY updated_at DESC LIMIT ?",
                (tenant_id, max(1, min(int(limit), 500))),
            ).fetchall()
        out: list[dict] = []
        for row in rows:
            body = json.loads(row["case_json"])
            out.append({
                "case_id": row["case_id"],
                "applicant_name": body.get("applicant_name", ""),
                "pack_id": row["pack_id"],
                "pack_version": row["pack_version"],
                "created_at": row["created_at"],
                "updated_at": row["updated_at"],
            })
        return out

    def delete(self, tenant_id: str, case_id: str) -> bool:
        """Delete the case AND destroy its encryption key (R-F3155).

        Both in one transaction: a case row removed while its key survived
        would leave recoverable personal data with nothing pointing at it —
        the worst of both, undiscoverable and un-erased.
        """
        if not tenant_id:
            return False
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            cursor = connection.execute(
                "DELETE FROM vetting_cases WHERE tenant_id = ? AND case_id = ?",
                (tenant_id, case_id),
            )
            connection.execute(
                "DELETE FROM vetting_case_keys WHERE tenant_id = ? AND case_id = ?",
                (tenant_id, case_id),
            )
            connection.commit()
        return cursor.rowcount > 0

    # ── R-F3155: per-case encryption keys ─────────────────────────────────
    def get_or_create_case_key(self, tenant_id: str, case_id: str) -> bytes:
        """The case's data key, minted on first document upload."""
        from .crypto import new_case_key

        if not tenant_id:
            raise CaseNotFound("tenant required")
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT case_key FROM vetting_case_keys "
                "WHERE tenant_id = ? AND case_id = ?",
                (tenant_id, case_id),
            ).fetchone()
            if row is not None:
                connection.commit()
                return bytes(row["case_key"])
            key = new_case_key()
            connection.execute(
                "INSERT INTO vetting_case_keys (tenant_id, case_id, case_key, "
                "created_at) VALUES (?, ?, ?, ?)",
                (tenant_id, case_id, key, _now_iso()),
            )
            connection.commit()
        return key

    def get_case_key(self, tenant_id: str, case_id: str) -> bytes | None:
        """The key, or None when it has been destroyed (i.e. content erased)."""
        if not tenant_id:
            return None
        with self._connect() as connection:
            row = connection.execute(
                "SELECT case_key FROM vetting_case_keys "
                "WHERE tenant_id = ? AND case_id = ?",
                (tenant_id, case_id),
            ).fetchone()
        return bytes(row["case_key"]) if row is not None else None

    def destroy_case_key(self, tenant_id: str, case_id: str) -> bool:
        """Crypto-shred: make this case's stored documents irrecoverable."""
        if not tenant_id:
            return False
        with self._connect() as connection:
            cursor = connection.execute(
                "DELETE FROM vetting_case_keys WHERE tenant_id = ? AND case_id = ?",
                (tenant_id, case_id),
            )
            connection.commit()
        return cursor.rowcount > 0


_STORE: VettingCaseStore | None = None
_STORE_LOCK = threading.Lock()


def get_case_store() -> VettingCaseStore:
    global _STORE
    if _STORE is None:
        with _STORE_LOCK:
            if _STORE is None:
                _STORE = VettingCaseStore()
    return _STORE
