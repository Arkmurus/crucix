"""R-F2786 — customer-facing five-question DD decision-readiness gate."""
from __future__ import annotations

import pytest

from aria_service.intel.dd_orchestrator import (
    _assemble_bluf,
    _refresh_persisted_decision_readiness,
)
from aria_service.intel.dd_schema import ARKDDReport, _dd_decision_readiness, structured_view


def _decision_grade_report() -> ARKDDReport:
    report = ARKDDReport(target={"name": "Example Defence plc", "type": "company"})
    report.identity.entity_name = "Example Defence plc"
    report.identity.entity_type = "company"
    report.identity.registration_number = "01234567"
    report.identity.registration_status = "active"
    report.identity.incorporation_date = "2001-01-01"
    report.identity.directors = [{"name": "A Director"}]
    report.identity.shareholders = [{"name": "Public shareholders"}]
    report.identity.sanctions_screen = {
        "matches": [],
        "verified_sources": {"uk_ofsi": {"status": "CLEAN"}},
    }
    report.compliance.export_control = {"recommendation": "classification review complete"}
    report.compliance.financial_health = {
        "data_available": True,
        "health_verdict": "HEALTHY",
        "sources": [{"url": "https://example.test/filing"}],
    }
    report.digital.press_coverage = [
        {"title": f"Independent source {i}", "url": f"https://source{i}.test/report"}
        for i in range(8)
    ]
    report.digital.source_tier_breakdown = {"T1": 3, "T2": 2, "T3": 0}
    report.verification.citations_checked = 8
    report.verification.citations_grounded = 8
    report.verification.citation_grounding_rate = 1.0
    # A completed search needs at least one classified item under the existing
    # Grade-A quality contract; it need not be a material adverse finding.
    report.adverse_media = {
        "ok": True,
        "templates_run": 30,
        "partial": False,
        "timed_out": False,
        "findings_count": 1,
        "findings": [{"title": "Routine trade coverage", "source_tier": 4}],
    }
    report.risk_classification = "GREEN"
    report.synthesis.risk_classification = "GREEN"
    return report


def test_rf2786_bae_shape_is_not_decision_ready() -> None:
    """CAPABILITY: a GREEN-like report with UNKNOWN financials cannot clear."""
    report = _decision_grade_report()
    report.compliance.financial_health = {
        "data_available": False,
        "health_verdict": "UNKNOWN",
        "reason": "not a US filer",
    }

    readiness = _dd_decision_readiness(report.as_dict())

    assert readiness["status"] == "NOT_CLEARED"
    assert readiness["clearance_ready"] is False
    assert readiness["completion_pct"] == 80
    assert readiness["questions"]["financial_capacity"]["status"] == "UNRESOLVED"
    assert "financial" in " ".join(readiness["blocking_reasons"]).lower()


def test_rf2786_ubo_budget_exhaustion_blocks_clearance() -> None:
    report = _decision_grade_report()
    report.identity.shareholders = []
    report.network.ubo_chain = [{"name": "Intermediate Holdco"}]
    report.network.ubo_chain_walk = {
        "stats": {"budget_exhausted": True},
        "coverage_gaps": ["UBO walk budget exhausted at 50 nodes"],
    }

    readiness = _dd_decision_readiness(report.as_dict())

    assert readiness["clearance_ready"] is False
    assert readiness["questions"]["ownership_control"]["status"] == "INCOMPLETE"


def test_rf2786_all_five_questions_answered_is_decision_ready() -> None:
    readiness = _dd_decision_readiness(_decision_grade_report().as_dict())

    assert readiness["status"] == "CLEARED_FOR_RELIANCE"
    assert readiness["clearance_ready"] is True
    assert readiness["completion_pct"] == 100
    assert readiness["evidence_grade"] == "A"
    assert not readiness["blocking_reasons"]


def test_rf2786_five_answers_with_weak_grounding_still_cannot_clear() -> None:
    report = _decision_grade_report()
    report.digital.press_coverage = []
    report.digital.source_tier_breakdown = {}
    report.verification.citations_checked = 0
    report.verification.citations_grounded = 0

    readiness = _dd_decision_readiness(report.as_dict())

    assert readiness["completion_pct"] == 100
    assert readiness["evidence_ready"] is False
    assert readiness["clearance_ready"] is False
    assert "Grade A" in " ".join(readiness["blocking_reasons"])


@pytest.mark.asyncio
async def test_rf2786_green_bluf_is_prohibited_when_financials_unknown() -> None:
    """CAPABILITY: drive the real BLUF assembler that produced the sampled false clean."""
    report = _decision_grade_report()
    report.compliance.financial_health = {
        "data_available": False,
        "health_verdict": "UNKNOWN",
    }

    await _assemble_bluf(report)

    assert report.risk_classification == "GREEN"  # observed risk remains separate
    assert report.decision_readiness["status"] == "NOT_CLEARED"
    assert "NOT CLEARED" in report.bottom_line
    assert "Standard contracting path available" not in report.bottom_line
    assert "financial" in report.bottom_line.lower()


def test_rf2786_structured_view_exposes_five_question_scorecard() -> None:
    report = _decision_grade_report()
    report.compliance.financial_health = {"data_available": False, "health_verdict": "UNKNOWN"}

    view = structured_view(report.as_dict())

    assert view["decision_readiness"]["completion_pct"] == 80
    assert set(view["decision_readiness"]["questions"]) == {
        "identity",
        "sanctions_export_control",
        "adverse_media",
        "ownership_control",
        "financial_capacity",
    }


def test_rf2786_markdown_and_web_render_the_scorecard() -> None:
    report = _decision_grade_report()
    report.compliance.financial_health = {"data_available": False, "health_verdict": "UNKNOWN"}

    markdown = report.render_markdown()
    web = ("public/dd-reports.html")
    with open(web, encoding="utf-8") as handle:
        html = handle.read()

    assert "Decision Readiness: NOT_CLEARED (4/5 — 80%)" in markdown
    assert "Financial capacity: UNRESOLVED" in markdown
    assert "renderDecisionReadiness(dr)" in html
    assert "Decision readiness:" in html


def test_rf2786_adverse_followup_refreshes_stored_bluf_without_overwriting_risk() -> None:
    report = _decision_grade_report()
    body = report.as_dict()
    body["adverse_media"] = {"status": "in_progress"}

    initial = _refresh_persisted_decision_readiness(body)
    assert initial["clearance_ready"] is False
    assert "NOT CLEARED" in body["bottom_line"]

    body["adverse_media"] = {
        "ok": True,
        "templates_run": 30,
        "partial": False,
        "timed_out": False,
        "findings_count": 0,
        "findings": [],
    }
    completed = _refresh_persisted_decision_readiness(body)
    assert completed["clearance_ready"] is True
    assert "all five decision-critical questions are answered" in body["bottom_line"]

    body["risk_classification"] = "AMBER-LIGHT"
    body["bottom_line"] = "credible adverse-media escalation"
    _refresh_persisted_decision_readiness(body)
    assert body["bottom_line"] == "credible adverse-media escalation"


def test_rf2786_tampered_nested_state_fails_closed() -> None:
    readiness = _dd_decision_readiness({
        "identity": ["not", "a", "mapping"],
        "compliance": "corrupt",
        "network": None,
        "adverse_media": 42,
    })

    assert readiness["status"] == "NOT_CLEARED"
    assert readiness["completion_pct"] == 0
    assert len(readiness["blocking_reasons"]) == 6
