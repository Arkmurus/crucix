"""Built-in packs — Phase 0 statuses.

uk_bs7858     PRODUCTION · JURISDICTION_REVIEWED · decision-eligible
intl_baseline PRODUCTION · FRAMEWORK_ONLY · NOT decision-eligible
              (organises evidence anywhere; never issues jurisdictional
              decision-readiness)
pt_generic    DRAFT — structure only, pending local legal review
"""

from __future__ import annotations

from ..models import CareerEntryType as C
from ..models import DocumentRequirement as R
from ..models import DocumentType as D
from ..models import Money
from .base import (
    ChecklistSpec, LegalCoverage, PackStatus, ScreeningPack, SignoffTrigger,
    registry,
)

_COMMON_CHECKLIST = [
    ChecklistSpec(field="full_name", label="Full name recorded", reference="core"),
    ChecklistSpec(field="previous_names_declared", label="Previous names/aliases declared", reference="core"),
    ChecklistSpec(field="date_of_birth", label="Date of birth recorded", reference="core"),
    ChecklistSpec(field="identity_verified", label="Identity verified (photographic preferred)", reference="core"),
    ChecklistSpec(field="address_confirmed", label="Current address confirmed", reference="core"),
    ChecklistSpec(field="right_to_work_evidenced", label="Right-to-work / work-permit evidence", reference="core"),
    ChecklistSpec(field="screening_consent_signed", label="Signed screening consent", reference="core"),
    ChecklistSpec(field="verification_authorisation_signed", label="Written verification authorisation", reference="core"),
    ChecklistSpec(field="watchlist_check_done", label="Sanctions/watchlist check", reference="core"),
]

_EVIDENCE = {
    C.EMPLOYMENT: [D.EMPLOYER_REFERENCE, D.PAYSLIP, D.P60, D.P45,
                   D.EMPLOYMENT_CONTRACT, D.REDUNDANCY_LETTER, D.BANK_STATEMENT],
    C.UNEMPLOYMENT: [D.DWP_CONFIRMATION, D.OTHER],
    C.SELF_EMPLOYMENT: [D.HMRC_DOCUMENT, D.BANK_STATEMENT, D.ACCOUNTANT_REFERENCE],
    C.EDUCATION: [D.EDUCATION_REFERENCE],
    C.CAREER_BREAK: [D.OTHER],
    C.RESIDENCE_ABROAD: [D.PASSPORT, D.EMPLOYER_REFERENCE],
    C.TRAVEL_ABROAD: [D.TRAVEL_EVIDENCE],
}

UK_BS7858 = ScreeningPack(
    pack_id="uk_bs7858", jurisdiction="GB",
    display_name="UK — aligned with BS 7858:2019",
    version="1.1.0", status=PackStatus.PRODUCTION,
    legal_coverage=LegalCoverage.JURISDICTION_REVIEWED,
    employment_decision_eligible=True,
    legal_review_ref="MGE screening controller review 2026-07",
    source_references=["BS 7858:2019 (licensed copy required per customer)"],
    default_screening_years=5, alternative_screening_years=[10],
    max_unverified_gap_days=31, limited_screening_years=3,
    full_screening_weeks={5: 12, 10: 16}, extension_weeks=4,
    declaration_max_days_per_block=183,
    checklist=_COMMON_CHECKLIST + [
        ChecklistSpec(field="address_history_5y", label="Five-year address history", reference="7.3.2 a)4)"),
        ChecklistSpec(field="ni_number", label="National Insurance number", reference="7.3.2 a)6)"),
        ChecklistSpec(field="convictions_declared", label="Convictions/cautions declaration", reference="7.3.2 c)"),
        ChecklistSpec(field="financial_history_declared", label="Bankruptcy/CCJ/IVA declaration", reference="7.3.2 d)"),
        ChecklistSpec(field="misrepresentation_ack_signed", label="Misrepresentation acknowledgement", reference="7.3.2 e)"),
        ChecklistSpec(field="interview_done", label="Interview before any offer", reference="7.3.4"),
        ChecklistSpec(field="public_record_search_done", label="Public record search via CRA", reference="7.4 f)"),
    ],
    accepted_evidence=_EVIDENCE,
    evidence_references={C.EMPLOYMENT: "7.7 b)", C.UNEMPLOYMENT: "7.7 c)",
                         C.SELF_EMPLOYMENT: "7.7 d)", C.EDUCATION: "7.7 a)",
                         C.CAREER_BREAK: "7.7 e)", C.RESIDENCE_ABROAD: "7.7 f)",
                         C.TRAVEL_ABROAD: "7.7 g)"},
    criminality_routes=[D.SIA_LICENCE, D.NPCC_POLICE_LETTER, D.DISCLOSURE_CERTIFICATE],
    criminality_reference="7.7 j)",
    signoff_triggers=[
        SignoffTrigger(code="SIGNOFF_CCJ", reference="7.4 f) i)",
                       predicate="ccj_over_threshold",
                       threshold=Money(amount_minor=1_000_000, currency="GBP"),
                       message="CCJ/IVA total above threshold: top-management acceptance-of-risk sign-off required."),
        SignoffTrigger(code="SIGNOFF_BANKRUPTCY", reference="7.4 f) ii)",
                       predicate="is_bankrupt",
                       message="Bankruptcy on record: top-management sign-off required."),
        SignoffTrigger(code="SIGNOFF_DIRECTORSHIP", reference="7.4 f) iii)",
                       predicate="is_director",
                       message="Current/former directorship: top-management sign-off required; registry search advisable."),
    ],
    retention_unsuccessful_months=12, retention_post_employment_years=7,
    controller_notes=[
        "Screening staff must themselves be screened; no self-screening; NDAs on file.",
        "Insurers may impose longer periods — set screening_years per contract.",
        "Telephone numbers for referees must be independently ascertained.",
    ],
)

