"""R-F3148 — retention schedule and audited disposal.

The packs have always carried the periods (`retention_unsuccessful_months`,
`retention_post_employment_years`); nothing acted on them, so "retention is a
first-class design point" was a claim the code did not honour. This module
makes the schedule computable and disposal explicit.

── The clock cannot start from a date we do not have ─────────────────────
A retention period is anchored to an OUTCOME, not to when the file was
created:
  * screening still in progress  → nothing is due; the file is live
  * applicant unsuccessful       → outcome date + retention_unsuccessful_months
  * applicant employed           → employment END + retention_post_employment_years

The last one is the trap. A file for someone still employed has NO due date,
and inventing one from `employment_start` would delete a live personnel record
years early. So `due_date` is None with a stated reason, and this module says
UNKNOWN rather than guessing — the same discipline the assessment engine uses.

── What disposal can honestly claim ──────────────────────────────────────
`intel/dd_evidence_store.py` is APPEND-ONLY by construction — it has no
delete, and that is deliberate: it is the tamper-evident evidence spine the DD
side relies on. So disposing of a vetting case removes the case record and its
personal content from the vetting store, and CANNOT by itself remove the
retained artifacts.

`plan_disposal` therefore reports both halves separately and never claims more
than it did. Reporting "disposed" while artifacts remain is the failure mode
that matters here: a data-protection response that overstates erasure is worse
than one that admits a residue, because only the second gets fixed.

Closing that residue needs an operator/counsel decision (add a tenant-scoped
purge to the shared evidence store, or encrypt vetting artifacts per-case and
shred the key). It is recorded here rather than silently skipped.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from enum import Enum

from .models import VettingCase
from .packs.base import ScreeningPack
from .rules import shift_years


class CaseOutcome(str, Enum):
    """Why a screening file is being kept."""

    PENDING = "PENDING"              # screening in progress
    UNSUCCESSFUL = "UNSUCCESSFUL"    # applicant not engaged
    EMPLOYED = "EMPLOYED"            # applicant engaged
    WITHDRAWN = "WITHDRAWN"          # applicant withdrew


# Outcomes that start the short (unsuccessful-file) clock.
_SHORT_CLOCK = {CaseOutcome.UNSUCCESSFUL, CaseOutcome.WITHDRAWN}


@dataclass(frozen=True)
class RetentionVerdict:
    due_date: date | None
    reason: str
    overdue: bool


def _add_months(anchor: date, months: int) -> date:
    """Month arithmetic that never rolls into the next month.

    31 Jan + 1 month is 28/29 Feb, not 2/3 Mar. A disposal date that silently
    slid a few days later is a retention breach, small but real.
    """
    total = anchor.month - 1 + months
    year = anchor.year + total // 12
    month = total % 12 + 1
    day = anchor.day
    while day > 1:
        try:
            return date(year, month, day)
        except ValueError:
            day -= 1
    return date(year, month, 1)


def retention_due_date(
    case: VettingCase,
    pack: ScreeningPack,
    as_of: date,
) -> RetentionVerdict:
    """When this file must be disposed of. `as_of` is explicit, as everywhere."""
    outcome = CaseOutcome(case.outcome)

    if outcome is CaseOutcome.PENDING:
        return RetentionVerdict(
            None, "screening in progress — no retention clock has started", False)

    if outcome in _SHORT_CLOCK:
        months = pack.retention_unsuccessful_months
        if months is None:
            return RetentionVerdict(
                None, f"pack {pack.pack_id} declares no unsuccessful-file "
                      f"retention period", False)
        if case.outcome_date is None:
            return RetentionVerdict(
                None, "outcome recorded without a date — the retention clock "
                      "cannot start until the outcome date is supplied", False)
        due = _add_months(case.outcome_date, months)
        return RetentionVerdict(due, f"unsuccessful file: {months} months from "
                                     f"{case.outcome_date.isoformat()}",
                                due <= as_of)

    # EMPLOYED — anchored to the END of employment, which we may not know yet.
    years = pack.retention_post_employment_years
    if years is None:
        return RetentionVerdict(
            None, f"pack {pack.pack_id} declares no post-employment retention "
                  f"period", False)
    if case.employment_end is None:
        return RetentionVerdict(
            None, "employment is ongoing — the post-employment retention clock "
                  "starts when employment ends", False)
    due = shift_years(case.employment_end, years)
    return RetentionVerdict(due, f"post-employment file: {years} years from "
                                 f"{case.employment_end.isoformat()}",
                            due <= as_of)


@dataclass(frozen=True)
class DisposalPlan:
    """What a disposal will and will NOT remove. Both halves, always."""

    case_id: str
    removable_case_record: bool
    retained_evidence_ids: tuple[str, ...]
    residual_reason: str

    @property
    def complete(self) -> bool:
        """True only when nothing personal survives the disposal."""
        return self.removable_case_record and not self.retained_evidence_ids

    def as_dict(self) -> dict:
        return {
            "case_id": self.case_id,
            "case_record_removed": self.removable_case_record,
            "evidence_retained": list(self.retained_evidence_ids),
            "evidence_retained_count": len(self.retained_evidence_ids),
            "residual_reason": self.residual_reason,
            "erasure_complete": self.complete,
        }


_APPEND_ONLY_NOTE = (
    "The evidence store (dd_evidence_store, R-F3083) is append-only by "
    "construction and exposes no delete, so retained document artifacts "
    "survive this disposal. Completing erasure requires an operator decision: "
    "a tenant-scoped purge on the shared evidence store, or per-case "
    "encryption with key destruction."
)


def plan_disposal(case: VettingCase) -> DisposalPlan:
    """Describe honestly what disposing of this case would achieve."""
    evidence_ids = tuple(
        d.evidence_id for d in case.documents if d.evidence_id
    )
    return DisposalPlan(
        case_id=case.case_id,
        removable_case_record=True,
        retained_evidence_ids=evidence_ids,
        residual_reason=_APPEND_ONLY_NOTE if evidence_ids else "",
    )
