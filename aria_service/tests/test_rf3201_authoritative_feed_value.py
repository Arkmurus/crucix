"""R-F3201 — authoritative feeds must yield useful, honest intelligence."""

from __future__ import annotations

import json
import pathlib
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
    ("title", "summary", "expected_entity", "expected_grade"),
    [
        # A named PRODUCT is a portfolio nexus -> decision-grade.
        (
            "2026-009: Critical Vulnerabilities in Microsoft SharePoint",
            "",
            "Microsoft SharePoint",
            "A",
        ),
        (
            "2026-004: Critical Vulnerability in SharePoint Exploited",
            "",
            "SharePoint",
            "A",
        ),
        (
            "Critical Vulnerability in Cisco Secure Email and Web Manager "
            "On December 17",
            "",
            "Cisco Secure Email and Web Manager",
            "A",
        ),
        (
            "Critical Vulnerability in Windows Netlogon On 12 May 2026",
            "",
            "Windows Netlogon",
            "A",
        ),
        # R-F3812 — AMBIENT: the entity is extracted, but an event in a country is
        # not a portfolio nexus, so R-F3536 grades it B. See the note in the body.
        (
            "Hurricane Genevieve Public Advisory Number 10",
            "The hurricane is southwest of Mexico.",
            "Hurricane Genevieve",
            "B",
        ),
        (
            "M 5.0 - 38 km SSE of Spearman, Texas",
            "PAGER - GREEN; ShakeMap - VI",
            "Earthquake near 38 km SSE of Spearman, Texas",
            "B",
        ),
    ],
)
def test_rf3201_official_sector_entities_earn_customer_grade(
    title: str,
    summary: str,
    expected_entity: str,
    expected_grade: str,
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
    # R-F3201's actual subject — the affected asset/event must be EXTRACTED.
    assert expected_entity in entities
    assert signal["target"] in {expected_entity, "Mexico"}

    # R-F3812 — the grade was asserted as "A" for ALL six fixtures, which encoded the
    # pre-R-F3536 policy and had been red on the two ambient ones.
    #
    # R-F3536 introduced `_AMBIENT_SIGNAL_TYPES` ({"natural_hazard",
    # "political_transition"}): types that describe the WORLD rather than a
    # counterparty are "official and true, but not a decision on their own". The
    # hurricane and earthquake fixtures populate `countries` and `events` but no
    # `oems`/`products`/`facilities`, so `_has_specific_nexus` is False and they grade
    # B. The four CVE fixtures name a PRODUCT, which IS a portfolio nexus, so they
    # still earn A — the demotion is conditional, not a blanket rule about hazards.
    #
    # The expectation is therefore per-fixture rather than one blanket assertion; a
    # single value here is what let the superseded policy hide in four passing cases.
    assert signal["intel_grade"] == expected_grade
    if expected_grade == "B":
        assert "no portfolio nexus" in signal["grade_reason"]


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

    # R-F3494 — the properties asserted here are unchanged (an authoritative
    # article is promoted with the right signal_type/priority; the marker is
    # written; a completed version does not re-run). Only the SOURCE moved: the
    # replay now walks the permanent archive instead of the hot list, which was
    # destructively trimmed and capped the reachable history at 200 records.
    import tempfile
    from aria_service.intel import news_archive as _na
    with tempfile.TemporaryDirectory() as _tmp:
        _orig_db = _na._DB_PATH
        _na._DB_PATH = pathlib.Path(_tmp) / "news_archive.db"
        _na._reset_for_tests()
        try:
            for article in articles:
                await _na.archive_article(article)

            with (
                patch.object(nm.rs, "get_json", AsyncMock(return_value=None)),
                patch.object(nm.rs, "set_json", AsyncMock()) as set_marker,
                patch.object(nm, "_store_intel_signal",
                             AsyncMock(side_effect=writes.append)),
            ):
                result = await nm._replay_articles_for_classifier()

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
                    AsyncMock(return_value={
                        "version": nm._CLASSIFIER_REPLAY_VERSION,
                        "status": "completed",
                    }),
                ),
                patch.object(nm.rs, "set_json", AsyncMock()),
            ):
                current = await nm._replay_articles_for_classifier()

            assert current["status"] == "current"
            assert current["scanned"] == 0
        finally:
            _na._reset_for_tests()
            _na._DB_PATH = _orig_db


def test_rf3812_the_ambient_demotion_is_conditional_on_a_portfolio_nexus() -> None:
    """R-F3812 — the other half of R-F3536's rule, so the demotion above cannot be
    mistaken for "natural hazards can never be Grade A".

    An ambient signal is demoted only while nothing in the customer's portfolio is
    named. Add an OEM and the SAME signal type earns A. Without this, a future change
    that demoted every natural_hazard unconditionally would still pass.
    """
    kw = dict(source_tier="tier_1a", signal_type="natural_hazard", priority="HIGH",
              evidence_count=2, url="https://example.gov/evidence")

    grade, reason = nm._compute_intel_grade(
        **kw, entities={"countries": ["Mexico"], "events": ["Hurricane X"]})
    assert grade == "B" and "no portfolio nexus" in reason

    grade, reason = nm._compute_intel_grade(
        **kw, entities={"countries": ["Mexico"], "oems": ["Airbus"]})
    assert grade == "A", (
        "an ambient signal that names a portfolio asset IS decision-grade — "
        f"got {grade}: {reason}"
    )
