"""Canonical evidence contract for ARIA due-diligence.

R-F3069 establishes the dependency-light truth boundary that source adapters and
DD workers must target.  It deliberately makes retrieval outcome separate from
interpretation: a timeout, zero-result response, and policy-defined no-match are
different states and cannot be silently exchanged.

This first increment is additive.  The control-plane endpoints expose the
versioned contract and validate candidate records; subsequent R-numbers will
wrap individual adapters and persist accepted records.
"""
from __future__ import annotations

from dataclasses import MISSING, dataclass, field, fields
from datetime import datetime
from enum import Enum
import math
import re
from types import MappingProxyType
from typing import Any, Mapping
from uuid import UUID

from .engine_wiring import wire_failure, wire_success


EVIDENCE_SCHEMA_ID = "aria.dd.evidence"
EVIDENCE_SCHEMA_VERSION = "1.0.0"
VERDICT_FUNCTION_VERSION = "1.0.0"
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


class SourceAuthority(str, Enum):
    """Authority class of the source, independent of what it asserts."""

    PRIMARY_OFFICIAL = "primary_official"
    PRIMARY_ENTITY = "primary_entity"
    REGULATED_PROVIDER = "regulated_provider"
    COMMERCIAL_AGGREGATOR = "commercial_aggregator"
    REPUTABLE_MEDIA = "reputable_media"
    SECONDARY_OPEN_SOURCE = "secondary_open_source"
    USER_SUPPLIED = "user_supplied"


class RetrievalOutcome(str, Enum):
    """Terminal outcome of one source attempt."""

    SUCCESS = "success"
    ZERO_RESULTS = "zero_results"
    NO_MATCH = "no_match"
    ACCESS_DENIED = "access_denied"
    AUTH_FAILED = "authentication_failed"
    RATE_LIMITED = "rate_limited"
    TIMEOUT = "timeout"
    SOURCE_UNAVAILABLE = "source_unavailable"
    QUERY_REJECTED = "query_rejected"
    PARSER_FAILED = "parser_failed"
    ENTITY_UNRESOLVED = "entity_unresolved"


class SnapshotPolicy(str, Enum):
    """Whether retaining the raw source response is permitted."""

    PERMITTED = "permitted"
    PROHIBITED = "prohibited"
    NOT_APPLICABLE = "not_applicable"


class ConfigurationState(str, Enum):
    """Whether the evidence capability is permitted and configured to run.

    ``ACCESS_BASIS_MISSING`` deliberately lives on this axis.  It is a
    legal-governance configuration state: engineering may be ready while ARIA is
    still not permitted to acquire or retain the source.
    """

    CONFIGURED = "configured"
    NOT_CONFIGURED = "not_configured"
    NOT_APPLICABLE = "not_applicable"
    DISABLED = "disabled"
    ACCESS_BASIS_MISSING = "access_basis_missing"


class SourceState(str, Enum):
    """Condition of the source observation used for the attempt."""

    CURRENT = "current"
    STALE = "stale"
    DEGRADED = "degraded"
    UNAVAILABLE = "unavailable"
    UNKNOWN = "unknown"


class AttemptOutcome(str, Enum):
    """Operational outcome of consulting one configured capability."""

    NOT_ATTEMPTED = "not_attempted"
    SUCCEEDED = "succeeded"
    PARTIAL = "partial"
    TIMED_OUT = "timed_out"
    AUTH_REQUIRED = "auth_required"
    ACCESS_DENIED = "access_denied"
    SOURCE_FAILED = "source_failed"
    PARSER_FAILED = "parser_failed"


class MatchOutcome(str, Enum):
    """Entity/evidence resolution result, independent of source condition.

    ``AMBIGUOUS`` means two or more plausible candidates remain.
    ``UNRESOLVED`` means the resolver lacked enough evidence to resolve any
    candidate.  Neither is a negative match.
    """

    NOT_EVALUATED = "not_evaluated"
    MATCH = "match"
    NO_MATCH = "no_match"
    AMBIGUOUS = "ambiguous"
    UNRESOLVED = "unresolved"


