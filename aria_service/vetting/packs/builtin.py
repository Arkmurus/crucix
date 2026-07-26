"""Built-in packs — Phase 0 statuses.

uk_bs7858     PRODUCTION · JURISDICTION_REVIEWED · decision-eligible
intl_baseline PRODUCTION · FRAMEWORK_ONLY · NOT decision-eligible
              (organises evidence anywhere; never issues jurisdictional
              decision-readiness)
pt_generic    DRAFT — structure only, pending local legal review
"""

from __future__ import annotations

from ..models import CareerEntryType as C
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
for _p in (UK_BS7858, INTL_BASELINE, PT_GENERIC_DRAFT):
    registry.ensure_registered(_p)
