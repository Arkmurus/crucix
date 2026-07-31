"""Evidence-led relationship intelligence for commercial access requests.

Public form values are assertions, not verified identity or buying intent.  This
module turns an intake event into a deterministic, explainable triage record
without sending personal data to an LLM or promoting it into durable knowledge.

R-F3531 — coherence surgery.  The R-F3481 assessment was honest but *inert*:

  1. It graded four required facts, three of which (organisation, jurisdiction,
     role) the landing form never asked for and the aria-web proxy would have
     dropped anyway.  Every real lead was permanently stuck at 1/4.
  2. ``trust_state`` was hardcoded ``SUBMITTED_UNVERIFIED`` at every call site and
     nothing in the tree ever assigned ``EMAIL_VERIFIED``/``OPERATOR_VERIFIED``, so
     ``readiness`` could only ever be ``needs_verification`` and two of the three
     ``IntakeReadiness`` branches were unreachable code.
  3. ``next_best_action`` told the operator to "verify email ownership, then
     assign a human owner" — two things the surface could not do.

The fix is structural, not cosmetic: every fact this module GRADES is now a fact
the intake pipeline CARRIES (``INTAKE_FIELDS``), and every action it RECOMMENDS is
an action the operator surface OFFERS (``OperatorAction``).  Both properties are
guarded by tests that read the producer and the surface, so the mismatch cannot
silently return.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any

from .engine_wiring import wire_failure, wire_success


ASSESSMENT_SCHEMA_VERSION = "1.2.0"

#: Every field the intake pipeline must carry end to end (form → aria-web →
#: brain → assessment).  A fact this module grades but the pipeline cannot
#: deliver is an ungradeable gap forever — the R-F3481 defect.  Guarded by
#: ``test_rf3531_lead_intake_coherence``.
INTAKE_FIELDS = ("name", "email", "use_case", "company", "country", "role")

#: The evidence the assessment requires.  Each entry is emitted as a gap code
#: when the corresponding fact is absent, so ``required`` and the gap vocabulary
#: cannot drift apart.
REQUIRED_EVIDENCE = (
    "specific_use_case",
    "organisation",
    "jurisdiction",
    "role_or_decision_capacity",
)

#: Operator workflow stages.  Stored on the record and advanced only through the
#: operator endpoint — never inferred from an unverified submission.
LIFECYCLE_STAGES = ("NEW", "CONTACTED", "QUALIFYING", "ACCEPTED", "DECLINED")

#: How long an email-ownership challenge stays valid.
VERIFICATION_TTL_SECONDS = 7 * 24 * 3600

#: How soon a re-submission may mint a NEW challenge. Inside this window the
#: live link is reused, so repeatedly submitting the form cannot be used to mail
#: someone repeatedly; outside it, a contact who never received the first email
#: can recover by submitting again instead of waiting seven days for the
#: operator to notice. Without this the choice is spammable or unrecoverable.
VERIFICATION_REISSUE_AFTER_SECONDS = 15 * 60


class TrustState(str, Enum):
    """Evidence state of the submitted relationship identity."""

    SUBMITTED_UNVERIFIED = "submitted_unverified"
    EMAIL_VERIFIED = "email_verified"
    OPERATOR_VERIFIED = "operator_verified"


#: Trust states that were established by a control, not merely asserted.
VERIFIED_TRUST_STATES = frozenset({
    TrustState.EMAIL_VERIFIED,
    TrustState.OPERATOR_VERIFIED,
})


class IntakeReadiness(str, Enum):
    """Truthful workflow readiness; never a probability of conversion."""

    NEEDS_VERIFICATION = "needs_verification"
    INCOMPLETE = "incomplete"
    READY_FOR_REVIEW = "ready_for_review"


class OperatorAction(str, Enum):
    """The closed set of next actions the operator surface actually implements.

    ``next_action_code`` is drawn from this enum and nothing else.  A test reads
    ``public/leads.html`` and asserts every member is offered there, so the
    assessment can never again recommend a control that does not exist.
    """

    AWAIT_EMAIL_VERIFICATION = "await_email_verification"
    RESEND_VERIFICATION = "resend_verification"
    MARK_OPERATOR_VERIFIED = "mark_operator_verified"
    REQUEST_MISSING_EVIDENCE = "request_missing_evidence"
    ASSIGN_OWNER = "assign_owner"
    REVIEW_AND_DECIDE = "review_and_decide"


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


def coerce_trust_state(value: Any) -> TrustState:
    """Read a stored trust state without ever UPGRADING an unknown value.

    Legacy records pre-date the field.  An unreadable or unrecognised value must
    fall back to the *weakest* state: silently treating garbage as verified would
    manufacture the exact false confidence this module exists to prevent.
    """
    if isinstance(value, TrustState):
        return value
    try:
        return TrustState(str(value or "").strip().lower())
    except ValueError:
        return TrustState.SUBMITTED_UNVERIFIED


def assess_access_request(
    *,
    name: str,
    email: str,
    use_case: str,
    company: str = "",
    country: str = "",
    role: str = "",
    trust_state: TrustState | str = TrustState.SUBMITTED_UNVERIFIED,
    owner: str = "",
    verification_pending: bool = False,
    assessed_at: str | None = None,
) -> dict[str, Any]:
    """Return explainable workflow readiness without inferring buyer quality."""
    trust_state = coerce_trust_state(trust_state)
    factors: list[EvidenceFactor] = []
    gaps: list[str] = []
    domain = _email_domain(email)

    factors.append(EvidenceFactor(
        code="ACCESS_REQUEST_SUBMITTED",
        label="Explicit access request",
        basis="observed_event",
        detail="The visitor submitted the access-request form.",
    ))

    if trust_state == TrustState.EMAIL_VERIFIED:
        factors.append(EvidenceFactor(
            code="EMAIL_OWNERSHIP_VERIFIED",
            label="Email ownership confirmed",
            basis="verified_control",
            detail=(
                "The submitted address confirmed a single-use link issued by ARIA. "
                "This proves control of the mailbox — not employment, authority "
                "or organisational fit."
            ),
        ))
    elif trust_state == TrustState.OPERATOR_VERIFIED:
        factors.append(EvidenceFactor(
            code="OPERATOR_ATTESTED_IDENTITY",
            label="Operator-attested identity",
            basis="operator_attestation",
            detail=(
                "A named operator recorded an out-of-band check of this contact. "
                "The attestation is auditable but is not an automated control."
            ),
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

    if str(country or "").strip():
        factors.append(EvidenceFactor(
            code="JURISDICTION_SUBMITTED",
            label="Operating jurisdiction supplied",
            basis="submitted_assertion",
            detail="A jurisdiction was supplied but has not been confirmed against any register.",
        ))
    else:
        gaps.append("jurisdiction")

    if str(role or "").strip():
        factors.append(EvidenceFactor(
            code="ROLE_SUBMITTED",
            label="Role or decision capacity supplied",
            basis="submitted_assertion",
            detail="A role was supplied; ARIA has not confirmed the person holds it.",
        ))
    else:
        gaps.append("role_or_decision_capacity")

    required_fact_count = len(REQUIRED_EVIDENCE)
    supplied_fact_count = required_fact_count - len(gaps)
    has_owner = bool(str(owner or "").strip())

    # Readiness is a function of PROVEN trust plus supplied evidence. An
    # unverified request can never reach review, however complete the form was —
    # that is the invariant the whole module exists to hold.
    if trust_state not in VERIFIED_TRUST_STATES:
        readiness = IntakeReadiness.NEEDS_VERIFICATION
        if verification_pending:
            action = OperatorAction.AWAIT_EMAIL_VERIFICATION
            next_action = (
                "A confirmation link has been issued and is unexpired. Await the "
                "contact's confirmation, or record an out-of-band check with "
                "“Mark verified”."
            )
        else:
            action = OperatorAction.RESEND_VERIFICATION
            next_action = (
                "No live confirmation link. Use “Resend link” to issue a new "
                "one, or record an out-of-band check with “Mark verified”."
            )
    elif gaps:
        readiness = IntakeReadiness.INCOMPLETE
        action = OperatorAction.REQUEST_MISSING_EVIDENCE
        next_action = (
            "Identity is confirmed. Ask the contact for: "
            + ", ".join(gap.replace("_", " ") for gap in gaps)
            + ", then record it with “Add note”."
        )
    elif not has_owner:
        readiness = IntakeReadiness.READY_FOR_REVIEW
        action = OperatorAction.ASSIGN_OWNER
        next_action = "Evidence is complete and identity confirmed. Use “Assign to me” to take ownership."
    else:
        readiness = IntakeReadiness.READY_FOR_REVIEW
        action = OperatorAction.REVIEW_AND_DECIDE
        next_action = (
            "Owned and complete. Review the submitted facts and advance the stage "
            "to Accepted or Declined."
        )

    return {
        "schema_version": ASSESSMENT_SCHEMA_VERSION,
        "assessed_at": assessed_at or datetime.now(timezone.utc).isoformat(),
        "trust_state": trust_state.value,
        "trust_is_established": trust_state in VERIFIED_TRUST_STATES,
        "readiness": readiness.value,
        "evidence_completeness": {
            "supplied": supplied_fact_count,
            "required": required_fact_count,
            "is_complete": supplied_fact_count == required_fact_count,
        },
        "factors": [factor.as_dict() for factor in factors],
        "gaps": gaps,
        "next_action_code": action.value,
        "next_best_action": next_action,
        "invariants": [
            "submission is not identity verification",
            "no conversion probability is inferred",
            "unverified identity cannot become ready for review",
            "trust state advances only through a completed control or a named operator attestation",
            "every recommended action is implemented on the operator surface",
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


def assess_record(record: dict[str, Any], *, assessed_at: str | None = None) -> dict[str, Any]:
    """Re-assess a stored lead from its OWN state.

    Single derivation point for every read and every mutation.  R-F3481 rebuilt
    legacy assessments with ``trust_state`` hardcoded to ``SUBMITTED_UNVERIFIED``,
    which silently discarded a verification the record already held; deriving from
    the record makes that class of drift impossible.
    """
    record = record if isinstance(record, dict) else {}
    return assess_access_request(
        name=str(record.get("name") or ""),
        email=str(record.get("email") or ""),
        use_case=str(record.get("use_case") or ""),
        company=str(record.get("company") or ""),
        country=str(record.get("country") or ""),
        role=str(record.get("role") or ""),
        trust_state=record.get("trust_state") or TrustState.SUBMITTED_UNVERIFIED,
        owner=str(record.get("owner") or ""),
        verification_pending=verification_is_pending(record.get("verification")),
        assessed_at=assessed_at,
    )


# ── Email-ownership challenge ────────────────────────────────────────────────
# The challenge lives INSIDE the lead record rather than in its own TTL'd key.
# That is deliberate: a separate key would survive the GDPR erasure endpoint,
# leaving a live credential pointing at a subject who asked to be forgotten.
# Only the digest is stored, so a state-store read never yields a usable link.


def issue_verification_challenge(*, now: datetime | None = None) -> tuple[str, dict[str, Any]]:
    """Return ``(plaintext_token, stored_challenge)``.

    The plaintext is returned to the caller ONCE so the web tier can mail it, and
    is never persisted. Callers must not log it or return it to a public client.
    """
    moment = now or datetime.now(timezone.utc)
    token = secrets.token_urlsafe(32)
    return token, {
        "token_sha256": hashlib.sha256(token.encode("utf-8")).hexdigest(),
        "issued_at": moment.isoformat(),
        "expires_at": (moment + timedelta(seconds=VERIFICATION_TTL_SECONDS)).isoformat(),
    }


def _parse_iso(value: Any) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def verification_is_pending(challenge: Any, *, now: datetime | None = None) -> bool:
    """True only for a challenge that exists and has not expired."""
    if not isinstance(challenge, dict) or not challenge.get("token_sha256"):
        return False
    expires_at = _parse_iso(challenge.get("expires_at"))
    if expires_at is None:
        return False
    return (now or datetime.now(timezone.utc)) < expires_at


def challenge_is_reusable(challenge: Any, *, now: datetime | None = None) -> bool:
    """True while a live challenge is recent enough to reuse rather than replace.

    Guards the resubmission path in both directions: a burst of form submissions
    must not mail the contact once per submission, and a contact whose first
    email went astray must not be stuck until an operator intervenes.
    """
    if not verification_is_pending(challenge, now=now):
        return False
    issued_at = _parse_iso((challenge or {}).get("issued_at"))
    if issued_at is None:
        return False
    age = (now or datetime.now(timezone.utc)) - issued_at
    return age.total_seconds() < VERIFICATION_REISSUE_AFTER_SECONDS


def check_verification_token(
    challenge: Any, token: str, *, now: datetime | None = None
) -> tuple[bool, str]:
    """Constant-time check of a presented token. Returns ``(ok, reason)``.

    ``reason`` is one of ``""``, ``no_challenge``, ``expired``, ``mismatch`` — for
    telemetry and for an honest (non-enumerable) message to the caller.
    """
    if not isinstance(challenge, dict) or not challenge.get("token_sha256"):
        return False, "no_challenge"
    presented = str(token or "")
    if not presented:
        return False, "mismatch"
    expires_at = _parse_iso(challenge.get("expires_at"))
    if expires_at is None or (now or datetime.now(timezone.utc)) >= expires_at:
        return False, "expired"
    digest = hashlib.sha256(presented.encode("utf-8")).hexdigest()
    if not hmac.compare_digest(digest, str(challenge.get("token_sha256"))):
        return False, "mismatch"
    return True, ""


# ── Non-PII telemetry (§21a: success AND failure reach the brain) ────────────


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


def record_email_verified(assessment: dict[str, Any]) -> None:
    """A contact completed the ownership challenge — the trust state advanced."""
    wire_success(
        module="inbound_leads",
        summary=(
            "access request email-verified; "
            f"readiness={assessment.get('readiness', 'unknown')}; "
            f"gaps={len(assessment.get('gaps') or [])}"
        ),
        source_id="relationship_intelligence:record_email_verified",
    )


def record_verification_rejected(reason: str) -> None:
    """A presented token failed. Wired as a failure, never silent.

    A burst of rejections is either a broken mail path or someone guessing at
    links; both are things the operator must be able to see. The reason is a
    fixed vocabulary — it carries no token material and no PII.
    """
    wire_failure(
        module="inbound_leads",
        detail=f"access-request verification rejected; reason={reason}",
        gap_type="engine_failure",
        source="relationship_intelligence:record_verification_rejected",
    )


def record_operator_action(action: str, *, readiness: str = "") -> None:
    """An operator advanced a request (owner, stage, note or attestation)."""
    wire_success(
        module="inbound_leads",
        summary=f"operator advanced access request; action={action}; readiness={readiness or 'unknown'}",
        source_id="relationship_intelligence:record_operator_action",
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
