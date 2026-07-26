"""Jurisdiction-neutral deterministic rules engine — Phase 0 hardening.

Changes per external review: explicit as_of everywhere (no system-clock
calls in domain logic — replay is deterministic), leap-day-safe year
arithmetic, timeline integrity findings (overlaps, duplicates, future-
dated, impossible ranges), Money-aware sign-off triggers with no silent
currency conversion, decision-eligibility surfaced from the pack.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from enum import Enum
from itertools import combinations

from .models import CareerEntryType, VerificationState, VettingCase
from .packs.base import ScreeningPack


class Severity(str, Enum):
    BLOCKER = "BLOCKER"
    SIGNOFF = "SIGNOFF"
    ACTION = "ACTION"
    DEADLINE = "DEADLINE"
    INFO = "INFO"


@dataclass(frozen=True)
class Finding:
    code: str
    severity: Severity
    ref: str
    message: str
    entry_id: str | None = None
    due_date: date | None = None


@dataclass(frozen=True)
class TimelineGap:
    start: date
    end: date
    days: int
    kind: str
    entry_id: str | None = None


def shift_years(d: date, years: int) -> date:
    """Leap-day-safe: 29 Feb maps to 28 Feb in non-leap years."""
    try:
        return d.replace(year=d.year + years)
    except ValueError:
        return d.replace(year=d.year + years, day=28)


# ---------------------------------------------------------------- period --

def screening_period(case: VettingCase, pack: ScreeningPack) -> tuple[date, date]:
    years = case.screening_years or pack.default_screening_years
    nominal = shift_years(case.employment_start, -years)
    floor = shift_years(case.date_of_birth, pack.min_age_floor)
    return (max(nominal, floor), case.employment_start)


# ----------------------------------------------------- timeline integrity --

_CAN_OVERLAP = {CareerEntryType.EDUCATION, CareerEntryType.SELF_EMPLOYMENT}


def integrity_findings(case: VettingCase, as_of: date) -> list[Finding]:
    out: list[Finding] = []
    seen: set[tuple] = set()
    for e in case.career:
        if e.start > as_of:
            out.append(Finding("FUTURE_DATED_HISTORY", Severity.BLOCKER, "core",
                               f"Entry '{e.organisation or e.entry_type.value}' "
                               f"starts {e.start.isoformat()}, after the "
                               f"assessment date {as_of.isoformat()}.",
                               entry_id=e.entry_id))
        key = (e.entry_type, e.organisation, e.start, e.end)
        if key in seen:
            out.append(Finding("DUPLICATE_ENTRY", Severity.ACTION, "core",
                               f"Duplicate declaration: "
                               f"{e.organisation or e.entry_type.value} "
                               f"{e.start.isoformat()}.", entry_id=e.entry_id))
        seen.add(key)
    for a, b in combinations(case.career, 2):
        a_end, b_end = a.end or as_of, b.end or as_of
        if a.start <= b_end and b.start <= a_end:            # overlap
            if a.entry_type in _CAN_OVERLAP or b.entry_type in _CAN_OVERLAP:
                continue                                     # legitimate simultaneity
            out.append(Finding(
                "OVERLAPPING_DECLARATIONS", Severity.ACTION, "core",
                f"'{a.organisation or a.entry_type.value}' and "
                f"'{b.organisation or b.entry_type.value}' overlap between "
                f"{max(a.start, b.start).isoformat()} and "
                f"{min(a_end, b_end).isoformat()}; ask the applicant to "
                f"clarify (may be legitimate concurrent work).",
            ))
    return out


# ------------------------------------------------------------------ gaps --

def find_gaps(case: VettingCase, pack: ScreeningPack, as_of: date) -> list[TimelineGap]:
    start, end = screening_period(case, pack)
    gaps: list[TimelineGap] = []
    entries = sorted((e for e in case.career if (e.end or end) >= start),
                     key=lambda e: e.start)
    cursor = start
    for e in entries:
        e_start, e_end = max(e.start, start), min(e.end or end, end)
        if e_start > cursor:
            days = (e_start - cursor).days
            if days > 0:
                gaps.append(TimelineGap(cursor, e_start, days, "UNDECLARED"))
        cursor = max(cursor, e_end + timedelta(days=1))
    if cursor <= end:
        gaps.append(TimelineGap(cursor, end, (end - cursor).days + 1, "UNDECLARED"))
    for e in entries:
        if e.state in (VerificationState.VERIFIED, VerificationState.COVERED_BY_STAT_DEC):
            continue
        e_start, e_end = max(e.start, start), min(e.end or end, end)
        gaps.append(TimelineGap(e_start, e_end, (e_end - e_start).days + 1,
                                "UNVERIFIED", entry_id=e.entry_id))
    return gaps


def gap_findings(case: VettingCase, pack: ScreeningPack, as_of: date) -> list[Finding]:
    out: list[Finding] = []
    for g in find_gaps(case, pack, as_of):
        if g.kind == "UNDECLARED":
            out.append(Finding("GAP_UNDECLARED", Severity.ACTION, pack.pack_id,
                               f"No career entry covers {g.start.isoformat()} to "
                               f"{g.end.isoformat()} ({g.days} days). Ask the "
                               f"applicant to account for this period."))
        elif g.days > pack.max_unverified_gap_days:
            out.append(Finding("GAP_UNVERIFIED_OVER_LIMIT", Severity.BLOCKER,
                               pack.pack_id,
                               f"Unverified period of {g.days} days "
                               f"({g.start.isoformat()}-{g.end.isoformat()}) "
                               f"exceeds the {pack.max_unverified_gap_days}-day "
                               f"limit; obtain written verification or assess "
                               f"declaration eligibility.", entry_id=g.entry_id))
    return out


# -------------------------------------------------------------- evidence --

def evidence_findings(case: VettingCase, pack: ScreeningPack, as_of: date) -> list[Finding]:
    doc_index = {d.document_id: d for d in case.documents}
    out: list[Finding] = []
    for e in case.career:
        if e.state == VerificationState.VERIFIED:
            continue
        accepted = pack.accepted_evidence.get(e.entry_type, [])
        ref = pack.evidence_references.get(e.entry_type, pack.pack_id)
        have = {doc_index[d].doc_type for d in e.supporting_documents if d in doc_index}
        if accepted and not have & set(accepted):
            wanted = ", ".join(t.value for t in accepted[:4])
            out.append(Finding(
                "EVIDENCE_MISSING", Severity.ACTION, ref,
                f"'{e.organisation or e.entry_type.value}' "
                f"({e.start.isoformat()}-{(e.end or as_of).isoformat()}): no "
                f"accepted evidence attached yet. Acceptable: {wanted}.",
                entry_id=e.entry_id))
            continue

        # R-F3174 — BS 7858 7.7 b): a direct reference from the employer stands
        # alone, but where one cannot be obtained the fallback is "two or more
        # DIFFERENT items" of documentary evidence. Accepting a single payslip
        # in place of a reference builds a thinner file than the standard asks
        # for, and it is the substitution most likely to be made under time
        # pressure.
        minimum = pack.min_documentary_items_without_reference
        direct = set(pack.direct_reference_documents)
        if minimum > 1 and accepted and not (have & direct) and len(have) < minimum:
            out.append(Finding(
                "EVIDENCE_INSUFFICIENT", Severity.ACTION, ref,
                f"'{e.organisation or e.entry_type.value}' "
                f"({e.start.isoformat()}-{(e.end or as_of).isoformat()}): "
                f"{len(have)} documentary item(s) on file and no direct "
                f"reference. Without a reference this period needs at least "
                f"{minimum} different items of evidence.",
                entry_id=e.entry_id))
    return out


# ------------------------------------------------------------- checklist --

def checklist_findings(case: VettingCase, pack: ScreeningPack) -> list[Finding]:
    out: list[Finding] = []
    for spec in pack.checklist:
        if not getattr(case.inputs, spec.field, False):
            out.append(Finding("CHECKLIST_MISSING", Severity.ACTION,
                               spec.reference, f"Outstanding: {spec.label}."))
    if pack.criminality_routes and (
        case.inputs.criminality_route not in pack.criminality_routes
    ):
        routes = ", ".join(r.value for r in pack.criminality_routes)
        out.append(Finding("CRIMINALITY_ROUTE_MISSING", Severity.ACTION,
                           pack.criminality_reference,
                           f"No accepted criminality/conduct route on file yet. "
                           f"Accepted for {pack.display_name}: {routes}."))
    return out


# -------------------------------------------------------------- signoffs --

def signoff_findings(case: VettingCase, pack: ScreeningPack) -> list[Finding]:
    out: list[Finding] = []
    f = case.financial
    for trig in pack.signoff_triggers:
        if trig.predicate == "ccj_over_threshold":
            if f.ccj_total is None:
                continue
            thr = trig.threshold
            if thr is not None and f.ccj_total.currency != thr.currency:
                out.append(Finding(
                    "FINANCIAL_CURRENCY_REVIEW", Severity.SIGNOFF, trig.reference,
                    f"Judgment total is in {f.ccj_total.currency} but the pack "
                    f"threshold is in {thr.currency}: manual review required — "
                    f"amounts are never silently currency-converted."))
            elif thr is None or f.ccj_total.amount_minor > thr.amount_minor:
                out.append(Finding(trig.code, Severity.SIGNOFF, trig.reference,
                                   trig.message + f" (total: {f.ccj_total})"))
        elif trig.predicate == "is_bankrupt" and f.is_bankrupt:
            out.append(Finding(trig.code, Severity.SIGNOFF, trig.reference, trig.message))
        elif trig.predicate == "is_director" and f.is_or_was_director:
            out.append(Finding(trig.code, Severity.SIGNOFF, trig.reference, trig.message))
    return out


# ----------------------------------------------------------------- clock --

def deadline_findings(case: VettingCase, pack: ScreeningPack, as_of: date) -> list[Finding]:
    if case.conditional_employment_start is None:
        return []
    years = case.screening_years or pack.default_screening_years
    weeks = pack.full_screening_weeks.get(years)
    if weeks is None:
        return [Finding("PACK_PERIOD_UNSUPPORTED", Severity.BLOCKER, pack.pack_id,
                        f"{years}-year screening is not defined in pack "
                        f"{pack.pack_id} v{pack.version}.")]
    deadline = case.conditional_employment_start + timedelta(weeks=weeks)
    if case.extension_approved:
        deadline += timedelta(weeks=pack.extension_weeks)
    remaining = (deadline - as_of).days
    if remaining < 0:
        return [Finding("SCREENING_OVERDUE", Severity.BLOCKER, pack.pack_id,
                        f"Full screening deadline passed {-remaining} days ago "
                        f"({deadline.isoformat()}). The individual should not "
                        f"continue in relevant employment until screening is "
                        f"completed.", due_date=deadline)]
    sev = Severity.DEADLINE if remaining <= 21 else Severity.INFO
    return [Finding("SCREENING_DEADLINE", sev, pack.pack_id,
                    f"Full screening due by {deadline.isoformat()} "
                    f"({remaining} days remaining).", due_date=deadline)]


# ------------------------------------------------------ document sighting --

def sighting_findings(case: VettingCase, pack: ScreeningPack) -> list[Finding]:
    """R-F3189 — BS 7858 7.4 c)/d): originals sighted, and by whom.

    Two distinct findings, because they need different actions:
      COPY_ONLY      → someone must see the original. An ACTION.
      NOT_RECORDED   → nobody has answered the question. Also an ACTION, but a
                       different one: it is unknown, NOT a failure. Collapsing
                       the two would either invent a breach that has not
                       happened, or hide one that has.

    A missing examiner name is separate again: the standard requires a record
    of WHO examined the original, and "an original was seen" with nobody
    attached to it cannot be evidenced to an auditor.
    """
    out: list[Finding] = []
    required = set(pack.originals_required)
    if not required:
        return out
    for doc in case.documents:
        if doc.doc_type not in required:
            continue
        name = doc.doc_type.value
        if doc.sighting == "COPY_ONLY":
            out.append(Finding(
                "ORIGINAL_NOT_SIGHTED", Severity.ACTION, "7.4 c)",
                f"{name}: only a copy is held. The original must be visually "
                f"inspected and a copy retained.",
            ))
        elif doc.sighting == "NOT_RECORDED":
            out.append(Finding(
                "SIGHTING_NOT_RECORDED", Severity.ACTION, "7.4 c)",
                f"{name}: it is not recorded whether the original was sighted "
                f"or only a copy held.",
            ))
        elif doc.sighting == "ORIGINAL_SEEN" and not doc.examined_by.strip():
            out.append(Finding(
                "EXAMINER_NOT_RECORDED", Severity.ACTION, "7.4 c)",
                f"{name}: the original was sighted but no examiner is named. "
                f"The standard requires a record of who examined and copied it.",
            ))
    return out


# ------------------------------------------------------- coverage map ----

@dataclass(frozen=True)
class CoverageMonth:
    """One cell of the screening-period grid."""

    year: int
    month: int
    state: str                      # VERIFIED | UNVERIFIED | UNDECLARED
    entry_ids: tuple[str, ...] = ()


def coverage_map(
    case: VettingCase, pack: ScreeningPack, as_of: date,
) -> list[CoverageMonth]:
    """R-F3177 — month-by-month coverage across the screening period.

    This replaces the grid at the centre of the manual Verification Progress
    Sheet, where an officer colours in JAN..DEC for each year of the screening
    period. It is the artefact the whole sheet is organised around: it answers
    "what is still uncovered?" at a glance, which a findings LIST does not —
    a list tells you a gap exists, a grid shows you where it sits and how big.

    Three states, deliberately distinguished (the sheet conflates the last two
    by leaving a cell blank):
      VERIFIED   — covered by an entry that has been verified, or covered by an
                   accepted statutory declaration
      UNVERIFIED — declared by the applicant but not yet verified. Present on
                   the file, NOT yet evidence.
      UNDECLARED — nothing on the file covers this month at all.

    A month counts as covered if ANY day in it is covered, and the strongest
    state wins. That is deliberately generous at the month boundary and is why
    the grid is a navigation aid, not the compliance test — `gap_findings`
    remains the authority, because it works in days and the 31-day rule is a
    day rule. Presenting the grid as the test would round a 31-day breach down
    to "two covered months".
    """
    start, end = screening_period(case, pack)

    # Rank so the strongest state for a month wins.
    rank = {"UNDECLARED": 0, "UNVERIFIED": 1, "VERIFIED": 2}
    cells: dict[tuple[int, int], tuple[str, list[str]]] = {}

    year, month = start.year, start.month
    while (year, month) <= (end.year, end.month):
        cells[(year, month)] = ("UNDECLARED", [])
        month += 1
        if month > 12:
            year, month = year + 1, 1

    verified_states = {VerificationState.VERIFIED,
                       VerificationState.COVERED_BY_STAT_DEC}
    for entry in case.career:
        entry_end = min(entry.end or end, end)
        entry_start = max(entry.start, start)
        if entry_end < entry_start:
            continue
        state = ("VERIFIED" if entry.state in verified_states else "UNVERIFIED")
        y, m = entry_start.year, entry_start.month
        while (y, m) <= (entry_end.year, entry_end.month):
            key = (y, m)
            if key in cells:
                current, ids = cells[key]
                if rank[state] > rank[current]:
                    current = state
                if entry.entry_id not in ids:
                    ids = [*ids, entry.entry_id]
                cells[key] = (current, ids)
            m += 1
            if m > 12:
                y, m = y + 1, 1

    return [
        CoverageMonth(year=y, month=m, state=st, entry_ids=tuple(ids))
        for (y, m), (st, ids) in sorted(cells.items())
    ]


# ---------------------------------------------------------------- assess --

def assess(case: VettingCase, pack: ScreeningPack, as_of: date) -> dict:
    """as_of is REQUIRED: the engine never reads the system clock, so any
    assessment can be replayed byte-identically at any later time."""
    findings = (
        integrity_findings(case, as_of)
        + checklist_findings(case, pack)
        + gap_findings(case, pack, as_of)
        + evidence_findings(case, pack, as_of)
        + sighting_findings(case, pack)
        + signoff_findings(case, pack)
        + deadline_findings(case, pack, as_of)
    )
    blockers = [f for f in findings if f.severity == Severity.BLOCKER]
    signoffs = [f for f in findings if f.severity == Severity.SIGNOFF]
    actions = [f for f in findings if f.severity == Severity.ACTION]
    if blockers:
        status = "NOT_READY"
    elif signoffs:
        status = "AWAITING_SIGNOFF"
    elif actions:
        status = "IN_PROGRESS"
    elif pack.employment_decision_eligible:
        status = "READY_FOR_CONTROLLER_REVIEW"
    else:
        status = "EVIDENCE_COMPLETE"     # framework packs never issue readiness
    start, end = screening_period(case, pack)
    months = coverage_map(case, pack, as_of)
    return {
        "case_id": case.case_id,
        "as_of": as_of.isoformat(),
        "pack": {"pack_id": pack.pack_id, "version": pack.version,
                 "jurisdiction": pack.jurisdiction, "status": pack.status.value,
                 "legal_coverage": pack.legal_coverage.value,
                 "employment_decision_eligible": pack.employment_decision_eligible,
                 "content_hash": pack.content_hash()},
        "screening_period": [start.isoformat(), end.isoformat()],
        "status": status,
        "findings": [f.__dict__ for f in findings],
        "controller_notes": pack.controller_notes,
        "counts": {"blockers": len(blockers), "signoffs": len(signoffs),
                   "actions": len(actions)},
        # R-F3177 — the month grid from the manual progress sheet. A navigation
        # aid, NOT the compliance test: it works in months, the 31-day rule
        # works in days, and `findings` remains the authority.
        "coverage": [
            {"year": c.year, "month": c.month, "state": c.state,
             "entry_ids": list(c.entry_ids)} for c in months
        ],
        "coverage_summary": {
            "months_total": len(months),
            "verified": sum(1 for c in months if c.state == "VERIFIED"),
            "unverified": sum(1 for c in months if c.state == "UNVERIFIED"),
            "undeclared": sum(1 for c in months if c.state == "UNDECLARED"),
        },
    }
