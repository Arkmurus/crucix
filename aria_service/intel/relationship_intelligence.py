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


ASSESSMENT_SCHEMA_VERSION = "1.0.0"


class TrustState(str, Enum):
    """Evidence state of the submitted relationship identity."""

    SUBMITTED_UNVERIFIED = "submitted_unverified"
    EMAIL_VERIFIED = "email_verified"
    OPERATOR_VERIFIED = "operator_verified"


class IntakePriority(str, Enum):
    """Truthful triage band; never a probability of conversion."""

    NEEDS_VERIFICATION = "needs_verification"
    REVIEW = "review"
    PRIORITY = "priority"


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
class ScoreFactor:
    """One human-readable input to a deterministic triage score."""

    code: str
    label: str
    points: int
    basis: str
    detail: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "label": self.label,
            "points": self.points,
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
    """Return an explainable triage assessment without inferring buyer truth.

    Scores measure observable intake quality, not conversion probability.  A
    record cannot become ``priority`` while identity remains unverified.
    """
    factors: list[ScoreFactor] = []
    gaps: list[str] = []
    domain = _email_domain(email)
    fit_score = 0
    engagement_score = 20  # one explicit access-request submission
    data_quality_score = 0

    factors.append(ScoreFactor(
        code="ACCESS_REQUEST_SUBMITTED",
        label="Explicit access request",
        points=20,
        basis="observed_event",
        detail="The visitor submitted the access-request form.",
    ))

    if domain and domain not in _FREE_EMAIL_DOMAINS:
        fit_score += 15
        factors.append(ScoreFactor(
            code="WORK_EMAIL_DOMAIN",
            label="Non-consumer email domain",
            points=15,
            basis="derived_from_submission",
            detail="The submitted domain is not in ARIA's consumer-email list; ownership remains unverified.",
        ))
    else:
        gaps.append("work_email_or_verified_identity")

    normalized_use_case = str(use_case or "").strip().lower()
    if normalized_use_case in _SPECIFIC_USE_CASES:
        fit_score += 15
        data_quality_score += 10
        factors.append(ScoreFactor(
            code="SPECIFIC_USE_CASE",
            label="Specific supported use case",
            points=25,
            basis="submitted_assertion",
            detail="The visitor selected a use case ARIA is designed to support; the assertion is not independently verified.",
        ))
    else:
        gaps.append("specific_use_case")

    if str(name or "").strip():
        data_quality_score += 5
    if str(company or "").strip():
        fit_score += 10
        data_quality_score += 5
    else:
        gaps.append("organisation")
    if not str(country or "").strip():
        gaps.append("jurisdiction")
    if not str(role or "").strip():
        gaps.append("role_or_decision_capacity")

    total_score = min(100, fit_score + engagement_score + data_quality_score)
    if trust_state == TrustState.SUBMITTED_UNVERIFIED:
        priority = IntakePriority.NEEDS_VERIFICATION
        total_score = min(total_score, 49)
        next_action = (
            "Verify email ownership, then collect organisation, jurisdiction "
            "and role before qualification."
        )
    elif total_score >= 65:
        priority = IntakePriority.PRIORITY
        next_action = "Assign an owner and review the evidence-backed fit factors."
    else:
        priority = IntakePriority.REVIEW
        next_action = "Complete the missing relationship facts before prioritisation."

    return {
        "schema_version": ASSESSMENT_SCHEMA_VERSION,
        "assessed_at": assessed_at or datetime.now(timezone.utc).isoformat(),
        "trust_state": trust_state.value,
        "priority": priority.value,
        "scores": {
            "fit": fit_score,
            "engagement": engagement_score,
            "data_quality": data_quality_score,
            "total": total_score,
        },
        "factors": [factor.as_dict() for factor in factors],
        "gaps": gaps,
        "next_best_action": next_action,
        "invariants": [
            "submission is not identity verification",
            "score is not conversion probability",
            "unverified identity cannot receive priority status",
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
            f"priority={assessment.get('priority', 'unknown')}; "
            f"gaps={len(assessment.get('gaps') or [])}"
        ),
        source_id="relationship_intelligence:record_persisted_access_request",
    )
