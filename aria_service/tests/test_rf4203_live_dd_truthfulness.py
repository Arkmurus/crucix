"""R-F4203 — regressions proved by live Vigilo/KGHW DD dd_9ff5e49aba24."""
from __future__ import annotations

import pytest

from aria_service.intel.dd_orchestrator import (
    _apply_adverse_media_to_verdict,
    _assemble_bluf,
    _invalidate_stale_report_render,
    _retained_research_findings,
)
from aria_service.intel.dd_schema import ARKDDReport, RiskClassification
from aria_service.intel.dd_versioning import resolve_version_chain


def test_running_row_cannot_become_its_own_previous_version():
    index = [
        {"run_id": "dd_current", "canonical_entity_id": "company:GB:14825146",
         "version_number": 1, "generated_at": "2026-08-20T13:42:19Z"},
        {"run_id": "dd_previous", "canonical_entity_id": "company:GB:14825146",
         "version_number": 4, "generated_at": "2026-08-19T13:42:19Z"},
    ]
    version, previous = resolve_version_chain(
        "company:GB:14825146", index, current_run_id="dd_current",
    )
    assert (version, previous) == (5, "dd_previous")


@pytest.mark.asyncio
async def test_confirmed_dissolved_identity_is_not_called_unconfirmed_registry():
    report = ARKDDReport(target={"name": "Vigilo Solutions Limited", "type": "company"})
    report.orchestrator_mode = "deep"
    report.identity.entity_name = "KGHW LTD"
    report.identity.registration_number = "14825146"
    report.identity.registration_status = "dissolved"
    report.identity.incorporation_date = "2023-04-25"
    report.identity.directors = [{"name": "GANDHI, Kailan"}]
    report.risk_classification = RiskClassification.AMBER_LIGHT.value
    report.confidence_gate_triggered = True
    report.confidence_gate_reasons = ["5 unresolved data gaps"]

    await _assemble_bluf(report)

    assert "could not be confirmed" not in report.bottom_line
    assert "LIMITED REGISTRY DATA" not in report.bottom_line
    identity_q = report.decision_readiness["questions"]["identity"]
    assert identity_q["blocker"] == "registry status is 'dissolved'"
    assert "Re-run the DD in DEEP mode" not in report.recommendation


def test_followup_invalidates_both_cached_markdown_surfaces():
    body = {
        "rendered": "stale top-level report",
        "synthesis": {"rendered_markdown": "stale synthesis report"},
    }
    _invalidate_stale_report_render(body, reason="adverse_media_followup")
    assert "rendered" not in body
    assert "rendered_markdown" not in body["synthesis"]
    assert body["rendered_invalidated_reason"] == "adverse_media_followup"


def test_adverse_payload_exposes_filtered_review_findings_separately():
    body = {
        "risk_classification": "AMBER-LIGHT",
        "identity": {"entity_name": "KGHW LTD"},
        "target": {"name": "KGHW LTD"},
        "synthesis": {"key_findings": []},
    }
    result = {
        "ok": True,
        "findings": [
            {"title": "brain_hook:web_search", "snippet": "KGHW LTD fraud",
             "source_url": "memory://self", "credibility_tier": 2},
            {"title": "KGHW LTD fraud investigation", "snippet": "KGHW LTD fraud",
             "source_url": "https://regulator.example/kghw", "credibility_tier": 1,
             "source_class_corroborated": True},
        ],
        "findings_count": 2,
    }
    _apply_adverse_media_to_verdict(body, result)
    assert result["findings_count"] == 2
    assert len(result["findings_for_review"]) == 1
    assert result["findings_for_review"][0]["source_url"].startswith("https://")
    assert all(not f["source_url"].startswith("memory://")
               for f in result["findings_for_review"])


def test_retained_research_requires_subject_attribution_when_identity_known():
    result = {"facts": [
        {"content": "KGH INVESTMENTS LTD files property accounts.",
         "source_url": "https://example.test/wrong", "confidence": "CONFIRMED"},
        {"content": "Companies House records KGHW LTD under company number 14825146.",
         "source_url": "https://example.test/right", "confidence": "CONFIRMED"},
    ]}
    findings = _retained_research_findings(
        result, subject_names=["KGHW LTD", "Vigilo Solutions Limited"],
        registration_number="14825146",
    )
    assert len(findings) == 1
    assert findings[0].source == "https://example.test/right"
    assert findings[0].confidence == "UNVERIFIED"