INTL_BASELINE = ScreeningPack(
    pack_id="intl_baseline", jurisdiction="INTL",
    display_name="International baseline (evidence-first)",
    version="1.1.0", status=PackStatus.PRODUCTION,
    legal_coverage=LegalCoverage.FRAMEWORK_ONLY,
    employment_decision_eligible=False,
    legal_review_ref=None,
    source_references=["ARIA evidence-first methodology"],
    default_screening_years=5, alternative_screening_years=[3, 7, 10],
    max_unverified_gap_days=31,
    full_screening_weeks={3: 12, 5: 12, 7: 16, 10: 16}, extension_weeks=4,
    checklist=_COMMON_CHECKLIST,
    accepted_evidence=_EVIDENCE,
    evidence_references={k: "baseline" for k in _EVIDENCE},
    criminality_routes=[D.DISCLOSURE_CERTIFICATE, D.NPCC_POLICE_LETTER],
    criminality_reference="baseline: local police/conduct certificate",
    signoff_triggers=[
        SignoffTrigger(code="SIGNOFF_BANKRUPTCY", reference="baseline",
                       predicate="is_bankrupt",
                       message="Insolvency on record: senior sign-off required."),
    ],
    retention_unsuccessful_months=12, retention_post_employment_years=7,
    controller_notes=[
        "FRAMEWORK_ONLY: organises and grades evidence; it does not determine "
        "whether screening is lawful or sufficient in any jurisdiction and "
        "never issues decision-readiness. Engage local counsel per country.",
    ],
)

PT_GENERIC_DRAFT = ScreeningPack(
    pack_id="pt_generic", jurisdiction="PT",
    display_name="Portugal (DRAFT — pending local legal review)",
    version="0.2.0", status=PackStatus.DRAFT,
    legal_coverage=LegalCoverage.FRAMEWORK_ONLY,
    employment_decision_eligible=False,
    source_references=["To confirm with counsel: certificado de registo criminal, "
                       "AT/SS documents for self-employment, labour-law retention"],
    default_screening_years=5, max_unverified_gap_days=31,
    full_screening_weeks={5: 12},
    checklist=_COMMON_CHECKLIST, accepted_evidence=_EVIDENCE,
    evidence_references={k: "draft" for k in _EVIDENCE},
    criminality_routes=[D.DISCLOSURE_CERTIFICATE],
    criminality_reference="draft: certificado de registo criminal",
    controller_notes=["DRAFT pack — registry will refuse production use."],
)

