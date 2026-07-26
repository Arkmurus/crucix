"""R-F3182 — authoritative cyber, security and hazard early-warning feeds."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from aria_service.intel import news_monitor as nm


EXPECTED_SOURCES = {
    "UK NCSC Reports": ("cyber_security", "tier_1a"),
    "CERT-EU Security Advisories": ("cyber_security", "tier_1a"),
    "Europol News": ("security", "tier_1a"),
    "GDACS Disaster Alerts": ("crisis_early_warning", "tier_1a"),
    "USGS Significant Earthquakes": ("crisis_early_warning", "tier_1a"),
    "NOAA NHC Atlantic": ("maritime_risk", "tier_1a"),
    "NOAA NHC Eastern Pacific": ("maritime_risk", "tier_1a"),
}


def test_rf3182_news_page_describes_real_coverage_without_phantom_count() -> None:
    """The customer page must describe the measured catalogue, not claim “80+”."""
    page = (
        Path(__file__).resolve().parents[2] / "public" / "news.html"
    ).read_text(encoding="utf-8")

    assert "80+" not in page
    assert "cyber, security, maritime-risk, and crisis early-warning" in page
    assert "stats.total_sources" in page


def test_rf3182_registers_only_verified_authoritative_sources() -> None:
    """Every researched source must be live-wired at its genuine authority tier."""
    registered = {
        name: (category, tier)
        for name, _url, category, _lang, tier, _topics in nm.NEWS_SOURCES
    }
    assert {name: registered.get(name) for name in EXPECTED_SOURCES} == EXPECTED_SOURCES


@pytest.mark.parametrize(
    ("title", "expected_type"),
    [
        (
            "Critical vulnerabilities in Microsoft SharePoint are being actively exploited",
            "cyber_threat",
        ),
        (
            "Piracy attack reported against merchant vessel in the Gulf of Aden",
            "maritime_security",
        ),
        (
            "Major earthquake triggers tsunami warning across Pacific ports",
            "natural_hazard",
        ),
        (
            "Atlantic hurricane forecast to intensify near major shipping lanes",
            "natural_hazard",
        ),
    ],
)
def test_rf3182_new_early_warning_sectors_are_actionable(
    title: str,
    expected_type: str,
) -> None:
    """Sector alerts must survive relevance gating and receive a specific type."""
    article = {
        "title": title,
        "summary": "",
        "category": "crisis_early_warning",
        "topics": ["official", "primary", "early_warning"],
    }
    relevance = nm._topical_relevance(article)
    signal_type, _why, _action, evidence = nm._classify_article_signal(
        title,
        article["category"],
        article["topics"],
    )
    assert relevance["on_topic"] is True
    assert signal_type == expected_type
    assert evidence.startswith("matched '")


@pytest.mark.asyncio
async def test_rf3182_real_poll_path_stores_authoritative_cyber_article() -> None:
    """The production poller must carry a new source into the News Monitor store."""
    source = (
        "UK NCSC Reports",
        "https://www.ncsc.gov.uk/api/1/services/v1/report-rss-feed.xml",
        "cyber_security",
        "en",
        "tier_1a",
        ["cyber", "official", "primary", "early_warning"],
    )
    rss = """<?xml version="1.0"?>
    <rss version="2.0"><channel><item>
      <title>Critical cyber threat report</title>
      <link>https://www.ncsc.gov.uk/report/example</link>
      <description>Official early warning for UK organisations.</description>
      <pubDate>Sun, 26 Jul 2026 12:00:00 GMT</pubDate>
    </item></channel></rss>"""
    stored: list[dict] = []

    async def capture(article: dict) -> None:
        stored.append(article.copy())

    from aria_service.intel import golden_intel_bridge

    with (
        patch.object(nm, "NEWS_SOURCES", [source]),
        patch.object(nm, "_get_vault_feed_sources", MagicMock(return_value=[])),
        patch.object(nm, "_fetch_feed", AsyncMock(return_value=rss)),
        patch.object(nm, "_is_seen", AsyncMock(return_value=False)),
        patch.object(nm, "_mark_seen", AsyncMock()),
        patch.object(nm, "_store_article", AsyncMock(side_effect=capture)),
        patch.object(nm, "_feed_to_brain", AsyncMock(return_value=True)),
        patch.object(nm, "_load_feed_health", AsyncMock(return_value={})),
        patch.object(nm, "_save_feed_health", AsyncMock()),
        patch.object(nm, "_read_poll_state", AsyncMock(return_value={})),
        patch.object(nm, "_write_poll_state", AsyncMock(return_value={"status": "fresh"})),
        patch.object(
            golden_intel_bridge,
            "run_promotion_pass",
            AsyncMock(return_value={"promoted": 1}),
        ),
    ):
        result = await nm.poll_feeds()

    assert result["articles_new"] == 1
    assert stored[0]["source"] == "UK NCSC Reports"
    assert stored[0]["category"] == "cyber_security"
    assert stored[0]["tier"] == "tier_1a"
