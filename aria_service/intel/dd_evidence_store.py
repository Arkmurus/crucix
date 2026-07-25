"""Append-only persistence for canonical DD evidence records.

R-F3083 turns the R-F3069 in-memory contract into a durable truth boundary.
Answered source outcomes are accepted only after their declared content hash
has been recomputed from the supplied raw bytes.  Permitted snapshots are
stored by content hash; prohibited snapshots are verified in memory and are
not retained.
"""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import tempfile
import threading
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator, Mapping

from .dd_evidence_standard import (
    EvidenceRecord,
    RetrievalOutcome,
    SnapshotPolicy,
)
from .engine_wiring import wire_failure, wire_success


_ANSWERED_OUTCOMES = frozenset({
    RetrievalOutcome.SUCCESS,
    RetrievalOutcome.ZERO_RESULTS,
    RetrievalOutcome.NO_MATCH,
    RetrievalOutcome.PARSER_FAILED,
})


class EvidencePersistenceError(ValueError):
    """Raised when evidence cannot be persisted without weakening integrity."""


@dataclass(frozen=True)
class PersistenceResult:
    """Outcome of one append attempt."""

    evidence_id: str
    status: str
    content_hash_verified: bool
    artifact_retained: bool

    def as_dict(self) -> dict[str, Any]:
        """Return a JSON-safe response."""
        return {
            "evidence_id": self.evidence_id,
            "status": self.status,
            "content_hash_verified": self.content_hash_verified,
            "artifact_retained": self.artifact_retained,
        }


def _default_db_path() -> Path:
    configured = os.environ.get("ARIA_DD_EVIDENCE_DB", "").strip()
    if configured:
        return Path(configured)
    if Path("/data").is_dir():
        return Path("/data/dd_evidence.db")
    return Path(__file__).with_name("_local_dd_evidence.db")


def _default_artifact_dir() -> Path:
    configured = os.environ.get("ARIA_DD_EVIDENCE_ARTIFACT_DIR", "").strip()
    if configured:
        return Path(configured)
    if Path("/data").is_dir():
        return Path("/data/dd_evidence_artifacts")
    return Path(__file__).with_name("_local_dd_evidence_artifacts")


_SCHEMA = """
CREATE TABLE IF NOT EXISTS dd_evidence (
    evidence_id            TEXT PRIMARY KEY,
    tenant_id              TEXT NOT NULL,
    case_id                TEXT NOT NULL,
    source_attempt_id      TEXT NOT NULL,
    record_json            TEXT NOT NULL,
    record_hash            TEXT NOT NULL,
    content_hash_verified  INTEGER NOT NULL CHECK(content_hash_verified IN (0, 1)),
    artifact_retained      INTEGER NOT NULL CHECK(artifact_retained IN (0, 1)),
    artifact_path          TEXT,
    stored_at              TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (tenant_id, source_attempt_id)
);
CREATE INDEX IF NOT EXISTS idx_dd_evidence_case
    ON dd_evidence(tenant_id, case_id, stored_at);
"""
_SCHEMA_LOCK = threading.Lock()


