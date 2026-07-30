"""R-F3466 capability tests for the single-standard BS 7858 workflow."""

from datetime import date

from aria_service.vetting.models import (
    CareerEntry,
    CareerEntryType,
    DocumentType,
    ScreeningInputs,
    UploadedDocument,
    VerificationState,
    VettingCase,
)
from aria_service.vetting.packs.base import registry
from aria_service.vetting.rules import assess
from aria_service.vetting.stages import STAGE_SPECS
from aria_service.vetting.standard_map import coverage_report


def _case(*, interview: date, offer: date | None) -> VettingCase:
    return VettingCase(
        tenant_id="rf3466",
        case_id="RF3466",
        applicant_name="Test Applicant",
        date_of_birth=date(1990, 1, 1),
        employment_start=date(2026, 8, 1),
        offer_date=offer,
        inputs=ScreeningInputs(
            interview_done=True,
            interview_date=interview,
            interviewed_by="Vetting Officer",
        ),
    )


def test_interview_is_the_first_workflow_stage():
    assert [stage.key for stage in STAGE_SPECS[:2]] == [
        "INTERVIEW",
        "APPLICATION",
    ]


def test_assessment_blocks_an_interview_that_did_not_precede_the_offer():
    pack = registry.latest_usable("uk_bs7858")
    result = assess(
        _case(interview=date(2026, 7, 10), offer=date(2026, 7, 10)),
        pack,
        as_of=date(2026, 7, 30),
    )

    assert result["status"] == "NOT_READY"
    assert result["next_actions"][0]["code"] == "INTERVIEW_NOT_BEFORE_OFFER"
    assert result["next_actions"][0]["priority"] == "BLOCKER"
    assert result["next_actions"][0]["reference"] == "7.3.4"


def test_assessment_flags_missing_offer_date_instead_of_claiming_sequence():
    pack = registry.latest_usable("uk_bs7858")
    result = assess(
        _case(interview=date(2026, 7, 10), offer=None),
        pack,
        as_of=date(2026, 7, 30),
    )

    action = next(
        item for item in result["next_actions"]
        if item["code"] == "OFFER_DATE_MISSING"
    )
    assert action["priority"] == "ACTION"
    assert action["reference"] == "7.3.4"


def test_statutory_declaration_cannot_bypass_document_and_approval_controls():
    pack = registry.latest_usable("uk_bs7858")
    case = _case(interview=date(2026, 7, 1), offer=date(2026, 7, 2))
    case = case.model_copy(update={"career": [
        CareerEntry(
            entry_id="gap",
            entry_type=CareerEntryType.CAREER_BREAK,
            start=date(2026, 1, 1),
            end=date(2026, 2, 1),
            state=VerificationState.COVERED_BY_STAT_DEC,
        ),
    ]})

    result = assess(case, pack, as_of=date(2026, 7, 30))
    codes = {item["code"] for item in result["next_actions"]}
    assert "STAT_DEC_DOCUMENT_MISSING" in codes
    assert "STAT_DEC_APPROVAL_MISSING" in codes


def test_statutory_declaration_total_is_enforced_across_periods():
    pack = registry.latest_usable("uk_bs7858")
    declaration = UploadedDocument(
        document_id="stat-dec",
        doc_type=DocumentType.STATUTORY_DECLARATION,
        evidence_id="evidence-stat-dec",
        extraction_confidence=0.95,
    )
    case = _case(interview=date(2026, 7, 1), offer=date(2026, 7, 2))
    case = case.model_copy(update={
        "stat_dec_approved_by": "Top Manager",
        "documents": [declaration],
        "career": [
            CareerEntry(
                entry_id="gap",
                entry_type=CareerEntryType.CAREER_BREAK,
                start=date(2025, 1, 1),
                end=date(2025, 8, 1),
                state=VerificationState.COVERED_BY_STAT_DEC,
                supporting_documents=["stat-dec"],
            ),
        ],
    })

    result = assess(case, pack, as_of=date(2026, 7, 30))
    assert any(
        item["code"] == "STAT_DEC_TOTAL_EXCEEDED"
        and item["priority"] == "BLOCKER"
        for item in result["next_actions"]
    )


def test_clause_register_discloses_organizational_and_unbuilt_controls():
    report = coverage_report(registry.latest_usable("uk_bs7858"))
    by_clause = {row["clause"]: row for row in report["clauses"]}

    assert by_clause["4"]["status"] == "OPERATOR_CONTROL"
    assert by_clause["6.2"]["status"] == "OPERATOR_CONTROL"
    assert by_clause["7.8"]["status"] == "PARTIAL"
    assert by_clause["10"]["status"] == "NOT_ENCODED"
    assert by_clause["11"]["status"] == "ENCODED"
    assert report["not_modelled"]
