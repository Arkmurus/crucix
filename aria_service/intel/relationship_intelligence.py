"""Evidence-led relationship intelligence for commercial access requests.

Public form values are assertions, not verified identity or buying intent.  This
module turns an intake event into a deterministic, explainable triage record
without sending personal data to an LLM or promoting it into durable knowledge.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Any

from .engine_wiring import wire_failure, wire_success


ASSESSMENT_SCHEMA_VERSION = "1.1.0"


class TrustState(str, Enum):
    """Evidence state of the submitted relationship identity."""

    SUBMITTED_UNVERIFIED = "submitted_unverified"
    EMAIL_VERIFIED = "email_verified"
    OPERATOR_VERIFIED = "operator_verified"


class IntakeReadiness(str, Enum):
    """Truthful workflow readiness; never a probability of conversion."""

    NEEDS_VERIFICATION = "needs_verification"
    INCOMPLETE = "incomplete"
    READY_FOR_REVIEW = "ready_for_review"


_FREE_EMAIL_DOMAINS = frozenset({
    "gmail.com", "googlemail.com", "outlook.com", "hotmail.com", "live.com",
    "yahoo.com", "icloud.com", "proton.me", "protonmail.com", "aol.com",
})
_SPECIFIC_USE_CASES = frozenset({
    "defence brokerage",
    "compliance advisory",
    "financial advisory",
    "government / institutional",
})


@dataclass(frozen=True)
class EvidenceFactor:
    """One human-readable observation, assertion or derived signal."""

    code: str
    label: str
    basis: str
    detail: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "label": self.label,
            "basis": self.basis,
            "detail": self.detail,
        }


def _email_domain(email: str) -> str:
    parts = str(email or "").strip().lower().rsplit("@", 1)
    return parts[1] if len(parts) == 2 else ""


def assess_access_request(
    *,
    name: str,
    email: str,
    use_case: str,
    company: str = "",
    country: str = "",
    role: str = "",
    trust_state: TrustState = TrustState.SUBMITTED_UNVERIFIED,
    assessed_at: str | None = None,
) -> dict[str, Any]:
    """Return explainable workflow readiness without inferring buyer quality."""
    factors: list[EvidenceFactor] = []
    gaps: list[str] = []
    domain = _email_domain(email)

    factors.append(EvidenceFactor(
        code="ACCESS_REQUEST_SUBMITTED",
        label="Explicit access request",
        basis="observed_event",
        detail="The visitor submitted the access-request form.",
    ))

    if domain and domain not in _FREE_EMAIL_DOMAINS:
        factors.append(EvidenceFactor(
            code="WORK_EMAIL_DOMAIN",
            label="Non-consumer email domain",
            basis="derived_from_submission",
            detail=(
                "The submitted domain is not in ARIA's consumer-email list; "
                "this does not prove ownership, employment or organisational fit."
            ),
        ))
    else:
        factors.append(EvidenceFactor(
            code="CONSUMER_OR_UNKNOWN_EMAIL_DOMAIN",
            label="Consumer or unclassified email domain",
            basis="derived_from_submission",
            detail=(
                "The submitted domain is consumer-hosted or unclassified. "
                "ARIA makes no inference about professional standing from this."
            ),
        ))

    normalized_use_case = str(use_case or "").strip().lower()
    if normalized_use_case in _SPECIFIC_USE_CASES:
        factors.append(EvidenceFactor(
            code="SPECIFIC_USE_CASE",
            label="Specific supported use case",
            basis="submitted_assertion",
            detail="The visitor selected a use case ARIA is designed to support; the assertion is not independently verified.",
        ))
    else:
        gaps.append("specific_use_case")

    if str(company or "").strip():
        factors.append(EvidenceFactor(
            code="ORGANISATION_SUBMITTED",
            label="Organisation supplied",
            basis="submitted_assertion",
            detail="An organisation name was supplied but has not been resolved or verified.",
        ))
    else:
        gaps.append("organisation")
    if not str(country or "").strip():
        gaps.append("jurisdiction")
    if not str(role or "").strip():
        gaps.append("role_or_decision_capacity")

    required_fact_count = 4
    supplied_fact_count = required_fact_count - len(gaps)
    if trust_state == TrustState.SUBMITTED_UNVERIFIED:
        readiness = IntakeReadiness.NEEDS_VERIFICATION
        if gaps:
            next_action = (
                "Verify email ownership, then complete: "
                + ", ".join(gap.replace("_", " ") for gap in gaps)
                + "."
            )
        else:
            next_action = "Verify email ownership, then assign a human owner to review the submitted facts."
    elif gaps:
        readiness = IntakeReadiness.INCOMPLETE
        next_action = "Complete the named evidence gaps before qualification."
    else:
        readiness = IntakeReadiness.READY_FOR_REVIEW
        next_action = "Assign a human owner to review the verified facts and business fit."

    return {
        "schema_version": ASSESSMENT_SCHEMA_VERSION,
        "assessed_at": assessed_at or datetime.now(timezone.utc).isoformat(),
        "trust_state": trust_state.value,
        "readiness": readiness.value,
        "evidence_completeness": {
            "supplied": supplied_fact_count,
            "required": required_fact_count,
            "is_complete": supplied_fact_count == required_fact_count,
        },
        "factors": [factor.as_dict() for factor in factors],
        "gaps": gaps,
        "next_best_action": next_action,
        "invariants": [
            "submission is not identity verification",
            "no conversion probability is inferred",
            "unverified identity cannot become ready for review",
        ],
    }


def assess_new_access_request(**values: Any) -> dict[str, Any]:
    """Assess a newly observed request, wiring deterministic failures."""
    try:
        return assess_access_request(**values)
    except Exception as exc:
        wire_failure(
            module="inbound_leads",
            detail=f"relationship assessment failed: {type(exc).__name__}",
            gap_type="engine_failure",
            source="relationship_intelligence",
        )
        raise


def record_persisted_access_request(assessment: dict[str, Any]) -> None:
    """Wire success only after the route has durably stored the assessment."""
    wire_success(
        module="inbound_leads",
        summary=(
            "unverified access request persisted; "
            f"readiness={assessment.get('readiness', 'unknown')}; "
            f"gaps={len(assessment.get('gaps') or [])}"
        ),
        source_id="relationship_intelligence:record_persisted_access_request",
    )


def record_erased_access_request(*, index_removed: bool) -> None:
    """Emit a non-PII metric after strict read-back proves erasure."""
    wire_success(
        module="inbound_leads",
        summary=f"access request erased; index_removed={index_removed}",
        source_id="relationship_intelligence:record_erased_access_request",
    )


def record_failed_erasure(*, record_deleted: bool, still_present: bool) -> None:
    """Wire the FAILURE branch of erasure — added in review of R-F3481.

    §21a defines a path as wired only when it emits on BOTH branches. The
    success branch had record_erased_access_request(); the failure branch
    returned a 503 and reached no sink. The endpoint's ``@fail_wire`` decorator
    does not cover it either — that fires on an unhandled EXCEPTION, and a
    returned JSONResponse is not one.

    An erasure that cannot be PROVEN is a data-protection incident: the operator
    told a data subject their record was removed and it may still be there.
    Silence is the one outcome that must never happen here, so this is wired as
    a data_protection_violation rather than a generic engine failure. Carries no
    PII — only booleans.
    """
    wire_failure(
        module="inbound_leads",
        detail=(
            "access-request erasure could not be proven; "
            f"record_deleted={record_deleted} still_present={still_present}"
        ),
        gap_type="data_protection_violation",
        source="relationship_intelligence:record_failed_erasure",
    )
