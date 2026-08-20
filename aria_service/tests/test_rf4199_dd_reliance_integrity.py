"""R-F4199 — capability guards for the Vigilo DD reliability failures."""

import pytest

from aria_service.intel import network_walker
from aria_service.intel.dd_orchestrator import (
    _refresh_report_decision_readiness,
    _truncate_on_boundary,
    compose_decision_bluf,
)
from aria_service.intel.dd_schema import (
    ARKDDReport,
    _dd_decision_readiness,
    _dd_quality_assessment,
    _export_control_assessed,
)


def _completed_sanctions_screen() -> dict:
    return {
        "verified_sources": {"UK OFSI / HMT": {"status": "CLEAN"}},
        "screened": True,
    }


def test_vigilo_not_assessed_export_control_is_not_decision_ready():
    """The real symptom: sanctions coverage cannot answer a product assessment."""
    report = {
        "identity": {"sanctions_screen": _completed_sanctions_screen()},
        "compliance": {
            "sanctions_regimes": ["UK", "EU", "US", "UN"],
            "export_control": {
                "recommendation": "NOT ASSESSED — no product or transaction specified",
            },
        },
    }

    readiness = _dd_decision_readiness(report)

    assert _export_control_assessed(report["compliance"]["export_control"]) is False
    assert readiness["questions"]["sanctions_export_control"]["answered"] is False
    assert readiness["answered"] < readiness["required"]

    bluf = compose_decision_bluf(readiness, "Vigilo Solutions Limited")
    assert "Proceed with standard commercial process" not in bluf["next_actions"]
    assert any("product" in action.casefold() or "export" in action.casefold()
               for action in bluf["next_actions"])


def test_real_export_classification_still_answers_the_question():
    assert _export_control_assessed({"classification": "EAR99"}) is True
    assert _export_control_assessed({"findings": [{"code": "ECCN 5A002"}]}) is True


def test_vigilo_strength_metrics_cannot_receive_grade_a():
    """A 30%-grounded, 20%-corroborated report with seven open claims is not A."""
    report = {
        "identity": {
            "registration_status": "active",
            "incorporation_date": "2020-01-01",
            "sanctions_screen": _completed_sanctions_screen(),
        },
        "compliance": {"export_control": {"classification": "EAR99"}},
        "digital": {
            "press_coverage": [{"url": f"https://example.test/{n}"} for n in range(8)],
            "source_tier_breakdown": {"T1": 5, "T3": 3},
        },
        "adverse_media": {
            "ok": True,
            "templates_searched": 30,
            "search_backends_answered": True,
        },
        "verification": {
            "grounded_rate": 0.30,
            "unverified_claim_count": 7,
            "independent_corroboration_rate": 0.20,
            "triangulated_claims": [{"claim": str(n)} for n in range(10)],
            "citations_checked": 8,
            "citations_grounded": 8,
            "citation_grounding_rate": 1.0,
        },
    }

    quality = _dd_quality_assessment(report)

    assert quality["grade"] != "A"
    reasons = " ".join(quality["blocking_reasons"])
    assert "material-claim grounding below 80% (30%)" in reasons
    assert "7 material claim(s) remain unverified" in reasons
    assert "independent corroboration below 50% (20%)" in reasons


def test_ordered_but_undelivered_section_blocks_grade_a():
    report = {
        "data_gaps_summary": [
            "ORDERED SECTION NOT DELIVERED — IS-14: ORDERED BUT NOT SEARCHED"
        ],
    }
    quality = _dd_quality_assessment(report)
    assert quality["grade"] != "A"
    assert "an operator-ordered DD section was not delivered" in quality["blocking_reasons"]


def test_late_ordered_gap_refreshes_persisted_readiness_and_actions():
    report = ARKDDReport(target={"name": "Vigilo Solutions Limited", "type": "company"})
    report.identity.entity_name = "Vigilo Solutions Limited"
    report.risk_classification = "GREEN"
    report.decision_readiness = {"answered": 5, "required": 5, "evidence_grade": "A"}
    report.next_actions = ["Proceed with standard commercial process"]
    report.data_gaps_summary.append(
        "ORDERED SECTION NOT DELIVERED — IS-14: ORDERED BUT NOT SEARCHED"
    )

    readiness = _refresh_report_decision_readiness(report)

    assert readiness["evidence_grade"] != "A"
    assert report.decision_readiness == readiness
    assert "Proceed with standard commercial process" not in report.next_actions
    assert "NOT CLEARED" in report.bottom_line


def test_deep_research_finding_truncates_at_a_word_boundary():
    finding = (
        "The company is family controlled with a material ownership transition "
        "requiring independent verification before contracting"
    )
    rendered = _truncate_on_boundary(finding, 80)
    assert rendered.endswith(" …")
    assert "transi …" not in rendered
    assert len(rendered) <= 82


@pytest.mark.asyncio
async def test_ubo_name_only_pep_match_is_an_unverified_info_lead(monkeypatch):
    """Drive the real walker path that produced the two false Vigilo PEP findings."""
    async def officers(*_args, **_kwargs):
        return ([{"name": "BRIAN REID LTD.", "role": "corporate officer"}],
                "companies_house", "https://registry.test/officers")

    async def screen(_name):
        return {"matches": [{
            "name": "BRIAN BROCK REID",
            "score": 0.91,
            "topics": ["role.pep"],
            "match_field": "primary_name",
        }]}

    async def appointments(*_args, **_kwargs):
        return [], "companies_house", "https://registry.test/appointments"

    monkeypatch.setattr(network_walker, "_fetch_officers_with_provenance", officers)
    monkeypatch.setattr(network_walker, "_screen_name", screen)
    monkeypatch.setattr(network_walker, "_other_appointments_with_provenance", appointments)

    result = await network_walker.walk_ubo_chain("Vigilo Solutions Limited", max_hops=1)

    assert result["sanctioned_in_chain"] == []
    assert len(result["pep_in_chain"]) == 1
    assert result["pep_in_chain"][0]["severity"] == "info"
    assert result["pep_in_chain"][0]["identity_confirmed"] is False


@pytest.mark.asyncio
async def test_ubo_secondary_identity_match_keeps_pep_severity(monkeypatch):
    async def officers(*_args, **_kwargs):
        return ([{"name": "Jane Example", "role": "director"}],
                "companies_house", "https://registry.test/officers")

    async def screen(_name):
        return {"matches": [{
            "name": "Jane Example",
            "score": 1.0,
            "topics": ["role.pep"],
            "match_field": "date_of_birth",
        }]}

    monkeypatch.setattr(network_walker, "_fetch_officers_with_provenance", officers)
    monkeypatch.setattr(network_walker, "_screen_name", screen)

    result = await network_walker.walk_ubo_chain("Example Ltd", max_hops=1)

    assert result["pep_in_chain"][0]["severity"] == "amber"
    assert result["pep_in_chain"][0]["identity_confirmed"] is True