class EvidenceVerdict(str, Enum):
    """Presentation-neutral verdict derived from the four status axes."""

    BLOCKED = "blocked"
    DEGRADED = "degraded"
    COMPLETED_MATCH = "completed_match"
    COMPLETED_NO_MATCH = "completed_no_match"
    COMPLETED_PARTIAL = "completed_partial"
    NOT_APPLICABLE = "not_applicable"
    NOT_RUN = "not_run"


_NON_CONFIGURED_STATES = frozenset({
    ConfigurationState.NOT_CONFIGURED,
    ConfigurationState.NOT_APPLICABLE,
    ConfigurationState.DISABLED,
    ConfigurationState.ACCESS_BASIS_MISSING,
})
_FAILED_ATTEMPTS = frozenset({
    AttemptOutcome.TIMED_OUT,
    AttemptOutcome.AUTH_REQUIRED,
    AttemptOutcome.ACCESS_DENIED,
    AttemptOutcome.SOURCE_FAILED,
    AttemptOutcome.PARSER_FAILED,
})
_EVALUATED_MATCHES = frozenset({
    MatchOutcome.MATCH,
    MatchOutcome.NO_MATCH,
    MatchOutcome.AMBIGUOUS,
    MatchOutcome.UNRESOLVED,
})


@dataclass(frozen=True)
class EvidenceAssessment:
    """Version-pinned orthogonal status of one evidence capability.

    Reports must persist all four axes and ``verdict_fn_version``.  Re-rendering
    a historical report must use the function version it was issued with rather
    than silently applying a later policy.
    """

    configuration_state: ConfigurationState
    source_state: SourceState
    attempt_outcome: AttemptOutcome
    match_outcome: MatchOutcome
    verdict_fn_version: str = VERDICT_FUNCTION_VERSION

    def validate(self) -> None:
        """Raise ``EvidenceContractError`` for an impossible combination."""
        errors: list[str] = []
        config = self.configuration_state
        source = self.source_state
        attempt = self.attempt_outcome
        match = self.match_outcome

        if self.verdict_fn_version != VERDICT_FUNCTION_VERSION:
            errors.append(
                f"verdict_fn_version must equal {VERDICT_FUNCTION_VERSION}")

        if config in _NON_CONFIGURED_STATES:
            if source != SourceState.UNKNOWN:
                errors.append(
                    f"{config.value} requires source_state=unknown")
            if attempt != AttemptOutcome.NOT_ATTEMPTED:
                errors.append(
                    f"{config.value} requires attempt_outcome=not_attempted")
            if match != MatchOutcome.NOT_EVALUATED:
                errors.append(
                    f"{config.value} requires match_outcome=not_evaluated")

        if config == ConfigurationState.CONFIGURED:
            if attempt == AttemptOutcome.NOT_ATTEMPTED:
                if match != MatchOutcome.NOT_EVALUATED:
                    errors.append(
                        "not_attempted requires match_outcome=not_evaluated")
            elif attempt in _FAILED_ATTEMPTS:
                if match != MatchOutcome.NOT_EVALUATED:
                    errors.append(
                        f"{attempt.value} requires match_outcome=not_evaluated")
            elif attempt == AttemptOutcome.SUCCEEDED:
                if match not in _EVALUATED_MATCHES:
                    errors.append(
                        "succeeded requires an evaluated match outcome")
            elif attempt == AttemptOutcome.PARTIAL:
                # A partial attempt may fail before matching, or may resolve only
                # the subset that answered.  Both states are truthful.
                pass

            if source == SourceState.UNAVAILABLE and attempt not in _FAILED_ATTEMPTS:
                errors.append(
                    "source_state=unavailable requires a failed attempt outcome")

        if match in _EVALUATED_MATCHES and attempt not in {
            AttemptOutcome.SUCCEEDED, AttemptOutcome.PARTIAL
        }:
            errors.append(
                "an evaluated match requires attempt_outcome=succeeded or partial")

        if errors:
            raise EvidenceContractError(errors)

    def derive_verdict(self) -> EvidenceVerdict:
        """Validate and derive the v1 verdict without consulting synthesis."""
        self.validate()
        config = self.configuration_state
        source = self.source_state
        attempt = self.attempt_outcome
        match = self.match_outcome

        if config == ConfigurationState.NOT_APPLICABLE:
            return EvidenceVerdict.NOT_APPLICABLE
        if config == ConfigurationState.DISABLED:
            return EvidenceVerdict.NOT_RUN
        if config in {
            ConfigurationState.NOT_CONFIGURED,
            ConfigurationState.ACCESS_BASIS_MISSING,
        }:
            return EvidenceVerdict.BLOCKED
        if attempt == AttemptOutcome.NOT_ATTEMPTED:
            return EvidenceVerdict.NOT_RUN
        if source == SourceState.UNAVAILABLE or attempt in _FAILED_ATTEMPTS:
            return EvidenceVerdict.BLOCKED
        if (
            source in {SourceState.STALE, SourceState.DEGRADED, SourceState.UNKNOWN}
            or attempt == AttemptOutcome.PARTIAL
        ):
            return EvidenceVerdict.DEGRADED
        if match == MatchOutcome.MATCH:
            return EvidenceVerdict.COMPLETED_MATCH
        if match == MatchOutcome.NO_MATCH:
            return EvidenceVerdict.COMPLETED_NO_MATCH
        return EvidenceVerdict.COMPLETED_PARTIAL


