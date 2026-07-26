"""R-F3153 — the employment decision record (UK GDPR Art. 22, Art. 5(2)).

── Why this exists ───────────────────────────────────────────────────────
The module has claimed from the first commit that "a named human makes the
employment decision" and that its terminal good state is
READY_FOR_CONTROLLER_REVIEW, not a verdict on a person. That claim was
ARCHITECTURAL, not enforced: nothing recorded who decided, when, on what
evidence, or whether a human was involved at all. An unenforced claim is the
same shape as the Phase A gates that "could not fail" — true by construction
until the day it isn't.

Art. 22 gives a data subject the right not to be subject to a decision based
SOLELY on automated processing which produces legal effects or similarly
significantly affects them. A hiring rejection is the textbook case. The
defence is not that our engine is deterministic — a deterministic rule engine
is still automated processing. The defence is documented human involvement
with real authority to depart from the recommendation.

Art. 5(2) then requires the controller to be able to DEMONSTRATE that. A
decision nobody recorded cannot be demonstrated, so it does not count.

── What this module enforces ─────────────────────────────────────────────
1. A decision must name a human. `decided_by` is required and cannot be the
   system.
2. The decision must be one a human made, not a transcription of the engine.
   `engine_status` is recorded ALONGSIDE `decision`, so a reviewer (or a
   regulator) can see every case where the human departed from the
   recommendation — and, more tellingly, whether anyone ever does.
3. A decision that goes against the applicant requires a stated reason.
   Art. 22(3) safeguards are meaningless if the subject cannot be told why.
4. Four-eyes on adverse outcomes: the person who ran the screening may not be
   the sole person who rejects on it. `assessed_by != decided_by` is checked
   when the outcome is adverse.
5. A blocked file cannot be approved silently — approving over an open BLOCKER
   requires an explicit override with a reason, which is itself recorded.

The module records; it never decides. There is deliberately no function here
that derives a decision from an assessment.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from enum import Enum


class DecisionOutcome(str, Enum):
    """What the human decided. Note there is no 'SYSTEM_*' member."""

    APPROVED = "APPROVED"
    APPROVED_WITH_CONDITIONS = "APPROVED_WITH_CONDITIONS"
    REJECTED = "REJECTED"
    DEFERRED = "DEFERRED"          # more evidence sought
    WITHDRAWN = "WITHDRAWN"        # applicant withdrew


# Outcomes that are adverse to the applicant and therefore attract the
# Art. 22(3) safeguards (reason, human involvement, ability to contest).
ADVERSE_OUTCOMES = frozenset({DecisionOutcome.REJECTED})


class DecisionError(ValueError):
    """A decision that may not be recorded as offered."""


# Values that would let the system masquerade as the decision-maker.
_NON_HUMAN = {"", "system", "aria", "automated", "auto", "engine", "none",
              "n/a", "-"}


@dataclass(frozen=True)
class DecisionRecord:
    """One employment decision, attributable to a named human."""

    decision_id: str
    case_id: str
    tenant_id: str
    decision: DecisionOutcome
    decided_by: str
    decided_at: str
    # The engine's status at the moment of decision, recorded so departures
    # from the recommendation are visible rather than inferred.
    engine_status: str
    engine_blockers: int
    reason: str = ""
    assessed_by: str = ""
    blocker_override_reason: str = ""
    conditions: tuple[str, ...] = ()

    @property
    def departed_from_engine(self) -> bool:
        """True when the human did NOT simply transcribe the recommendation."""
        recommended_ok = self.engine_status in {
            "READY_FOR_CONTROLLER_REVIEW", "EVIDENCE_COMPLETE"}
        decided_ok = self.decision in {
            DecisionOutcome.APPROVED, DecisionOutcome.APPROVED_WITH_CONDITIONS}
        return recommended_ok != decided_ok

    def as_dict(self) -> dict:
        return {
            "decision_id": self.decision_id,
            "case_id": self.case_id,
            "decision": self.decision.value,
            "decided_by": self.decided_by,
            "decided_at": self.decided_at,
            "engine_status": self.engine_status,
            "engine_blockers": self.engine_blockers,
            "departed_from_engine": self.departed_from_engine,
            "reason": self.reason,
            "assessed_by": self.assessed_by,
            "blocker_override_reason": self.blocker_override_reason,
            "conditions": list(self.conditions),
            # Stated explicitly so an export cannot be read as an automated
            # decision. Art. 22 turns on this fact.
            "automated_decision": False,
        }


def _is_human(name: str) -> bool:
    return (name or "").strip().lower() not in _NON_HUMAN


def record_decision(
    *,
    case_id: str,
    tenant_id: str,
    decision: DecisionOutcome,
    decided_by: str,
    engine_status: str,
    engine_blockers: int,
    reason: str = "",
    assessed_by: str = "",
    blocker_override_reason: str = "",
    conditions: tuple[str, ...] = (),
    now: datetime | None = None,
) -> DecisionRecord:
    """Validate and build a decision record. Raises DecisionError on refusal.

    Every refusal below is a legal requirement, not a style preference.
    """
    if not _is_human(decided_by):
        raise DecisionError(
            "decided_by must name the human who made this decision; an "
            "employment decision may not be attributed to the system "
            "(UK GDPR Art. 22)")

    if decision in ADVERSE_OUTCOMES and not (reason or "").strip():
        raise DecisionError(
            "an adverse decision requires a stated reason — the Art. 22(3) "
            "safeguards are void if the applicant cannot be told why")

    # Four-eyes on adverse outcomes. The person who assembled the evidence is
    # the person most invested in it being conclusive.
    if (
        decision in ADVERSE_OUTCOMES
        and assessed_by
        and assessed_by.strip().lower() == decided_by.strip().lower()
    ):
        raise DecisionError(
            "the person who ran the screening may not be the sole decision-"
            "maker on an adverse outcome; a second reviewer is required")

    approving = decision in {DecisionOutcome.APPROVED,
                             DecisionOutcome.APPROVED_WITH_CONDITIONS}
    if approving and engine_blockers > 0 and not blocker_override_reason.strip():
        raise DecisionError(
            f"cannot approve over {engine_blockers} open blocker(s) without a "
            f"recorded override reason")

    if decision is DecisionOutcome.APPROVED_WITH_CONDITIONS and not conditions:
        raise DecisionError(
            "APPROVED_WITH_CONDITIONS requires at least one condition")

    stamp = (now or datetime.now(UTC)).isoformat()
    return DecisionRecord(
        decision_id=f"vdec_{uuid.uuid4().hex[:16]}",
        case_id=case_id,
        tenant_id=tenant_id,
        decision=decision,
        decided_by=decided_by.strip(),
        decided_at=stamp,
        engine_status=engine_status,
        engine_blockers=int(engine_blockers),
        reason=reason.strip(),
        assessed_by=(assessed_by or "").strip(),
        blocker_override_reason=blocker_override_reason.strip(),
        conditions=tuple(conditions),
    )
