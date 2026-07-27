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

-- R-F3158 — the tenant's Art. 10 position for criminal-offence data: which
-- DPA 2018 Sch. 1 condition is relied on, and the appropriate policy document
-- that condition requires (Sch. 1 Pt 4 para 5). One row per tenant; absence
-- means criminal-offence data may not be held for that tenant at all.
CREATE TABLE IF NOT EXISTS vetting_art10_positions (
    tenant_id        TEXT PRIMARY KEY,
    condition_code   TEXT NOT NULL,
    apd_reference    TEXT NOT NULL DEFAULT '',
    apd_review_date  TEXT,
    dpia_reference   TEXT NOT NULL DEFAULT '',
    determined_by    TEXT NOT NULL DEFAULT '',
    recorded_at      TEXT NOT NULL
);

-- R-F3178 — scoped applicant/referee invite links. token_hash ONLY: the
-- plaintext is returned once at mint time and never stored, so a database read
-- cannot recover a working link. Indexed on the hash because that is the sole
-- lookup path (a presented token), and tenant/case are carried as columns so a
-- redeemed token still resolves to exactly one scope.
CREATE TABLE IF NOT EXISTS vetting_invites (
    invite_id     TEXT PRIMARY KEY,
    tenant_id     TEXT NOT NULL,
    case_id       TEXT NOT NULL,
    kind          TEXT NOT NULL,
    token_hash    TEXT NOT NULL UNIQUE,
    entry_id      TEXT NOT NULL DEFAULT '',
    referee_name  TEXT NOT NULL DEFAULT '',
    referee_email TEXT NOT NULL DEFAULT '',
    expires_at    TEXT NOT NULL,
    created_at    TEXT NOT NULL,
    revoked_at    TEXT NOT NULL DEFAULT '',
    used_count    INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_vetting_invites_case
    ON vetting_invites(tenant_id, case_id, created_at DESC);

-- R-F3203 — the verification request ledger: the progress sheet's
-- Code / Request sent / Reply rec. columns. `overdue` is deliberately NOT a
-- column. It is a function of (sent_at, as_of, policy) and is derived on read,
-- so it cannot drift out of date behind a changing file the way the cached
-- assessment verdict did (R-F3172).
CREATE TABLE IF NOT EXISTS vetting_requests (
    request_id  TEXT PRIMARY KEY,
    tenant_id   TEXT NOT NULL,
    case_id     TEXT NOT NULL,
    code        TEXT NOT NULL,
    sent_to     TEXT NOT NULL,
    sent_at     TEXT NOT NULL,
    status      TEXT NOT NULL,
    entry_id    TEXT NOT NULL DEFAULT '',
    channel     TEXT NOT NULL DEFAULT '',
    invite_id   TEXT NOT NULL DEFAULT '',
    replied_at  TEXT NOT NULL DEFAULT '',
    chases      TEXT NOT NULL DEFAULT '',
    note        TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_vetting_requests_case
    ON vetting_requests(tenant_id, case_id, sent_at DESC);
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

    def save(self, case: VettingCase, *, mark_stale: bool = True) -> VettingCase:
        """Persist mutations (uploads, career entries) to an existing case.

        Scoped by (tenant_id, case_id): an UPDATE naming a case owned by
        another tenant matches zero rows and raises, rather than silently
        writing nothing and reporting success.

        R-F3172 — `mark_stale` DEFAULTS TO TRUE, and that default is the whole
        point. Any write changes the file, so the cached assessment now
        describes something that no longer exists. Putting the invalidation
        here rather than at each call site means a future writer that forgets
        about it gets the SAFE outcome (verdict marked stale) instead of the
        unsafe one (a clean verdict surviving the change that invalidated it).
        Only the assessment cache itself passes mark_stale=False, because it is
        the one writer that has just recomputed the truth.
        """
        if mark_stale and not case.assessment_stale:
            case = case.model_copy(update={"assessment_stale": True})
        with self._connect() as connection:
            # R-F3266 — the pack COLUMNS are written here too, from the
            # manifest. They were previously set once at create() and never
            # again, which was invisible only because nothing could change a
            # manifest. `list_cases` builds every queue card from these
            # columns, so a pack migration that updated case_json alone would
            # move the governing rules while every card kept reporting the
            # version the case was created on. The manifest is the authority;
            # the columns are a read index over it and must follow it.
            cursor = connection.execute(
                "UPDATE vetting_cases SET case_json = ?, updated_at = ?, "
                "pack_id = COALESCE(?, pack_id), "
                "pack_version = COALESCE(?, pack_version) "
                "WHERE tenant_id = ? AND case_id = ?",
                (case.model_dump_json(), _now_iso(),
                 case.manifest.pack_id if case.manifest else None,
                 case.manifest.pack_version if case.manifest else None,
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
                # R-F3168 — enough for a card view without re-assessing.
                # last_status is a CACHE: "" means never assessed, which the UI
                # must render as UNKNOWN, not as clean.
                "last_status": body.get("last_status", ""),
                "last_assessed_at": body.get("last_assessed_at", ""),
                "last_blockers": body.get("last_blockers", 0),
                # R-F3172 — the file changed after the cached verdict was
                # computed, so that verdict describes a file that no longer
                # exists. The UI must render this, not the stale status.
                "assessment_stale": bool(body.get("assessment_stale", False)),
                "document_count": len(body.get("documents", []) or []),
                "decision_count": len(body.get("decisions", []) or []),
                "outcome": body.get("outcome", "PENDING"),
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

    # ── R-F3158: the tenant's Art. 10 position ────────────────────────────
    def set_art10_position(self, position) -> None:
        """Record (or replace) this tenant's criminal-offence-data position."""
        from .legal_basis import Art10Position

        if not isinstance(position, Art10Position):
            raise TypeError("position must be an Art10Position")
        if not position.tenant_id:
            raise CasePersistenceError("tenant required")
        with self._connect() as connection:
            connection.execute(
                "INSERT INTO vetting_art10_positions (tenant_id, condition_code, "
                "apd_reference, apd_review_date, dpia_reference, determined_by, "
                "recorded_at) VALUES (?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(tenant_id) DO UPDATE SET "
                "condition_code=excluded.condition_code, "
                "apd_reference=excluded.apd_reference, "
                "apd_review_date=excluded.apd_review_date, "
                "dpia_reference=excluded.dpia_reference, "
                "determined_by=excluded.determined_by, "
                "recorded_at=excluded.recorded_at",
                (position.tenant_id, position.condition.value,
                 position.apd_reference,
                 position.apd_review_date.isoformat() if position.apd_review_date else None,
                 position.dpia_reference, position.determined_by, _now_iso()),
            )
            connection.commit()

    def get_art10_position(self, tenant_id: str):
        """The tenant's recorded position, or None. None means criminal-offence
        data may not be held for this tenant."""
        from datetime import date as _date

        from .legal_basis import Art10Position, Sch1Condition

        if not tenant_id:
            return None
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM vetting_art10_positions WHERE tenant_id = ?",
                (tenant_id,),
            ).fetchone()
        if row is None:
            return None
        try:
            condition = Sch1Condition(row["condition_code"])
        except ValueError:
            # A stored code we no longer recognise is NOT treated as a valid
            # position: an unrecognised condition cannot be demonstrated.
            return None
        review = row["apd_review_date"]
        return Art10Position(
            tenant_id=row["tenant_id"],
            condition=condition,
            apd_reference=row["apd_reference"] or "",
            apd_review_date=_date.fromisoformat(review) if review else None,
            dpia_reference=row["dpia_reference"] or "",
            determined_by=row["determined_by"] or "",
        )

    # ── R-F3178: invite links ─────────────────────────────────────────────
    def save_invite(self, invite) -> None:
        with self._connect() as connection:
            connection.execute(
                "INSERT INTO vetting_invites (invite_id, tenant_id, case_id, "
                "kind, token_hash, entry_id, referee_name, referee_email, "
                "expires_at, created_at, revoked_at, used_count) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (invite.invite_id, invite.tenant_id, invite.case_id,
                 invite.kind.value, invite.token_hash, invite.entry_id,
                 invite.referee_name, invite.referee_email, invite.expires_at,
                 invite.created_at, invite.revoked_at, invite.used_count),
            )
            connection.commit()

    def _row_to_invite(self, row):
        from .invites import Invite, InviteKind
        return Invite(
            invite_id=row["invite_id"], tenant_id=row["tenant_id"],
            case_id=row["case_id"], kind=InviteKind(row["kind"]),
            token_hash=row["token_hash"], entry_id=row["entry_id"] or "",
            referee_name=row["referee_name"] or "",
            referee_email=row["referee_email"] or "",
            expires_at=row["expires_at"], created_at=row["created_at"],
            revoked_at=row["revoked_at"] or "", used_count=row["used_count"] or 0,
        )

    def get_invite_by_token_hash(self, hashed: str):
        """The ONLY lookup a presented token can perform.

        Deliberately not `get_invite(invite_id)` from an unauthenticated path:
        an id is guessable-ish and appears in employer-facing responses, while
        the hash requires possession of the token itself.
        """
        if not hashed:
            return None
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM vetting_invites WHERE token_hash = ?", (hashed,),
            ).fetchone()
        return self._row_to_invite(row) if row is not None else None

    def list_invites(self, tenant_id: str, case_id: str) -> list:
        if not tenant_id:
            return []
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM vetting_invites WHERE tenant_id = ? AND case_id = ? "
                "ORDER BY created_at DESC", (tenant_id, case_id),
            ).fetchall()
        return [self._row_to_invite(r) for r in rows]

    def revoke_invite(self, tenant_id: str, invite_id: str, when: str) -> bool:
        with self._connect() as connection:
            cursor = connection.execute(
                "UPDATE vetting_invites SET revoked_at = ? "
                "WHERE tenant_id = ? AND invite_id = ? AND revoked_at = ''",
                (when, tenant_id, invite_id),
            )
            connection.commit()
        return cursor.rowcount > 0

    def record_invite_use(self, token_hash_value: str) -> None:
        with self._connect() as connection:
            connection.execute(
                "UPDATE vetting_invites SET used_count = used_count + 1 "
                "WHERE token_hash = ?", (token_hash_value,),
            )
            connection.commit()

    # ── R-F3203: verification requests ────────────────────────────────────
    def save_request(self, tenant_id: str, request) -> None:
        with self._connect() as connection:
            connection.execute(
                "INSERT OR REPLACE INTO vetting_requests (request_id, tenant_id, "
                "case_id, code, sent_to, sent_at, status, entry_id, channel, "
                "invite_id, replied_at, chases, note) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (request.request_id, tenant_id, request.case_id,
                 request.code.value, request.sent_to, request.sent_at,
                 request.status.value, request.entry_id, request.channel,
                 request.invite_id, request.replied_at, request.chases,
                 request.note),
            )
            connection.commit()

    def list_requests(self, tenant_id: str, case_id: str) -> list:
        from .requests import RequestCode, RequestStatus, VerificationRequest

        if not tenant_id:
            return []
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM vetting_requests WHERE tenant_id = ? AND case_id = ? "
                "ORDER BY sent_at DESC", (tenant_id, case_id),
            ).fetchall()
        return [
            VerificationRequest(
                request_id=r["request_id"], case_id=r["case_id"],
                code=RequestCode(r["code"]), sent_to=r["sent_to"],
                sent_at=r["sent_at"], status=RequestStatus(r["status"]),
                entry_id=r["entry_id"] or "", channel=r["channel"] or "",
                invite_id=r["invite_id"] or "", replied_at=r["replied_at"] or "",
                chases=r["chases"] or "", note=r["note"] or "",
            ) for r in rows
        ]

    def update_request_status(
        self, tenant_id: str, request_id: str, status: str, replied_at: str,
    ) -> bool:
        with self._connect() as connection:
            cursor = connection.execute(
                "UPDATE vetting_requests SET status = ?, replied_at = ? "
                "WHERE tenant_id = ? AND request_id = ?",
                (status, replied_at, tenant_id, request_id),
            )
            connection.commit()
        return cursor.rowcount > 0

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