def _canonical_json(record: EvidenceRecord) -> str:
    return json.dumps(
        record.as_dict(),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class DDEvidenceStore:
    """SQLite metadata plus content-addressed raw evidence artifacts."""

    def __init__(
        self,
        db_path: str | os.PathLike[str] | None = None,
        artifact_dir: str | os.PathLike[str] | None = None,
    ) -> None:
        self.db_path = Path(db_path) if db_path is not None else _default_db_path()
        self.artifact_dir = (
            Path(artifact_dir) if artifact_dir is not None
            else _default_artifact_dir()
        )
        self._ensure_schema()

    def _new_connection(self) -> sqlite3.Connection:
        """Open one fully configured connection or close it on setup failure."""
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
        """Initialise WAL and schema once per store, outside request races."""
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

    def _retain_artifact(self, content_hash: str, artifact: bytes) -> Path:
        self.artifact_dir.mkdir(parents=True, exist_ok=True)
        path = self.artifact_dir / content_hash
        temporary_path: Path | None = None
        try:
            descriptor, temporary_name = tempfile.mkstemp(
                prefix=".pending-", dir=self.artifact_dir,
            )
            temporary_path = Path(temporary_name)
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(artifact)
                stream.flush()
                os.fsync(stream.fileno())
            try:
                os.link(temporary_path, path)
            except FileExistsError:
                if _sha256(path.read_bytes()) != content_hash:
                    raise EvidencePersistenceError(
                        "existing content-addressed artifact failed integrity verification")
        finally:
            if temporary_path is not None:
                temporary_path.unlink(missing_ok=True)
        return path

    def append(
        self,
        candidate: EvidenceRecord | Mapping[str, Any],
        artifact: bytes | None,
    ) -> PersistenceResult:
        """Validate and append one record without permitting mutation."""
        record = (
            candidate if isinstance(candidate, EvidenceRecord)
            else EvidenceRecord.from_mapping(candidate)
        )
        answered = record.retrieval_outcome in _ANSWERED_OUTCOMES
        if answered and artifact is None:
            self._fail(record, "answered evidence requires raw bytes for hash verification")
        if not answered and artifact is not None:
            self._fail(record, "failed retrieval outcomes cannot carry raw evidence bytes")

        verified = False
        if artifact is not None:
            actual_hash = _sha256(artifact)
            if actual_hash != record.content_hash:
                self._fail(record, "declared content_hash does not match raw evidence bytes")
            verified = True

        artifact_path: Path | None = None
        if (
            artifact is not None
            and record.snapshot_policy == SnapshotPolicy.PERMITTED
        ):
            artifact_path = self._retain_artifact(record.content_hash or "", artifact)

        record_json = _canonical_json(record)
        record_hash = _sha256(record_json.encode("utf-8"))
        try:
            with self._connect() as connection:
                connection.execute("BEGIN IMMEDIATE")
                existing = connection.execute(
                    "SELECT * FROM dd_evidence WHERE evidence_id = ? "
                    "OR (tenant_id = ? AND source_attempt_id = ?)",
                    (record.evidence_id, record.tenant_id, record.source_attempt_id),
                ).fetchone()
                if existing is not None:
                    same = (
                        existing["evidence_id"] == record.evidence_id
                        and existing["record_hash"] == record_hash
                        and bool(existing["content_hash_verified"]) == verified
                    )
                    connection.rollback()
                    if same:
                        return PersistenceResult(
                            record.evidence_id,
                            "already_present",
                            verified,
                            bool(existing["artifact_retained"]),
                        )
                    self._fail(
                        record,
                        "evidence_id or source_attempt_id already exists with different content",
                    )
                connection.execute(
                    """
                    INSERT INTO dd_evidence (
                        evidence_id, tenant_id, case_id, source_attempt_id,
                        record_json, record_hash, content_hash_verified,
                        artifact_retained, artifact_path
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        record.evidence_id,
                        record.tenant_id,
                        record.case_id,
                        record.source_attempt_id,
                        record_json,
                        record_hash,
                        int(verified),
                        int(artifact_path is not None),
                        str(artifact_path) if artifact_path is not None else None,
                    ),
                )
                connection.commit()
        except EvidencePersistenceError:
            raise
        except Exception as exc:
            self._fail(record, f"evidence persistence failed: {exc}")

        wire_success(
            module="dd_evidence_store",
            summary=f"Append-only evidence persisted for {record.source_id}",
            source_id=f"dd_evidence_store:{record.evidence_id}",
        )
        return PersistenceResult(
            record.evidence_id,
            "stored",
            verified,
            artifact_path is not None,
        )

    def get(self, tenant_id: str, evidence_id: str) -> dict[str, Any] | None:
        """Read one record within an explicit tenant boundary."""
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM dd_evidence WHERE tenant_id = ? AND evidence_id = ?",
                (tenant_id, evidence_id),
            ).fetchone()
        if row is None:
            return None
        record_json = str(row["record_json"])
        metadata_valid = _sha256(record_json.encode("utf-8")) == row["record_hash"]
        artifact_valid: bool | None = None
        if row["artifact_retained"]:
            path = Path(str(row["artifact_path"]))
            record = json.loads(record_json)
            artifact_valid = (
                path.is_file()
                and _sha256(path.read_bytes()) == record.get("content_hash")
            )
        return {
            "record": json.loads(record_json),
            "integrity": {
                "metadata_valid": metadata_valid,
                "content_hash_verified": bool(row["content_hash_verified"]),
                "artifact_retained": bool(row["artifact_retained"]),
                "artifact_valid": artifact_valid,
            },
        }

    @staticmethod
    def _fail(record: EvidenceRecord, detail: str) -> None:
        wire_failure(
            module="dd_evidence_store",
            detail=detail,
            gap_type="engine_failure",
            source=f"dd_evidence_store:{record.evidence_id}",
        )
        raise EvidencePersistenceError(detail)


_STORE: DDEvidenceStore | None = None
_STORE_LOCK = threading.Lock()


def get_evidence_store() -> DDEvidenceStore:
    """Return the process-local store configured for the durable volume."""
    global _STORE
    if _STORE is None:
        with _STORE_LOCK:
            if _STORE is None:
                _STORE = DDEvidenceStore()
    return _STORE
