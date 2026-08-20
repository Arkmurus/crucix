"""R-F4175 — sanctions screen contradictions compare structured source ids."""
from __future__ import annotations

import pytest

from aria_service.intel import _sanctions_classify as sanctions_classify
from aria_service.intel import dd_orchestrator, sanctions
from aria_service.intel.dd_schema import ARKDDReport, Finding


def _trade_match() -> dict:
    return {
        "name": "Black Shield Company for General Trading LLC",
        "score": 0.85,
        "string_similarity": 0.8,
        "topics": ["sanction", "debarment"],
        "lists": ["us_trade"],
    }


def test_rf4175_classifier_carries_the_source_id_that_drove_the_block() -> None:
    """The severity decision and contradiction detector share one identity."""
    classified = sanctions_classify.classify_matches(
        [_trade_match()],
        query_name="Black Shield Trading LLC",
    )
    assert classified["worst_severity"] == "hard_stop"
    assert classified["blocking_source_ids"] == ["US Trade CSL"]
    assert classified["per_match"][0]["canonical_source_ids"] == ["US Trade CSL"]


def test_rf4175_structured_ids_are_authoritative_over_prose() -> None:
    """A prose coincidence cannot override an explicit, different source id."""
    finding = Finding(
        severity="hard_stop",
        title="Blocking designation",
        detail="The narrative happens to mention OFAC SDN.",
        source="sanctions.screen_with_aliases",
        sanctions_source_ids=["US Trade CSL"],
    )
    contradictions = sanctions_classify.detect_screen_contradictions(
        [finding],
        {
            "OFAC SDN": {"status": "CLEAN"},
            "US Trade CSL": {"status": "HIT"},
        },
    )
    assert contradictions == []


@pytest.mark.asyncio
async def test_rf4175_registered_name_rescreen_surfaces_the_real_contradiction(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Drive the DD re-screen path that previously shipped HARD_STOP + CLEAN."""
    async def clean_rescreen(*_args, **_kwargs) -> dict:
        return {
            "screened": True,
            "matches": [],
            "aliases_checked": ["Black Rose Security Limited"],
        }

    monkeypatch.setattr(sanctions, "screen_with_aliases", clean_rescreen)
    report = ARKDDReport(target={"name": "Black Rose Security"})
    report.identity.findings.append(Finding(
        severity="hard_stop",
        title="Subject on active sanctions list",
        detail="Black Shield Company matched the US trade screening collection.",
        source="sanctions.screen_with_aliases",
        confidence="CONFIRMED",
        sanctions_source_ids=["US Trade CSL"],
    ))

    stopped = await dd_orchestrator._rescreen_under_registered_name(
        report,
        "Black Rose Security Limited",
        "Black Rose Security",
    )

    assert stopped is False
    alerts = [
        finding
        for finding in report.identity.findings
        if finding.source == "sanctions.screen_contradiction"
    ]
    assert len(alerts) == 1
    assert "us trade csl" in alerts[0].detail.casefold()
    assert any("us trade csl" in gap.casefold() for gap in report.identity.data_gaps)