def evidence_assessment_matrix() -> tuple[dict[str, str], ...]:
    """Return the complete v1 mapping of every valid axis combination.

    Invalid combinations are deliberately absent.  Tests enumerate the entire
    Cartesian product and prove every combination is either represented here or
    rejected by ``EvidenceAssessment.validate``.
    """
    rows: list[dict[str, str]] = []
    for configuration in ConfigurationState:
        for source in SourceState:
            for attempt in AttemptOutcome:
                for match in MatchOutcome:
                    assessment = EvidenceAssessment(
                        configuration_state=configuration,
                        source_state=source,
                        attempt_outcome=attempt,
                        match_outcome=match,
                    )
                    try:
                        verdict = assessment.derive_verdict()
                    except EvidenceContractError:
                        continue
                    rows.append({
                        "configuration_state": configuration.value,
                        "source_state": source.value,
                        "attempt_outcome": attempt.value,
                        "match_outcome": match.value,
                        "verdict": verdict.value,
                        "verdict_fn_version": VERDICT_FUNCTION_VERSION,
                    })
    return tuple(rows)


_ANSWERED_OUTCOMES = frozenset({
    RetrievalOutcome.SUCCESS,
    RetrievalOutcome.ZERO_RESULTS,
    RetrievalOutcome.NO_MATCH,
    RetrievalOutcome.PARSER_FAILED,
})
_NO_PAYLOAD_OUTCOMES = frozenset({
    RetrievalOutcome.ACCESS_DENIED,
    RetrievalOutcome.AUTH_FAILED,
    RetrievalOutcome.RATE_LIMITED,
    RetrievalOutcome.TIMEOUT,
    RetrievalOutcome.SOURCE_UNAVAILABLE,
    RetrievalOutcome.QUERY_REJECTED,
    RetrievalOutcome.ENTITY_UNRESOLVED,
})


class EvidenceContractError(ValueError):
    """Raised when a candidate record violates the evidence contract."""

    def __init__(self, errors: list[str]):
        self.errors = tuple(errors)
        super().__init__("; ".join(errors))


def _required_text(value: Any, field_name: str, errors: list[str]) -> str:
    text = str(value or "").strip()
    if not text:
        errors.append(f"{field_name} is required")
    return text


def _uuid_text(value: Any, field_name: str, errors: list[str]) -> str:
    text = _required_text(value, field_name, errors)
    if text:
        try:
            UUID(text)
        except (TypeError, ValueError):
            errors.append(f"{field_name} must be a UUID")
    return text


def _utc_datetime(value: Any, field_name: str, errors: list[str]) -> str:
    text = _required_text(value, field_name, errors)
    if not text:
        return text
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            errors.append(f"{field_name} must include a UTC offset")
    except ValueError:
        errors.append(f"{field_name} must be an ISO-8601 datetime")
    return text


def _enum_value(enum_type, value: Any, field_name: str, errors: list[str]):
    try:
        return enum_type(value)
    except (TypeError, ValueError):
        allowed = ", ".join(item.value for item in enum_type)
        errors.append(f"{field_name} must be one of: {allowed}")
        return next(iter(enum_type))


