"""R-F3224 — News Monitor counters describe bounded storage and live poll intake."""

import asyncio
from unittest.mock import AsyncMock, patch

from aria_service.intel import news_monitor as nm


def test_stats_exposes_retention_contract_and_deduped_source_count():
    """The real stats function must not call a full rolling list a lifetime total."""
    articles = [
        {"source": "Source A", "category": "defence_global"},
        {"source": "Source B", "category": "technology"},
    ]
    sources = [
        ("Publisher", "https://example.com/rss", "defence_global", "en", "tier_2", []),
        ("Publisher", "https://example.com/feed", "technology", "en", "tier_2", []),
    ]

    nm._stats_cache = {}
    nm._stats_cache_ts = 0
    with (
        patch.object(nm, "NEWS_SOURCES", sources),
        patch.object(nm, "get_recent_articles", AsyncMock(return_value=articles)),
        patch.object(nm, "_read_poll_state", AsyncMock(return_value={"articles_new": 3})),
    ):
        stats = asyncio.run(nm.get_stats())

    assert stats["recent_articles"] == 2
    assert stats["retention_limit"] == nm._MAX_ARTICLES
    assert stats["total_sources"] == 1
    assert stats["poll_state"]["articles_new"] == 3


def test_poll_suppresses_same_publisher_feed_alias(monkeypatch):
    """Capability: the real poller fetches only one alias for one publisher."""
    sources = [
        ("Publisher", "https://example.com/rss", "defence_global", "en", "tier_2", []),
        ("Publisher", "https://example.com/feed", "technology", "en", "tier_2", []),
    ]
    fetched: list[str] = []

    async def fake_fetch(url, _name):
        fetched.append(url)
        return "<rss><channel></channel></rss>"

    monkeypatch.setattr(nm, "NEWS_SOURCES", sources)
    monkeypatch.setattr(nm, "_fetch_feed", fake_fetch)
    monkeypatch.setattr(nm, "_parse_rss", AsyncMock(return_value=[]))
    monkeypatch.setattr(nm, "_load_feed_health", AsyncMock(return_value={}))
    monkeypatch.setattr(nm, "_save_feed_health", AsyncMock())
    monkeypatch.setattr(nm, "_read_poll_state", AsyncMock(return_value={}))
    monkeypatch.setattr(nm, "_write_poll_state", AsyncMock(side_effect=lambda value: value))
    monkeypatch.setattr(nm, "_get_vault_feed_sources", lambda: [])
    monkeypatch.setattr(nm.asyncio, "sleep", AsyncMock())

    result = asyncio.run(nm.poll_feeds())

    assert fetched == ["https://example.com/rss"]
    assert result["feeds_polled"] == 1


def test_news_page_renders_new_items_separately_from_retained_count():
    """The user-visible KPI must explain the cap and show the latest intake."""
    page = open("public/news.html", encoding="utf-8").read()
    assert "Retained Articles (cap)" in page
    assert "poll.articles_new + ' new'" in page
    assert "Last poll " in page
