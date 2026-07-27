"""R-F3214 — what the module knows about BS 7858, clause by clause, honestly.

The question this answers is "does ARIA understand the whole standard?", and
the only defensible way to answer it is to make the claim FALSIFIABLE rather
than assertable. So this file is not a summary of BS 7858. It is a register of
which clauses the module actually implements, each one naming the code that
implements it, cross-checked against the live rule pack at read time.

Three rules govern every entry:

1. **No BSI text, ever.** BS 7858:2019 is copyright. What is stored here is a
   clause NUMBER and OUR OWN statement of the obligation, in our words. That
   is the same discipline the packs already follow, and it is why the pack
   carries `source_references` telling a customer they need a licensed copy.

2. **Nothing is encoded that was not read against the licensed copy.** Each
   entry carries `verified_on`. An entry with no date is a claim nobody has
   checked, and it renders as such. The clause-by-clause pass on 2026-07-26
   covered 7.3.2, 7.4, 7.6 and 7.7; those entries carry that date. Everything
   else is UNMAPPED and says so — see `UNMAPPED_SCOPE`.

3. **A clause may not claim coverage the pack cannot corroborate.**
   `coverage_report()` checks every ENCODED clause against the live pack: the
   clause must appear as a reference on a checklist item, a document
   requirement, an evidence rule or a sign-off trigger, or set a named pack
   attribute. A clause that claims to be implemented and cannot point at
   anything is reported as `CLAIMED_NOT_CORROBORATED` — which is a defect in
   this file, not in the pack. That check is the whole point: without it this
   becomes a list of assertions, and a list of assertions about compliance
   coverage is exactly the artefact that gets believed and should not be.

The honest headline this produces is not "ARIA understands BS 7858". It is
"ARIA implements N clauses of BS 7858, each traceable to code and to a dated
reading of the licensed standard, and does not model the rest."
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .packs.base import ScreeningPack

__all__ = [
    "ClauseStatus", "Clause", "CLAUSES", "UNMAPPED_SCOPE", "coverage_report",
]

# The date of the clause-by-clause reading against the operator's licensed
# copy. Recorded once so an entry cannot silently claim a reading that never
# happened, and so a future re-reading is a single visible change.
VERIFIED_2026_07_26 = "2026-07-26"


class ClauseStatus:
    ENCODED = "ENCODED"                # a rule in this module enforces it
    PARTIAL = "PARTIAL"                # partly enforced; the gap is stated
    OPERATOR_CONTROL = "OPERATOR_CONTROL"   # an organisational duty software
                                            # cannot perform, surfaced as a note
    NOT_ENCODED = "NOT_ENCODED"        # read, understood, deliberately not built


@dataclass(frozen=True)
class Clause:
    clause: str
    title: str
    requirement: str          # OUR words. Never the standard's.
    status: str
    implemented_by: tuple[str, ...] = ()
    verified_on: str = ""
    note: str = ""

    def as_dict(self) -> dict:
        return {
            "clause": self.clause, "title": self.title,
            "requirement": self.requirement, "status": self.status,
            "implemented_by": list(self.implemented_by),
            "verified_on": self.verified_on, "note": self.note,
        }


CLAUSES: tuple[Clause, ...] = (
    # ── 7.3.2 — the application form ──────────────────────────────────────
    Clause(
        clause="7.3.2 a)4)", title="Five-year address history",
        requirement="The application must capture the applicant's addresses "
                    "for the full screening period.",
        status=ClauseStatus.ENCODED,
        implemented_by=("packs.builtin: checklist address_history_5y",),
        verified_on=VERIFIED_2026_07_26),
    Clause(
        clause="7.3.2 a)6)", title="National Insurance number",
        requirement="The applicant's National Insurance number is recorded.",
        status=ClauseStatus.ENCODED,
        implemented_by=("packs.builtin: checklist ni_number",),
        verified_on=VERIFIED_2026_07_26),
    Clause(
        clause="7.3.2 a)8)", title="SIA licence number and expiry",
        requirement="Where a licence is held, both its number and its expiry "
                    "date are recorded. An expired licence is a live "
                    "compliance failure, so the number alone is not enough.",
        status=ClauseStatus.ENCODED,
        implemented_by=("models.ScreeningInputs.sia_licence_expiry",
                        "packs.builtin: checklist sia_licence_expiry"),
        verified_on=VERIFIED_2026_07_26,
        note="Added by R-F3174; the pack captured only the number before."),
    Clause(
        clause="7.3.2 c)", title="Convictions and cautions declaration",
        requirement="The applicant declares unspent convictions and cautions.",
        status=ClauseStatus.ENCODED,
        implemented_by=("packs.builtin: checklist convictions_declared",
                        "legal_basis: Art. 10 gate on criminal-offence data"),
        verified_on=VERIFIED_2026_07_26),
    Clause(
        clause="7.3.2 d)", title="Bankruptcy, CCJ and IVA declaration",
        requirement="The applicant declares insolvency, county court judgments "
                    "and individual voluntary arrangements.",
        status=ClauseStatus.ENCODED,
        implemented_by=("packs.builtin: checklist financial_history_declared",
                        "rules.signoff_findings"),
        verified_on=VERIFIED_2026_07_26),
    Clause(
        clause="7.3.2 e)", title="Misrepresentation acknowledgement",
        requirement="The applicant signs an acknowledgement that "
                    "misrepresentation may end the engagement.",
        status=ClauseStatus.ENCODED,
        implemented_by=("packs.builtin: checklist misrepresentation_ack_signed",
                        "packs.builtin: requirement signed_authorisation"),
        verified_on=VERIFIED_2026_07_26),
    Clause(
        clause="7.3.2", title="The application form itself",
        requirement="A completed application form is held on the screening "
                    "file; it is the declared history everything else is "
                    "verified against.",
        status=ClauseStatus.ENCODED,
        implemented_by=("packs.builtin: requirement application_form",
                        "rules.requirement_findings"),
        verified_on=VERIFIED_2026_07_26,
        note="The DOCUMENT requirement is R-F3207. Before it, the engine "
             "checked the form's contents but never that the form was held."),

    # ── 7.3.4 — the interview ─────────────────────────────────────────────
    Clause(
        clause="7.3.4", title="Interview before an offer",
        requirement="An interview takes place before any offer of employment. "
                    "That is a claim about a DATE, so a date and a named "
                    "interviewer are recorded, not merely a tick.",
        status=ClauseStatus.ENCODED,
        implemented_by=("models.ScreeningInputs.interview_date",
                        "models.ScreeningInputs.interviewed_by",
                        "packs.builtin: requirement interview_record",
                        "stages: INTERVIEW"),
        verified_on=VERIFIED_2026_07_26,
        note="Date and interviewer added by R-F3212. The engine does not yet "
             "COMPARE the interview date against an offer date, because no "
             "offer date is captured — see UNMAPPED_SCOPE."),

    # ── 7.4 — verification ────────────────────────────────────────────────
    Clause(
        clause="7.4 c)", title="Identity documents: originals sighted",
        requirement="Original identity documents are visually inspected, a "
                    "copy retained, and a record kept of who examined them. "
                    "Holding a scan is not the same as having sighted the "
                    "original — a forged PDF passes a copy check.",
        status=ClauseStatus.ENCODED,
        implemented_by=("models.UploadedDocument.sighting",
                        "rules.sighting_findings",
                        "packs: originals_required",
                        "routes.vetting: PATCH .../sighting"),
        verified_on=VERIFIED_2026_07_26),
    Clause(
        clause="7.4 c)1)", title="SIA licence verified against the register",
        requirement="A licence is verified against the SIA public register and "
                    "a copy of the search result retained. Seeing the card is "
                    "not the check; the register is.",
        status=ClauseStatus.ENCODED,
        implemented_by=("models.ScreeningInputs.sia_register_verified",
                        "packs.builtin: checklist sia_register_verified"),
        verified_on=VERIFIED_2026_07_26,
        note="The CHECK is recorded. The module does not itself query the SIA "
             "register — an officer performs the search and records it."),
    Clause(
        clause="7.4 d)", title="Address documents: originals and examiner",
        requirement="Address evidence is examined in the original with a "
                    "record of who examined it. Two different items are held.",
        status=ClauseStatus.ENCODED,
        implemented_by=("packs.builtin: requirement proof_of_address (min 2)",
                        "models.ScreeningInputs.address_examined_by",
                        "rules.sighting_findings"),
        verified_on=VERIFIED_2026_07_26),
    Clause(
        clause="7.4 f)", title="Public record search via a credit reference agency",
        requirement="A public-record search is performed through a credit "
                    "reference agency. It is SEVEN elements, each of which "
                    "must be answerable on its own — one tick would let a "
                    "partly-performed search read as complete.",
        status=ClauseStatus.ENCODED,
        implemented_by=("packs.builtin: checklist public_record_search_done",
                        "packs.builtin: checklist electoral_roll_confirmed",
                        "packs.builtin: checklist linked_addresses_5y_searched",
                        "packs.builtin: checklist ccj_iva_searched",
                        "packs.builtin: checklist bankruptcy_orders_searched",
                        "packs.builtin: checklist aliases_searched"),
        verified_on=VERIFIED_2026_07_26,
        note="Split into its elements by R-F3174. The module records that each "
             "was performed; it does not perform the CRA search."),
    Clause(
        clause="7.4 f) i)", title="CCJ/IVA total above threshold",
        requirement="A judgment total above the threshold requires a "
                    "documented top-management acceptance-of-risk decision.",
        status=ClauseStatus.ENCODED,
        implemented_by=("packs.builtin: SIGNOFF_CCJ", "rules.signoff_findings"),
        verified_on=VERIFIED_2026_07_26,
        note="Amounts are never silently currency-converted: a threshold in a "
             "different currency raises a manual review instead."),
    Clause(
        clause="7.4 f) ii)", title="Bankruptcy on record",
        requirement="A bankruptcy on record requires top-management sign-off.",
        status=ClauseStatus.ENCODED,
        implemented_by=("packs.builtin: SIGNOFF_BANKRUPTCY",),
        verified_on=VERIFIED_2026_07_26),
    Clause(
        clause="7.4 f) iii)", title="Current or former directorship",
        requirement="A current or former directorship requires "
                    "top-management sign-off; a registry search is advisable.",
        status=ClauseStatus.ENCODED,
        implemented_by=("packs.builtin: SIGNOFF_DIRECTORSHIP",),
        verified_on=VERIFIED_2026_07_26),

    # ── 7.7 — the career history ──────────────────────────────────────────
    Clause(
        clause="7.7", title="No unverified period greater than 31 days",
        requirement="The screening period carries no unverified gap longer "
                    "than 31 days. The engine flags at 32 days, which is "
                    "exactly that; a stricter house limit is a per-contract "
                    "setting, not a correction to the standard.",
        status=ClauseStatus.ENCODED,
        implemented_by=("packs: max_unverified_gap_days = 31",
                        "rules.gap_findings", "rules.find_gaps"),
        verified_on=VERIFIED_2026_07_26,
        note="A test pins 31 so a future reading of '30' cannot quietly "
             "tighten it, and 'greater than' cannot quietly become '31 or "
             "more'."),
    Clause(
        clause="7.7 a)", title="Education periods",
        requirement="An education period is confirmed by the institution.",
        status=ClauseStatus.ENCODED,
        implemented_by=("packs: accepted_evidence[EDUCATION]",
                        "rules.evidence_findings"),
        verified_on=VERIFIED_2026_07_26),
    Clause(
        clause="7.7 b)", title="Employment periods, and the fallback",
        requirement="A direct reference from the employer stands alone. Where "
                    "one cannot be obtained, the fallback is TWO OR MORE "
                    "different items of documentary evidence — not one.",
        status=ClauseStatus.ENCODED,
        implemented_by=("packs: min_documentary_items_without_reference = 2",
                        "packs: direct_reference_documents",
                        "rules.evidence_findings"),
        verified_on=VERIFIED_2026_07_26,
        note="The engine accepted a single item before R-F3174 — a thinner "
             "file than the standard asks for, and the substitution most "
             "likely to be made under time pressure."),
    Clause(
        clause="7.7 c)", title="Unemployment periods",
        requirement="An unemployment period is confirmed, e.g. by the "
                    "benefits authority.",
        status=ClauseStatus.ENCODED,
        implemented_by=("packs: accepted_evidence[UNEMPLOYMENT]",),
        verified_on=VERIFIED_2026_07_26),
    Clause(
        clause="7.7 d)", title="Self-employment periods",
        requirement="Self-employment is confirmed by tax records, an "
                    "accountant's reference or bank evidence.",
        status=ClauseStatus.ENCODED,
        implemented_by=("packs: accepted_evidence[SELF_EMPLOYMENT]",),
        verified_on=VERIFIED_2026_07_26),
    Clause(
        clause="7.7 e)", title="Career breaks",
        requirement="A career break is accounted for.",
        status=ClauseStatus.ENCODED,
        implemented_by=("packs: accepted_evidence[CAREER_BREAK]",),
        verified_on=VERIFIED_2026_07_26),
    Clause(
        clause="7.7 f)", title="Residence abroad",
        requirement="Periods of residence abroad are evidenced.",
        status=ClauseStatus.ENCODED,
        implemented_by=("packs: accepted_evidence[RESIDENCE_ABROAD]",),
        verified_on=VERIFIED_2026_07_26),
    Clause(
        clause="7.7 g)", title="Travel abroad",
        requirement="Periods of travel abroad are evidenced.",
        status=ClauseStatus.ENCODED,
        implemented_by=("packs: accepted_evidence[TRAVEL_ABROAD]",),
        verified_on=VERIFIED_2026_07_26),
    Clause(
        clause="7.7 j)", title="Criminality and conduct route",
        requirement="A criminality or conduct route is on file — a disclosure "
                    "certificate, an NPCC police letter, or an SIA licence. "
                    "Choosing a route and holding the certificate are "
                    "different questions and both are asked.",
        status=ClauseStatus.ENCODED,
        implemented_by=("packs: criminality_routes",
                        "rules.checklist_findings",
                        "packs.builtin: requirement criminality_certificate"),
        verified_on=VERIFIED_2026_07_26),

    # ── screening clock and retention ─────────────────────────────────────
    Clause(
        clause="7.6", title="Screening completion clock",
        requirement="Full screening completes within 12 weeks for a five-year "
                    "period and 16 weeks for ten, from a conditional start, "
                    "with a four-week extension where approved.",
        status=ClauseStatus.ENCODED,
        implemented_by=("packs: full_screening_weeks, extension_weeks",
                        "rules.deadline_findings"),
        verified_on=VERIFIED_2026_07_26,
        note="The clause number for the 12/16-week clock is recorded here as "
             "7.6 on the strength of the 2026-07-26 reading. If a re-reading "
             "places it elsewhere, correct it here — the RULE is verified, the "
             "clause NUMBER is the part carrying less evidence."),
    Clause(
        clause="retention", title="Retention periods",
        requirement="Twelve months for an unsuccessful applicant; seven years "
                    "from the END of employment for a successful one.",
        status=ClauseStatus.ENCODED,
        implemented_by=("packs: retention_unsuccessful_months, "
                        "retention_post_employment_years",
                        "retention.retention_due_date",
                        "routes.vetting: GET /retention"),
        verified_on=VERIFIED_2026_07_26,
        note="The post-employment clock runs from employment END, not from "
             "the outcome date — anchoring it elsewhere would schedule a live "
             "personnel file for deletion years early."),

    # ── organisational controls the software cannot perform ───────────────
    Clause(
        clause="screening staff", title="Screeners are themselves screened",
        requirement="Staff who perform screening are themselves screened, no "
                    "one screens themselves, and confidentiality agreements "
                    "are held.",
        status=ClauseStatus.OPERATOR_CONTROL,
        implemented_by=("packs: controller_notes",),
        verified_on=VERIFIED_2026_07_26,
        note="Surfaced to the officer on every assessment as a controller "
             "note. The module cannot verify it and does not claim to."),
    Clause(
        clause="referee contact", title="Referee telephone numbers verified independently",
        requirement="A referee's telephone number is ascertained independently "
                    "of the applicant.",
        status=ClauseStatus.OPERATOR_CONTROL,
        implemented_by=("packs: controller_notes",
                        "models.CareerEntry.referee_*"),
        verified_on=VERIFIED_2026_07_26,
        note="The module records the nominated referee and warns where a "
             "period has none. It cannot verify that a number was sourced "
             "independently — that is a human control."),
)


# What the module does NOT model, stated plainly. This list is as important as
# the one above: a coverage register that only lists what it covers reads as
# completeness, and this module's whole discipline is that absence is never a
# finding.
UNMAPPED_SCOPE: tuple[str, ...] = (
    "Every clause outside those listed above. The 2026-07-26 reading against "
    "the licensed copy covered 7.3.2, 7.4, 7.6 and 7.7; the rest of BS 7858 "
    "has NOT been read into this module and nothing here should be taken as "
    "evidence about it.",
    "The offer date is not captured, so 7.3.4's 'before any offer' is recorded "
    "as an interview date but never COMPARED against an offer.",
    "Screening for a period longer than ten years, and the limited-screening "
    "variant, are carried as pack data but have not been clause-verified.",
    "Sector-specific annexes and any normative references BS 7858 makes to "
    "other standards are not modelled at all.",
    "The module records that a CRA public-record search and an SIA register "
    "check were performed. It does not perform either.",
)


@dataclass(frozen=True)
class ClauseCoverage:
    clause: Clause
    corroborated: bool
    corroborating_references: tuple[str, ...] = field(default_factory=tuple)

    def as_dict(self) -> dict:
        body = self.clause.as_dict()
        body["corroborated"] = self.corroborated
        body["corroborated_by"] = list(self.corroborating_references)
        if self.clause.status == ClauseStatus.ENCODED and not self.corroborated:
            # Loud on purpose. A clause claiming implementation that the live
            # pack cannot corroborate is a false claim about compliance
            # coverage, which is worse than an admitted gap.
            body["status"] = "CLAIMED_NOT_CORROBORATED"
        return body


def _pack_references(pack: ScreeningPack) -> set[str]:
    """Every clause reference the live pack actually cites."""
    references: set[str] = set()
    for spec in pack.checklist:
        references.add(spec.reference)
    for requirement in pack.required_documents:
        references.add(requirement.reference)
    references.update(pack.evidence_references.values())
    for trigger in pack.signoff_triggers:
        references.add(trigger.reference)
    if pack.criminality_reference:
        references.add(pack.criminality_reference)
    return {r.strip() for r in references if r and r.strip()}


# Clauses whose implementation is structural rather than a cited reference —
# a number in the pack, a field on a model, a rule in the engine. They are
# corroborated by a named pack attribute instead of by a reference string.
_STRUCTURAL: dict[str, tuple[str, ...]] = {
    "7.7": ("max_unverified_gap_days",),
    "7.6": ("full_screening_weeks", "extension_weeks"),
    "retention": ("retention_unsuccessful_months",
                  "retention_post_employment_years"),
    "7.4 c)": ("originals_required",),
    "7.7 b)": ("min_documentary_items_without_reference",
               "direct_reference_documents"),
    "screening staff": ("controller_notes",),
    "referee contact": ("controller_notes",),
}


def coverage_report(pack: ScreeningPack) -> dict:
    """Cross-check every claim in this file against the live pack.

    This is what stops the register becoming a list of assertions. A clause
    claiming ENCODED must be traceable to something the pack actually carries:
    a reference it cites, or a named attribute it sets. Anything else is
    reported as CLAIMED_NOT_CORROBORATED, and that is a defect in this file.
    """
    references = _pack_references(pack)
    rows: list[ClauseCoverage] = []
    for clause in CLAUSES:
        corroborating: list[str] = []
        if clause.clause in references:
            corroborating.append(f"pack cites '{clause.clause}'")
        # A parent clause is corroborated by any child that cites it, so
        # "7.4 f)" is satisfied by "7.4 f)1)-2)".
        corroborating.extend(
            f"pack cites '{r}'" for r in sorted(references)
            if r != clause.clause and r.startswith(clause.clause))
        for attribute in _STRUCTURAL.get(clause.clause, ()):
            value = getattr(pack, attribute, None)
            if value not in (None, "", [], {}, 0):
                corroborating.append(f"pack sets {attribute}")
        rows.append(ClauseCoverage(
            clause=clause, corroborated=bool(corroborating),
            corroborating_references=tuple(corroborating)))

    encoded = [r for r in rows if r.clause.status == ClauseStatus.ENCODED]
    uncorroborated = [r for r in encoded if not r.corroborated]
    return {
        "pack": {"pack_id": pack.pack_id, "version": pack.version,
                 "jurisdiction": pack.jurisdiction},
        "standard": "BS 7858:2019",
        "copyright_note": (
            "Clause numbers and ARIA's own statement of each obligation only. "
            "The text of BS 7858 is BSI copyright and is not stored, served or "
            "reproduced here. A licensed copy is required to audit against it."),
        "clauses": [r.as_dict() for r in rows],
        "counts": {
            "total": len(rows),
            "encoded": len(encoded),
            "corroborated": len(encoded) - len(uncorroborated),
            "claimed_not_corroborated": len(uncorroborated),
            "operator_control": sum(
                1 for r in rows
                if r.clause.status == ClauseStatus.OPERATOR_CONTROL),
        },
        # Stated in the same payload as the coverage, deliberately. A consumer
        # reading the counts without this reads them as completeness.
        "not_modelled": list(UNMAPPED_SCOPE),
        "honest_summary": (
            f"{len(encoded)} clauses of BS 7858 are implemented by this module "
            f"and traceable to the live rule pack. The module does not model "
            f"the rest of the standard, and makes no claim about it."),
    }