def _freeze_json(value: Any) -> Any:
    """Recursively freeze a JSON-compatible value."""
    if isinstance(value, Mapping):
        return MappingProxyType({
            str(key): _freeze_json(item) for key, item in value.items()
        })
    if isinstance(value, (list, tuple)):
        return tuple(_freeze_json(item) for item in value)
    return value


def _thaw_json(value: Any) -> Any:
    """Return a detached JSON-safe copy of a recursively frozen value."""
    if isinstance(value, Mapping):
        return {str(key): _thaw_json(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_thaw_json(item) for item in value]
    return value


def _validate_json_value(value: Any, path: str, errors: list[str]) -> None:
    """Reject values that cannot be represented deterministically as JSON."""
    if value is None or isinstance(value, (str, bool, int)):
        return
    if isinstance(value, float):
        if not math.isfinite(value):
            errors.append(f"{path} must not contain NaN or infinity")
        return
    if isinstance(value, Mapping):
        for key, item in value.items():
            if not isinstance(key, str):
                errors.append(f"{path} keys must be strings")
                continue
            _validate_json_value(item, f"{path}.{key}", errors)
        return
    if isinstance(value, (list, tuple)):
        for index, item in enumerate(value):
            _validate_json_value(item, f"{path}[{index}]", errors)
        return
    errors.append(f"{path} contains a non-JSON value of type {type(value).__name__}")


@dataclass(frozen=True)
class EvidenceRecord:
    """Immutable, serialisable result of one source retrieval attempt."""

    evidence_id: str
    tenant_id: str
    case_id: str
    case_scope_version: int
    subject_entity_id: str
    source_attempt_id: str

    source_id: str
    source_authority: SourceAuthority
    retrieval_outcome: RetrievalOutcome
    retrieved_at: str
    request_fingerprint: str

    licence_policy_id: str
    access_method: str
    adapter_version: str
    parser_version: str
    schema_version: str = EVIDENCE_SCHEMA_VERSION

    source_record_id: str | None = None
    jurisdiction: str | None = None
    published_at: str | None = None
    effective_from: str | None = None
    effective_to: str | None = None
    content_hash: str | None = None
    raw_artifact_uri: str | None = None
    source_url: str | None = None
    snapshot_policy: SnapshotPolicy = SnapshotPolicy.NOT_APPLICABLE
    matching_policy_id: str | None = None
    query_identifiers: tuple[str, ...] = ()
    structured_payload: Mapping[str, Any] = field(default_factory=dict)
    warnings: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        """Return a JSON-safe representation without changing the record."""
        body = {
            item.name: _thaw_json(getattr(self, item.name))
            for item in fields(self)
        }
        body["source_authority"] = self.source_authority.value
        body["retrieval_outcome"] = self.retrieval_outcome.value
        body["snapshot_policy"] = self.snapshot_policy.value
        body["query_identifiers"] = list(self.query_identifiers)
        body["warnings"] = list(self.warnings)
        return body

    @classmethod
    def from_mapping(cls, candidate: Mapping[str, Any]) -> "EvidenceRecord":
        """Validate and construct a record from untrusted candidate data."""
        errors: list[str] = []
        if not isinstance(candidate, Mapping):
            raise EvidenceContractError(["record must be an object"])
        allowed_fields = {item.name for item in fields(cls)}
        unknown_fields = sorted(set(candidate) - allowed_fields)
        if unknown_fields:
            errors.append(
                "unknown fields are not allowed: " + ", ".join(unknown_fields))

        evidence_id = _uuid_text(candidate.get("evidence_id"), "evidence_id", errors)
        case_id = _uuid_text(candidate.get("case_id"), "case_id", errors)
        subject_entity_id = _uuid_text(
            candidate.get("subject_entity_id"), "subject_entity_id", errors)
        source_attempt_id = _uuid_text(
            candidate.get("source_attempt_id"), "source_attempt_id", errors)
        tenant_id = _required_text(candidate.get("tenant_id"), "tenant_id", errors)
        source_id = _required_text(candidate.get("source_id"), "source_id", errors)
        retrieved_at = _utc_datetime(
            candidate.get("retrieved_at"), "retrieved_at", errors)
        request_fingerprint = _required_text(
            candidate.get("request_fingerprint"), "request_fingerprint", errors)
        licence_policy_id = _required_text(
            candidate.get("licence_policy_id"), "licence_policy_id", errors)
        access_method = _required_text(
            candidate.get("access_method"), "access_method", errors)
        adapter_version = _required_text(
            candidate.get("adapter_version"), "adapter_version", errors)
        parser_version = _required_text(
            candidate.get("parser_version"), "parser_version", errors)

        source_authority = _enum_value(
            SourceAuthority, candidate.get("source_authority"),
            "source_authority", errors)
        retrieval_outcome = _enum_value(
            RetrievalOutcome, candidate.get("retrieval_outcome"),
            "retrieval_outcome", errors)
        snapshot_policy = _enum_value(
            SnapshotPolicy,
            candidate.get("snapshot_policy", SnapshotPolicy.NOT_APPLICABLE.value),
            "snapshot_policy",
            errors,
        )

        scope_version = candidate.get("case_scope_version")
        if not isinstance(scope_version, int) or isinstance(scope_version, bool) or scope_version < 1:
            errors.append("case_scope_version must be a positive integer")
            scope_version = 1

        schema_version = str(
            candidate.get("schema_version") or EVIDENCE_SCHEMA_VERSION).strip()
        if schema_version != EVIDENCE_SCHEMA_VERSION:
            errors.append(
                f"schema_version must equal {EVIDENCE_SCHEMA_VERSION}")

        content_hash_value = candidate.get("content_hash")
        content_hash = str(content_hash_value).strip().lower() if content_hash_value else None
        if content_hash and not _SHA256_RE.fullmatch(content_hash):
            errors.append("content_hash must be a lowercase SHA-256 hex digest")

        if request_fingerprint and not _SHA256_RE.fullmatch(request_fingerprint.lower()):
            errors.append(
                "request_fingerprint must be a SHA-256 hex digest")
        request_fingerprint = request_fingerprint.lower()

        raw_payload = candidate.get("structured_payload", {})
        if not isinstance(raw_payload, Mapping):
            errors.append("structured_payload must be an object")
            raw_payload = {}
        _validate_json_value(raw_payload, "structured_payload", errors)

        query_values = candidate.get("query_identifiers", ())
        if not isinstance(query_values, (list, tuple)):
            errors.append("query_identifiers must be a list")
            query_values = ()
        query_identifiers = tuple(
            str(item).strip() for item in query_values if str(item).strip())

        warning_values = candidate.get("warnings", ())
        if not isinstance(warning_values, (list, tuple)):
            errors.append("warnings must be a list")
            warning_values = ()
        warnings = tuple(str(item).strip() for item in warning_values if str(item).strip())

        raw_artifact_uri = (
            str(candidate.get("raw_artifact_uri")).strip()
            if candidate.get("raw_artifact_uri") else None)
        if retrieval_outcome in _ANSWERED_OUTCOMES and not content_hash:
            errors.append(
                f"content_hash is required when retrieval_outcome is "
                f"{retrieval_outcome.value}")
        if retrieval_outcome in _NO_PAYLOAD_OUTCOMES and raw_payload:
            errors.append(
                f"structured_payload must be empty when retrieval_outcome is "
                f"{retrieval_outcome.value}")
        if retrieval_outcome == RetrievalOutcome.TIMEOUT and raw_artifact_uri:
            errors.append("raw_artifact_uri is not allowed for a timeout")
        if snapshot_policy == SnapshotPolicy.PERMITTED and retrieval_outcome in _ANSWERED_OUTCOMES:
            if not raw_artifact_uri:
                errors.append(
                    "raw_artifact_uri is required when an answered response may be snapshotted")
        if snapshot_policy == SnapshotPolicy.PROHIBITED and raw_artifact_uri:
            errors.append(
                "raw_artifact_uri must be empty when snapshot_policy is prohibited")
        matching_policy_id = (
            str(candidate.get("matching_policy_id")).strip()
            if candidate.get("matching_policy_id") else None)
        if retrieval_outcome == RetrievalOutcome.NO_MATCH:
            if not matching_policy_id:
                errors.append("matching_policy_id is required for no_match")
            if not query_identifiers:
                errors.append("query_identifiers are required for no_match")

        parsed_dates: dict[str, datetime] = {}
        for date_field in ("published_at", "effective_from", "effective_to"):
            if candidate.get(date_field):
                _utc_datetime(candidate.get(date_field), date_field, errors)
                try:
                    parsed_dates[date_field] = datetime.fromisoformat(
                        str(candidate.get(date_field)).replace("Z", "+00:00"))
                except ValueError:
                    pass
        if (
            "effective_from" in parsed_dates
            and "effective_to" in parsed_dates
            and parsed_dates["effective_from"] > parsed_dates["effective_to"]
        ):
            errors.append("effective_from must not be after effective_to")

        if errors:
            wire_failure(
                module="dd_evidence_standard",
                detail="Evidence contract rejected: " + "; ".join(errors),
                gap_type="evidence_contract_violation",
                source="dd_evidence_standard:validate",
            )
            raise EvidenceContractError(errors)

        record = cls(
            evidence_id=evidence_id,
            tenant_id=tenant_id,
            case_id=case_id,
            case_scope_version=scope_version,
            subject_entity_id=subject_entity_id,
            source_attempt_id=source_attempt_id,
            source_id=source_id,
            source_authority=source_authority,
            retrieval_outcome=retrieval_outcome,
            retrieved_at=retrieved_at,
            request_fingerprint=request_fingerprint,
            licence_policy_id=licence_policy_id,
            access_method=access_method,
            adapter_version=adapter_version,
            parser_version=parser_version,
            schema_version=schema_version,
            source_record_id=candidate.get("source_record_id"),
            jurisdiction=candidate.get("jurisdiction"),
            published_at=candidate.get("published_at"),
            effective_from=candidate.get("effective_from"),
            effective_to=candidate.get("effective_to"),
            content_hash=content_hash,
            raw_artifact_uri=raw_artifact_uri,
            source_url=candidate.get("source_url"),
            snapshot_policy=snapshot_policy,
            matching_policy_id=matching_policy_id,
            query_identifiers=query_identifiers,
            structured_payload=_freeze_json(raw_payload),
            warnings=warnings,
        )
        wire_success(
            module="dd_evidence_standard",
            summary=f"Evidence contract accepted for {source_id}",
            source_id=f"evidence:{evidence_id}",
        )
        return record


def describe_standard() -> dict[str, Any]:
    """Return the machine-readable evidence contract catalogue."""
    return {
        "schema_id": EVIDENCE_SCHEMA_ID,
        "schema_version": EVIDENCE_SCHEMA_VERSION,
        "immutable": True,
        "required_fields": [
            item.name
            for item in fields(EvidenceRecord)
            if item.default is MISSING and item.default_factory is MISSING
        ],
        "source_authorities": [item.value for item in SourceAuthority],
        "retrieval_outcomes": [item.value for item in RetrievalOutcome],
        "snapshot_policies": [item.value for item in SnapshotPolicy],
        "assessment_contract": {
            "verdict_fn_version": VERDICT_FUNCTION_VERSION,
            "configuration_states": [item.value for item in ConfigurationState],
            "source_states": [item.value for item in SourceState],
            "attempt_outcomes": [item.value for item in AttemptOutcome],
            "match_outcomes": [item.value for item in MatchOutcome],
            "derived_verdicts": [item.value for item in EvidenceVerdict],
            "valid_combinations": len(evidence_assessment_matrix()),
        },
        "invariants": [
            "timeout is not zero_results or no_match",
            "answered source outcomes require a SHA-256 content hash",
            "no_match requires the searched identifiers and matching policy",
            "failed source outcomes cannot carry structured evidence payloads",
            "snapshot policy controls whether a raw artefact URI may be retained",
            "all case and evidence identifiers are UUIDs",
            "retrieval and effective timestamps include an explicit UTC offset",
            "status axes remain orthogonal and impossible combinations are rejected",
            "stale or degraded evidence never derives a completed verdict",
            "historical reports pin the verdict function version used at issue time",
        ],
    }