# R-F3136 — ensure_registered, not register: this loop runs at IMPORT time and
# must survive a double import (see PackRegistry.ensure_registered). Identical
# re-registration is a no-op; a CHANGED pack under an existing version still
# raises, so version immutability is unaffected.
# ── R-F3174: UK pack v1.2.0 — verified clause-by-clause against BS 7858:2019 ──
#
# Published as a NEW VERSION rather than an edit to 1.1.0. A pack's content hash
# is pinned in every case manifest, so editing a released pack in place would
# break `get_exact` for every existing case — replay is the whole reason the
# hash is there. Cases opened under 1.1.0 stay on 1.1.0 and remain reproducible;
# new cases get 1.2.0.
#
# What the clause-by-clause check against the licensed standard found:
#   7.3.2 a)8)  SIA licence EXPIRY was not captured (only the number).
#   7.4  c)1)   Verification against the SIA public register was absent — the
#               standard requires the register check and a retained copy of the
#               result, not merely sight of the licence.
#   7.4  f)     The public-record search is SEVEN elements; the pack had one
#               tick, so a partially-performed search could read as complete.
#   7.4  c)/d)  The standard requires a record of WHO examined the original
#               identity and address documents.
#   7.7  b)     Without a direct employer reference, TWO OR MORE different
#               documentary items are required; the engine accepted one.
#
# NOT changed: the 31-day unverified-period limit. The standard says "no
# unverified periods greater than 31 days" (7.7), and the engine flags at 32+,
# which is exactly that. A stricter house limit is a per-contract setting, not a
# correction to the pack.
UK_BS7858_V120 = UK_BS7858.model_copy(update={
    "version": "1.2.0",
    "checklist": _COMMON_CHECKLIST + [
        ChecklistSpec(field="address_history_5y", label="Five-year address history", reference="7.3.2 a)4)"),
        ChecklistSpec(field="ni_number", label="National Insurance number", reference="7.3.2 a)6)"),
        ChecklistSpec(field="sia_licence_expiry", label="SIA licence expiry date recorded (if licence held)", reference="7.3.2 a)8)"),
        ChecklistSpec(field="sia_register_verified", label="SIA licence verified against the public register, result retained", reference="7.4 c)1)"),
        ChecklistSpec(field="identity_examined_by", label="Who examined the original identity document", reference="7.4 c)"),
        ChecklistSpec(field="address_examined_by", label="Who examined the original address document", reference="7.4 d)"),
        ChecklistSpec(field="convictions_declared", label="Convictions/cautions declaration", reference="7.3.2 c)"),
        ChecklistSpec(field="financial_history_declared", label="Bankruptcy/CCJ/IVA declaration", reference="7.3.2 d)"),
        ChecklistSpec(field="misrepresentation_ack_signed", label="Misrepresentation acknowledgement", reference="7.3.2 e)"),
        ChecklistSpec(field="interview_done", label="Interview before any offer", reference="7.3.4"),
        # 7.4 f) — the seven required elements, each answerable on its own.
        ChecklistSpec(field="public_record_search_done", label="Public record search via credit reference agency", reference="7.4 f)"),
        ChecklistSpec(field="electoral_roll_confirmed", label="Electoral roll listing confirmed (or alternative evidence)", reference="7.4 f)1)-2)"),
        ChecklistSpec(field="linked_addresses_5y_searched", label="Linked addresses searched for the previous five years", reference="7.4 f)3)"),
        ChecklistSpec(field="ccj_iva_searched", label="CCJs and IVAs searched", reference="7.4 f)4)"),
        ChecklistSpec(field="bankruptcy_orders_searched", label="Bankruptcy orders searched", reference="7.4 f)5)"),
        ChecklistSpec(field="aliases_searched", label="Aliases searched", reference="7.4 f)6)"),
    ],
    # 7.4 c)/d) — documents that must be sighted in the ORIGINAL. Identity and
    # entitlement documents: a forged PDF passes a copy check and fails an
    # in-person one, which is the entire point of the clause.
    "originals_required": [
        D.PASSPORT, D.DRIVING_LICENCE, D.BIRTH_CERTIFICATE, D.RESIDENCE_PERMIT,
        D.SIA_LICENCE, D.DISCLOSURE_CERTIFICATE, D.PROOF_OF_ADDRESS,
    ],
    # 7.7 b) — two or more different items where no direct reference exists.
    "min_documentary_items_without_reference": 2,
    "direct_reference_documents": [
        D.EMPLOYER_REFERENCE, D.EDUCATION_REFERENCE, D.ACCOUNTANT_REFERENCE,
        D.DWP_CONFIRMATION,
    ],
    "source_references": [
        "BS 7858:2019 (licensed copy required per customer)",
        "Verified clause-by-clause 2026-07-26: 7.3.2, 7.4, 7.6, 7.7",
    ],
})


