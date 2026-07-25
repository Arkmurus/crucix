"""R-F3084 — DD risk and adverse-media surfaces must agree with their evidence.

The production Measure Group Europe report exposed two root defects:

* a confirmed DISTRESSED balance sheet and red finding still aggregated to GREEN;
* adverse-media materiality filtered every raw hit, but the persisted blob omitted
  that arithmetic, so customer surfaces called all raw hits "items requiring review".
"""
import asyncio

from aria_service.intel import dd_orchestrator as ddo
from aria_service.intel.dd_schema import (
    ARKDDReport,
    Finding,
    _adverse_media_summary,
    _render_adverse_media,
)


def _raw_measure_group_hits() -> list[dict]:
    """Representative raw results from dd_3586df4d4e26."""
    findings: list[dict] = []
    for index in range(4):
        findings.append({
            "title": "brain_hook:web_search",
            "source_url": "memory://brain_hook:web_search",
            "snippet": "fraud investigation",
            "credibility_tier": 2,
            "query_executed": f"memory-{index}",
        })
    for index in range(21):
        findings.append({
            "title": "MEASURE GROUP EUROPE LTD overview - Find and update company information",
            "source_url": (
                "https://find-and-update.company-information.service.gov.uk/"
                "company/12869114"
            ),
            "snippet": "Company overview, officers and filing history",
            "credibility_tier": 1,
            "query_executed": f"registry-{index}",
        })
    findings.append({
        "title": "United Kingdom wants cozy science ties with Europe after Brexit",
        "source_url": "https://doi.org/10.1126/science.aap8919",
        "snippet": "Science policy article",
        "credibility_tier": 2,
        "source_class_corroborated": False,
    })
    return findings


def test_rf3084_distressed_financial_verdict_cannot_aggregate_to_green():
    """CAPABILITY: drive the real synthesis function that produced the green PDF."""
    report = ARKDDReport(target={"name": "Measure Group Europe Limited", "type": "company"})
    report.identity.entity_name = "Measure Group Europe Limited"
    report.identity.entity_type = "company"
    report.identity.registration_status = "active"
    report.identity.registration_number = "12869114"
    report.identity.incorporation_date = "2020-09-09"
    report.identity.directors = [{"name": "MEYERS, Andre Munroe"}]
    report.compliance.financial_health = {
        "data_available": True,
        "health_verdict": "DISTRESSED",
    }
    report.compliance.findings.append(Finding(
        severity="red",
        title="Financial health: DISTRESSED — UK filed accounts",
        detail="Balance-sheet insolvent and working-capital deficit.",
        source="companies_house_accounts",
        confidence="CONFIRMED",
    ))

    asyncio.run(ddo._run_synthesis(report.target, report))

    assert report.risk_classification == "RED"
    assert "no blocking risk" not in report.bottom_line.lower()


def test_rf3084_red_finding_in_any_decision_layer_cannot_be_green():
    """Unit contract: an explicit red evidence finding is a red risk candidate."""
    report = ARKDDReport(target={"name": "Acme Ltd", "type": "company"})
    report.identity.entity_name = "Acme Ltd"
    report.identity.entity_type = "company"
    report.digital.findings.append(Finding(
        severity="red",
        title="Court judgment",
        detail="Confirmed judgment against the subject.",
        source="court",
        confidence="CONFIRMED",
    ))

    asyncio.run(ddo._run_synthesis(report.target, report))

    assert report.risk_classification == "RED"


def test_rf3084_weak_financial_verdict_cannot_aggregate_to_green():
    """Unit contract: structured WEAK financial capacity is an amber signal."""
    report = ARKDDReport(target={"name": "Acme Ltd", "type": "company"})
    report.identity.entity_name = "Acme Ltd"
    report.identity.entity_type = "company"
    report.compliance.financial_health = {
        "data_available": True,
        "health_verdict": "WEAK",
    }

    asyncio.run(ddo._run_synthesis(report.target, report))

    assert report.risk_classification == "AMBER-LIGHT"


def test_rf3084_followup_persists_materiality_and_filtered_review_items():
    """CAPABILITY: drive the verdict merge used before the persisted PDF is rebuilt."""
    body = {
        "risk_classification": "GREEN",
        "identity": {"entity_name": "Measure Group Europe Limited"},
        "synthesis": {"key_findings": []},
    }
    adverse = {
        "ok": True,
        "status": "completed",
        "templates_searched": 12,
        "search_backends_answered": True,
        "findings": _raw_measure_group_hits(),
    }

    result = ddo._apply_adverse_media_to_verdict(body, adverse)

    assert result["credible_count"] == 0
    assert adverse["materiality"]["raw_count"] == 26
    assert adverse["materiality"]["credible_count"] == 0
    assert adverse["findings_for_review"] == []

    summary = _adverse_media_summary(adverse)
    assert "26 item(s) require review" not in summary["headline"]
    assert "nothing found in the sources searched" in summary["headline"]
    rendered = " ".join(_render_adverse_media(adverse))
    assert "Subject-named items returned: 26" not in rendered
    assert "Raw search results returned: 26" in rendered
    assert "0 item(s) require human review after filtering" in rendered


def test_rf3084_unfiltered_legacy_hits_are_never_called_subject_named():
    """Historic blobs without materiality must fail closed on attribution."""
    adverse = {
        "ok": True,
        "status": "completed",
        "findings": _raw_measure_group_hits(),
    }

    summary = _adverse_media_summary(adverse)
    rendered = " ".join(_render_adverse_media(adverse))

    assert summary["severity"] == "unknown"
    assert "RAW SEARCH RESULTS REQUIRE FILTERING" in summary["headline"]
    assert "subject attribution has not been verified" in summary["concern"]
    assert "Subject-named items returned" not in rendered
    assert "Raw, unfiltered search results returned: 26" in rendered
