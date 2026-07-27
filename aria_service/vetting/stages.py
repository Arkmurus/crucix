"""R-F3212 — the screening stages, derived from the file.

A findings list tells an officer what is wrong. It does not tell them where
they ARE. "Seven actions outstanding" is the same sentence whether the file is
waiting on an application form or waiting on the last of nine references, and
those are entirely different days of work.

── Derived, never stored ────────────────────────────────────────────────
Every stage state is computed from the case and the pack on read. A stored
stage is a second source of truth that goes stale behind the file — the exact
defect R-F3172 had to fix on the cached verdict, and the reason the request
ledger computes `overdue` rather than storing it. Nothing here can be advanced
by hand: a stage completes because the evidence for it is on the file.

── The states ───────────────────────────────────────────────────────────
  NOT_STARTED  nothing of this stage is on the file
  IN_PROGRESS  some of it is
  COMPLETE     all of it is, and nothing in it is waiting on a human
  BLOCKED      a BLOCKER finding sits in this stage

BLOCKED outranks COMPLETE. A stage whose evidence is all present but which
carries a blocking finding is not complete, and showing it green because the
documents arrived is the shape of false-clean this module refuses.

Purity: a function of (case, pack, resolved requirements, findings, as_of).
No clock, no I/O.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from .models import VettingCase
from .packs.base import ScreeningPack
from .requirements import RequirementState, ResolvedRequirement

__all__ = ["StageState", "Stage", "STAGE_SPECS", "build_stages", "current_stage"]


class StageState:
    NOT_STARTED = "NOT_STARTED"
    IN_PROGRESS = "IN_PROGRESS"
    COMPLETE = "COMPLETE"
    BLOCKED = "BLOCKED"


@dataclass(frozen=True)
class StageSpec:
    key: str
    label: str
    hint: str
    # Checklist fields on ScreeningInputs that belong to this stage. The
    # requirement side is matched by DocumentRequirement.stage, so a pack that
    # adds a requirement automatically lands it in the right stage without a
    # second list to keep in step.
    inputs: tuple[str, ...] = ()
    # Finding codes that make this stage the one to look at.
    finding_codes: tuple[str, ...] = ()


# Order is the order the work actually happens in, which is what makes this a
# progress tracker rather than a second checklist.
STAGE_SPECS: tuple[StageSpec, ...] = (
    StageSpec(
        key="APPLICATION", label="Application",
        hint="Declared history, signed authorisation, and the CV it is checked against",
        inputs=("full_name", "previous_names_declared", "date_of_birth",
                "ni_number", "address_history_5y", "convictions_declared",
                "financial_history_declared", "misrepresentation_ack_signed",
                "screening_consent_signed", "verification_authorisation_signed"),
        finding_codes=("CHECKLIST_MISSING",),
    ),
    StageSpec(
        key="IDENTITY", label="Identity & right to work",
        hint="Originals sighted, address confirmed, entitlement to work evidenced",
        inputs=("identity_verified", "address_confirmed",
                "right_to_work_evidenced", "identity_examined_by",
                "address_examined_by"),
        finding_codes=("ORIGINAL_NOT_SIGHTED", "SIGHTING_NOT_RECORDED",
                       "EXAMINER_NOT_RECORDED"),
    ),
    StageSpec(
        key="INTERVIEW", label="Interview",
        hint="Held before any offer, with a date and a named interviewer",
        inputs=("interview_done", "interview_date", "interviewed_by"),
    ),
    StageSpec(
        key="HISTORY", label="Career history",
        hint="Every period declared, and every period verified",
        finding_codes=("GAP_UNDECLARED", "GAP_UNVERIFIED_OVER_LIMIT",
                       "EVIDENCE_MISSING", "EVIDENCE_INSUFFICIENT",
                       "REFEREE_NOT_NOMINATED", "OVERLAPPING_DECLARATIONS",
                       "DUPLICATE_ENTRY", "FUTURE_DATED_HISTORY"),
    ),
    StageSpec(
        key="CRIMINALITY", label="Criminality & conduct",
        hint="A disclosure route chosen and the certificate itself on file",
        finding_codes=("CRIMINALITY_ROUTE_MISSING",),
    ),
    StageSpec(
        key="PUBLIC_RECORD", label="Public record & financial",
        hint="The credit-reference search and its seven elements",
        inputs=("public_record_search_done", "electoral_roll_confirmed",
                "linked_addresses_5y_searched", "ccj_iva_searched",
                "bankruptcy_orders_searched", "aliases_searched",
                "watchlist_check_done", "sia_licence_expiry",
                "sia_register_verified"),
        finding_codes=("SIGNOFF_CCJ", "SIGNOFF_BANKRUPTCY",
                       "SIGNOFF_DIRECTORSHIP", "FINANCIAL_CURRENCY_REVIEW"),
    ),
    StageSpec(
        key="DECISION", label="Decision",
        hint="A named human records the employment decision — the engine never does",
    ),
)


@dataclass(frozen=True)
class Stage:
    key: str
    label: str
    hint: str
    state: str
    done: int
    total: int
    blockers: int
    actions: int
    outstanding: tuple[str, ...]

    def as_dict(self) -> dict:
        return {
            "key": self.key, "label": self.label, "hint": self.hint,
            "state": self.state, "done": self.done, "total": self.total,
            "blockers": self.blockers, "actions": self.actions,
            # Capped: this is a progress strip, not the findings list. The
            # count is exact; the sample is a sample, and the UI says so.
            "outstanding": list(self.outstanding[:6]),
            "outstanding_total": len(self.outstanding),
        }


def build_stages(
    case: VettingCase,
    pack: ScreeningPack,
    resolved: list[ResolvedRequirement],
    findings: list,
    as_of: date,
) -> list[Stage]:
    """One row per stage, counted from what is actually on the file.

    `findings` is the engine's own list — passed in rather than recomputed so
    a stage can never disagree with the finding it is describing.
    """
    pack_fields = {spec.field for spec in pack.checklist}
    reqs_by_stage: dict[str, list[ResolvedRequirement]] = {}
    for item in resolved:
        reqs_by_stage.setdefault(item.requirement.stage, []).append(item)

    findings_by_code: dict[str, list] = {}
    for finding in findings:
        findings_by_code.setdefault(finding.code, []).append(finding)

    stages: list[Stage] = []
    for spec in STAGE_SPECS:
        done = total = 0
        outstanding: list[str] = []

        # Checklist items the PACK actually asks for. A field absent from the
        # pack is not a gap in the file — it is a question this framework does
        # not ask, and counting it would invent work the standard never set.
        for field in spec.inputs:
            if field not in pack_fields:
                continue
            total += 1
            if getattr(case.inputs, field, None):
                done += 1
            else:
                label = next((s.label for s in pack.checklist if s.field == field), field)
                outstanding.append(label)

        for item in reqs_by_stage.get(spec.key, []):
            total += 1
            if item.state in (RequirementState.ACCEPTED, RequirementState.WAIVED):
                done += 1
            else:
                held = f"{item.held}/{item.needed}" if item.needed > 1 else ""
                outstanding.append(
                    f"{item.requirement.label}"
                    + (f" ({held})" if held else "")
                    + (" — needs a human" if item.state == RequirementState.RECEIVED else ""))

        if spec.key == "HISTORY":
            # Periods are the unit here, not checklist ticks.
            for entry in case.career:
                total += 1
                if entry.state.value in ("VERIFIED", "COVERED_BY_STAT_DEC"):
                    done += 1
                else:
                    outstanding.append(
                        f"{entry.organisation or entry.entry_type.value} "
                        f"({entry.start.isoformat()} → "
                        f"{entry.end.isoformat() if entry.end else 'present'})")

        if spec.key == "DECISION":
            total += 1
            if case.decisions:
                done += 1
            else:
                outstanding.append("No employment decision recorded")

        blockers = actions = 0
        for code in spec.finding_codes:
            for finding in findings_by_code.get(code, []):
                if finding.severity.value == "BLOCKER":
                    blockers += 1
                elif finding.severity.value in ("ACTION", "SIGNOFF"):
                    actions += 1

        if blockers:
            state = StageState.BLOCKED
        elif done == total and actions == 0:
            # Includes the vacant case (total 0, no findings): a stage this
            # pack asks nothing of and that nothing flags is not work waiting.
            #
            # `actions == 0` is load-bearing. A stage whose every tick is
            # ticked can still carry an outstanding finding — an undeclared
            # gap belongs to Career history even when no career entry exists
            # to be counted, so counting only the ticks would have shown
            # "0/0 COMPLETE" over a screening period nothing covers. Ticks are
            # not the measure; the findings are.
            state = StageState.COMPLETE
        elif done == 0:
            state = StageState.NOT_STARTED
        else:
            state = StageState.IN_PROGRESS

        stages.append(Stage(
            key=spec.key, label=spec.label, hint=spec.hint, state=state,
            done=done, total=total, blockers=blockers, actions=actions,
            outstanding=tuple(outstanding),
        ))
    return stages


def current_stage(stages: list[Stage]) -> str:
    """The one an officer should open next.

    First blocked stage, else the first that is not complete, else DECISION.
    A single answer to "where is this file?" is what the card face needs;
    everything finer belongs inside the card.
    """
    for stage in stages:
        if stage.state == StageState.BLOCKED:
            return stage.key
    for stage in stages:
        if stage.state != StageState.COMPLETE:
            return stage.key
    return STAGE_SPECS[-1].key
