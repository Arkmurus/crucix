"""R-F2701 — GNews.io API is a real search backend, not a dormant vault entry."""
from __future__ import annotations

import asyncio
from types import SimpleNamespace


def _run(coro):
    return asyncio.run(coro)


def test_gnews_api_backend_uses_server_side_key(monkeypatch):
    from aria_service.intel import web_search

    captured = {}

    class FakeResponse:
        status_code = 200

        def json(self):
            return {
                "articles": [
                    {
                        "title": "Defence procurement update",
                        "url": "https://www.reuters.com/world/example",
                        "description": "A monitored defence procurement story.",
                        "publishedAt": "2026-07-17T12:00:00Z",
                    }
                ]
            }

    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def get(self, url, params=None, **kwargs):
            captured["url"] = url
            captured["params"] = dict(params or {})
            return FakeResponse()

    monkeypatch.setattr(web_search, "GNEWS_API_KEY", "test-key-123")
    monkeypatch.setattr(web_search.httpx, "AsyncClient", FakeClient)

    results = _run(web_search._search_gnews_api("defence procurement", max_results=5))

    assert captured["url"] == "https://gnews.io/api/v4/search"
    assert captured["params"]["apikey"] == "test-key-123"
    assert captured["params"]["q"] == "defence procurement"
    assert results
    assert results[0].source == "gnews_api"
    assert results[0].url == "https://www.reuters.com/world/example"


def test_search_news_fans_out_to_gnews_api(monkeypatch):
    from aria_service.intel import web_search

    calls = []

    async def fake_gnews(query, max_results=10, language="en"):
        calls.append(("gnews_api", query, language))
        return [web_search.SearchResult(
            title="GNews result",
            url="https://example.com/gnews",
            source="gnews_api",
        )]

    async def fake_google(query, max_results=10, language="en"):
        calls.append(("google_news", query, language))
        return []

    async def fake_bing(query, max_results=10):
        calls.append(("bing_news", query, ""))
        return []

    monkeypatch.setattr(web_search, "_search_gnews_api", fake_gnews)
    monkeypatch.setattr(web_search, "_search_google_news", fake_google)
    monkeypatch.setattr(web_search, "_search_bing_news", fake_bing)

    results = _run(web_search.search_news("export controls", language="en"))

    assert ("gnews_api", "export controls", "en") in calls
    assert ("google_news", "export controls", "en") in calls
    assert ("bing_news", "export controls", "") in calls
    assert results[0].source == "gnews_api"


def test_search_health_reports_gnews_configured(monkeypatch):
    from aria_service.intel import web_search

    monkeypatch.setattr(web_search, "GNEWS_API_KEY", "test-key-123")
    monkeypatch.setattr(
        web_search,
        "wire_success",
        lambda *args, **kwargs: None,
    )

    health = _run(web_search.get_search_health())

    assert health["gnews_api"] == {
        "configured": True,
        "available": True,
        "mode": "gnews.io_api_v4_search",
    }
