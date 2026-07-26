"""R-F3201 — authoritative feeds must yield useful, honest intelligence."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, patch

import pytest

from aria_service.intel import news_monitor as nm


@pytest.mark.parametrize(
    ("article", "expected_type"),
    [
        (
            {
                "title": "M 5.0 - 38 km SSE of Spearman, Texas",
                "summary": "PAGER - GREEN; ShakeMap - VI",
                "category": "crisis_early_warning",
                "topics": ["earthquake", "official", "primary", "early_warning"],
            },
            "natural_hazard",
        ),
        (
            {
                "title": "Hurricane Genevieve Public Advisory Number 10",
                "summary": "The hurricane is southwest of Mexico.",
                "category": "maritime_risk",
                "topics": ["maritime", "hurricane", "official", "primary", "early_warning"],
            },
            "natural_hazard",
        ),
        (
            {
                "title": "Migrant smuggling network dismantled across the Balkans",
                "summary": "The action was supported by Europol.",
                "category": "security",
                "topics": ["security", "organised_crime", "official", "primary"],
            },
            "security_operation",
        ),
        (
            {
                "title": "Europol-led action against nihilistic violent extremist network The Com",
                "summary": "An official counter-terrorism operation.",
                "category": "security",
                "topics": ["security", "terrorism", "official", "primary"],
            },
            "security_operation",
        ),
    ],
)
def test_rf3201_real_source_headlines_become_specific_signals(
    article: dict,
    expected_type: str,
) -> None:
    """Representative live payloads must survive the real relevance/classifier path."""
    relevance = nm._topical_relevance(article)
    signal = nm._build_intel_signal(article | {
        "source": "authoritative test source",
        "tier": "tier_1a",
        "url": "https://example.gov/evidence",
        "detected_at": "2026-07-26T22:00:00+00:00",
    })

    assert relevance["on_topic"] is True
    assert signal["signal_type"] == expected_type
    assert signal["priority"] == "HIGH"
    assert signal["classification_evidence"].startswith("matched '")
    assert signal["action_horizon"] == "0-72h"


def test_rf3201_low_impact_gdacs_notice_is_retained_but_not_promoted() -> None:
    """A GDACS Green notice is source evidence, not an actionable user alert."""
    article = {
        "title": (
            "Green earthquake (Magnitude 5.8M, Depth:10km) in United States, "
            "Few people affected in MMI III."
        ),
        "summary": "",
        "category": "crisis_early_warning",
        "topics": ["disaster", "official", "primary", "early_warning"],
    }

    relevance = nm._topical_relevance(article)

    assert relevance["on_topic"] is False
    assert relevance["reason"] == "low_impact_hazard"


def test_rf3201_hurricane_agency_name_does_not_invent_an_active_storm() -> None:
    """“Hurricane Center” is an institution, not evidence of an active cyclone."""
    article = {
        "title": "Atlantic Tropical Weather Outlook",
        "summary": (
            "The National Hurricane Center reports there are no tropical "
            "cyclones at this time."
        ),
        "category": "maritime_risk",
        "topics": ["maritime", "hurricane", "official", "primary", "early_warning"],
    }

    relevance = nm._topical_relevance(article)
    signal = nm._build_intel_signal(article | {
        "source": "NOAA NHC Atlantic",
        "tier": "tier_1a",
        "url": "https://www.nhc.noaa.gov/",
    })

    assert relevance["on_topic"] is False
    assert signal["signal_type"] == "situational_awareness"


@pytest.mark.parametrize(
    ("title", "summary", "expected_entity"),
    [
        (
            "2026-009: Critical Vulnerabilities in Microsoft SharePoint",
            "",
            "Microsoft SharePoint",
        ),
        (
            "Hurricane Genevieve Public Advisory Number 10",
            "The hurricane is southwest of Mexico.",
            "Hurricane Genevieve",
        ),
        (
            "M 5.0 - 38 km SSE of Spearman, Texas",
            "PAGER - GREEN; ShakeMap - VI",
            "Earthquake near 38 km SSE of Spearman, Texas",
        ),
    ],
)
def test_rf3201_official_sector_entities_earn_customer_grade(
    title: str,
    summary: str,
    expected_entity: str,
) -> None:
    """Real affected assets/events must satisfy the Grade A entity evidence gate."""
    signal = nm._build_intel_signal({
        "title": title,
        "summary": summary,
        "source": "official source",
        "url": "https://example.gov/evidence",
        "category": "crisis_early_warning",
        "tier": "tier_1a",
        "topics": ["official", "primary", "early_warning"],
    })

    entities = (signal["entities"].get("products") or []) + (
        signal["entities"].get("events") or []
    )
    assert expected_entity in entities
    assert signal["intel_grade"] == "A"
    assert signal["target"] in {expected_entity, "Mexico"}


def test_rf3201_ncsc_reports_are_not_mislabeled_as_live_early_warning() -> None:
    """NCSC's report archive is authoritative strategy, not a current-alert feed."""
    source = next(source for source in nm.NEWS_SOURCES if source[0] == "UK NCSC Reports")

    assert "early_warning" not in source[5]
    assert "strategic_assessment" in source[5]


@pytest.mark.asyncio
async def test_rf3201_classifier_replay_promotes_once_and_records_completion() -> None:
    """Existing raw evidence must gain corrected output without hourly duplication."""
    articles = [
        {
            "title": "Hurricane Genevieve Public Advisory Number 10",
            "summary": "The hurricane is southwest of Mexico.",
            "source": "NOAA NHC Eastern Pacific",
            "url": "https://www.nhc.noaa.gov/genevieve",
            "category": "maritime_risk",
            "tier": "tier_1a",
            "topics": ["maritime", "hurricane", "official", "primary", "early_warning"],
        },
        {
            "title": "Green earthquake in United States, few people affected",
            "summary": "",
            "source": "GDACS Disaster Alerts",
            "url": "https://www.gdacs.org/green-event",
            "category": "crisis_early_warning",
            "tier": "tier_1a",
            "topics": ["disaster", "official", "primary", "early_warning"],
        },
    ]
    writes: list[dict] = []

    with (
        patch.object(nm.rs, "get_json", AsyncMock(return_value=None)),
        patch.object(
            nm.rs,
            "lrange",
            AsyncMock(return_value=[json.dumps(article) for article in articles]),
        ),
        patch.object(nm.rs, "set_json", AsyncMock()) as set_marker,
        patch.object(nm, "_store_intel_signal", AsyncMock(side_effect=writes.append)),
    ):
        result = await nm._replay_recent_articles_for_classifier()

    assert result["status"] == "completed"
    assert result["scanned"] == 2
    assert result["promoted"] == 1
    assert writes[0]["signal_type"] == "natural_hazard"
    assert writes[0]["priority"] == "HIGH"
    assert set_marker.await_args.args[1]["version"] == nm._CLASSIFIER_REPLAY_VERSION

    with (
        patch.object(
            nm.rs,
            "get_json",
            AsyncMock(return_value={"version": nm._CLASSIFIER_REPLAY_VERSION}),
        ),
        patch.object(nm.rs, "lrange", AsyncMock()) as lrange,
    ):
        current = await nm._replay_recent_articles_for_classifier()

    assert current == {"status": "current", "scanned": 0, "promoted": 0}
    lrange.assert_not_awaited()