# ── R-F3207: the intake document set ──────────────────────────────────────
#
# Everything above indexes evidence by CAREER PERIOD, which is the right shape
# for "what confirms this engagement" and the wrong shape for every document
# that belongs to the person rather than to a period. The consequence was that
# a file could reach READY_FOR_CONTROLLER_REVIEW with no identity document on
# it at all — not because a rule was lenient, but because no rule asked.
#
# `basis` is not decoration. An auditor, and an officer deciding whether a
# waiver is theirs to sign, needs to know whether a requirement comes from the
# standard, from statute, or from our own intake discipline. A CV is not
# demanded by BS 7858; saying it is would be the same overstatement as marking
# an applicant's payslip a primary-official record.
_CORE_DOCUMENTS = [
    R(key="application_form", label="Application form",
      accepted=[D.APPLICATION_FORM], min_count=1,
      reference="7.3.2", basis="STANDARD", stage="APPLICATION",
      note="The declared history everything else is verified against."),
    R(key="signed_authorisation", label="Signed screening authorisation",
      accepted=[D.SIGNED_AUTHORISATION], min_count=1,
      reference="7.3.2 e)", basis="STANDARD", stage="APPLICATION",
      note="Evidence the screening was authorised. NOT the GDPR lawful "
           "basis — see the case's recorded basis for that."),
    R(key="cv", label="CV / career summary",
      accepted=[D.CV], min_count=1,
      reference="house practice", basis="HOUSE_PRACTICE", stage="APPLICATION",
      note="Not required by the standard. Held because the declared timeline "
           "is cross-checked against it, and discrepancies between the two "
           "are what a screening interview is for."),
    R(key="identity_document", label="Photographic identity document",
      accepted=[D.PASSPORT, D.DRIVING_LICENCE, D.RESIDENCE_PERMIT,
                D.BIRTH_CERTIFICATE],
      min_count=1, reference="7.4 c)", basis="STANDARD", stage="IDENTITY",
      note="The original must be sighted and a copy retained; record who "
           "examined it."),
    R(key="proof_of_address", label="Proof of address (two items)",
      accepted=[D.PROOF_OF_ADDRESS, D.BANK_STATEMENT, D.HMRC_DOCUMENT],
      min_count=2, reference="7.4 d)", basis="STANDARD", stage="IDENTITY",
      note="Two DIFFERENT items. The same document uploaded twice counts "
           "once — matching is by the digest of what was examined."),
    R(key="right_to_work", label="Right-to-work evidence",
      accepted=[D.RIGHT_TO_WORK_CHECK, D.PASSPORT, D.RESIDENCE_PERMIT],
      min_count=1, reference="Immigration, Asylum and Nationality Act 2006",
      basis="STATUTORY", stage="IDENTITY",
      note="A statutory duty in its own right; failing it is a civil penalty, "
           "not a screening non-conformity."),
    R(key="interview_record", label="Interview record",
      accepted=[D.INTERVIEW_RECORD], min_count=1,
      reference="7.3.4", basis="STANDARD", stage="INTERVIEW",
      note="The clause requires the interview before any offer, which is a "
           "claim about a date — record when it happened and who held it."),
    R(key="criminality_certificate", label="Criminal-record disclosure",
      accepted=[D.DISCLOSURE_CERTIFICATE, D.NPCC_POLICE_LETTER, D.SIA_LICENCE],
      min_count=1, reference="7.7 j)", basis="STANDARD", stage="CRIMINALITY",
      note="The certificate itself. Distinct from the checklist item, which "
           "records WHICH route was chosen — a chosen route with no "
           "certificate on file is not a completed check."),
]

# ── R-F3207: UK pack v1.3.0 ───────────────────────────────────────────────
#
# A new version, not an edit: v1.2.0's hash is pinned in the manifest of every
# case opened under it, and mutating a released pack breaks `get_exact` for all
# of them. Existing cases keep v1.2.0 and stay reproducible; new cases get the
# document set and the interview record fields.
UK_BS7858_V130 = UK_BS7858_V120.model_copy(update={
    "version": "1.3.0",
    "required_documents": _CORE_DOCUMENTS,
    "checklist": [
        *UK_BS7858_V120.checklist,
        # R-F3212 — 7.3.4 asks for an interview BEFORE any offer. A boolean
        # cannot evidence a date, and "who conducted it" is the same question
        # the standard already asks about who examined an identity document.
        ChecklistSpec(field="interview_date",
                      label="Interview date recorded", reference="7.3.4"),
        ChecklistSpec(field="interviewed_by",
                      label="Who conducted the interview", reference="7.3.4"),
    ],
    "source_references": [
        "BS 7858:2019 (licensed copy required per customer)",
        "Verified clause-by-clause 2026-07-26: 7.3.2, 7.4, 7.6, 7.7",
        "R-F3207 2026-07-27: intake document set (7.3.2, 7.3.4, 7.4 c)/d), "
        "7.7 j)) plus statutory right-to-work and one house-practice item, "
        "each labelled with its basis",
    ],
})

# The international baseline gets the SAME intake discipline minus the item
# that is a jurisdiction-specific claim: a UK statutory right-to-work duty
# does not travel. This pack organises evidence anywhere and must not imply it
# knows the local law — that is what FRAMEWORK_ONLY means.
INTL_BASELINE_V120 = INTL_BASELINE.model_copy(update={
    "version": "1.2.0",
    "required_documents": [
        r.model_copy(update={"reference": "baseline", "basis": "HOUSE_PRACTICE"})
        for r in _CORE_DOCUMENTS
        if r.key not in {"right_to_work"}
    ],
})

for _p in (UK_BS7858, UK_BS7858_V120, UK_BS7858_V130,
           INTL_BASELINE, INTL_BASELINE_V120, PT_GENERIC_DRAFT):
    registry.ensure_registered(_p)
