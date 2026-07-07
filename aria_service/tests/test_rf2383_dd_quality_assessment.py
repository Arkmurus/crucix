"""R-F2383 — DD report quality assessment.

The DD surface needs an evidence-grade separate from the risk verdict. A report
should not read as Grade A just because it is GREEN if the evidence is thin,
memory-only, self-reported, ungrounded, or missing adverse-media depth.
"""
from __future__ import annotations

from aria_service.intel.dd_schema import structured_view


def test_rf2383_thin_green_report_is_not_grade_a():
    """Capability: the real structured_view contract caps a sparse GREEN report."""
    report = {
        "risk_classification": "GREEN",
        "confidence_tag": "ASSESSED",
        "digital": {
            "press_coverage": [
                {"url": "https://example.com/about", "source_tier": "ENTITY_SITE"},
                {"url": "https://memory.local/item", "source_tier": "MEMORY_ONLY"},
            ],
            "source_tier_breakdown": {"ENTITY_SITE": 1, "MEMORY_ONLY": 1},
            "data_gaps": [
                "R-F188: live web returned 0 - served from RAG memory only",
            ],
        },
        "verification": {
            "citations_checked": 0,
            "citations_grounded": 0,
        },
        "adverse_media": {
            "skipped": "overall_budget_exhausted",
        },
    }

    qa = structured_view(report)["quality_assessment"]

    assert qa["grade"] in {"C", "D"}, qa
    reasons = " ".join(qa["blocking_reasons"])
    assert "own-site" in reasons
    assert "memory-only" in reasons
    assert "citations" in reasons
    assert "adverse-media" in reasons


def test_rf2383_grade_a_requires_grounded_independent_depth():
    """A genuinely deep report earns Grade A: enough independent sources,
    grounded citations, and adverse-media search executed."""
    press = []
    tiers = {"T1": 3, "T2": 3, "T3": 3}
    for tier, count in tiers.items():
        for i in range(count):
            press.append({
                "url": f"https://source.example/{tier.lower()}/{i}",
                "source_tier": tier,
            })
    report = {
        "risk_classification": "GREEN",
        "confidence_tag": "ASSESSED",
        "digital": {
            "press_coverage": press,
            "source_tier_breakdown": tiers,
            "data_gaps": [],
        },
        "verification": {
            "citations_checked": 10,
            "citations_grounded": 9,
            "citation_grounding_rate": 0.9,
        },
        "adverse_media": {
            "ok": True,
            "findings_count": 2,
            "coverage_by_class": {"regulatory": 1, "news_archive": 1},
        },
    }

    qa = structured_view(report)["quality_assessment"]

    assert qa["grade"] == "A", qa
    assert qa["score"] >= 85
    assert qa["blocking_reasons"] == []
    assert qa["metrics"]["verified_sources"] == 6
    assert qa["metrics"]["quality_press"] == 3
