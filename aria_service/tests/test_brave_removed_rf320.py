"""R-F320 — Brave permanently removed.

Operator directive 2026-05-11: "please remove brave then... lets focus
reducing dependency". Capability tests proving Brave is structurally
gone from the runtime path:

  1. _search_brave returns [] regardless of inputs
  2. brave_answers.ask returns the removed sentinel
  3. brave_answers.is_enabled() == False
  4. brave_answers.get_month_spend() reports $0 + removed=True
  5. get_search_health() does NOT include a 'brave' key
"""
from __future__ import annotations

import asyncio


def test_rf320_search_brave_returns_empty_regardless_of_inputs():
    from aria_service.intel import web_search
    result = asyncio.run(web_search._search_brave("any query", max_results=10))
    assert result == [], (
        f"R-F320 regression: _search_brave returned {result!r} — must "
        f"be empty list (permanent stub)."
    )


def test_rf320_search_brave_returns_empty_even_with_api_key_set(monkeypatch):
    """Even if BRAVE_SEARCH_API_KEY is set in the environment, the stub
    must NOT execute a real Brave call."""
    from aria_service.intel import web_search
    monkeypatch.setattr(web_search, "BRAVE_API_KEY", "fake-key-for-test")
    monkeypatch.setenv("ARIA_BRAVE_DISABLED", "0")  # explicitly try to re-enable
    result = asyncio.run(web_search._search_brave("modirum gespi"))
    assert result == [], (
        f"R-F320 regression: stub bypassed by setting key/env. Got {result!r}"
    )


def test_rf320_brave_answers_ask_returns_removed_sentinel():
    from aria_service.intel import brave_answers
    out = asyncio.run(brave_answers.ask("what is X?"))
    assert out.get("removed") is True
    assert out.get("ok") is False
    assert out.get("answer") is None
    assert "REMOVED" in (out.get("reason") or "")


def test_rf320_brave_answers_is_enabled_false():
    from aria_service.intel import brave_answers
    assert brave_answers.is_enabled() is False


def test_rf320_brave_answers_month_spend_is_zero():
    from aria_service.intel import brave_answers
    out = asyncio.run(brave_answers.get_month_spend())
    assert out["spend_usd"] == 0.0
    assert out["call_count"] == 0
    assert out.get("removed") is True


def test_rf320_search_health_no_brave_key():
    from aria_service.intel import web_search
    out = asyncio.run(web_search.get_search_health())
    assert "brave" not in out, (
        f"R-F320 regression: get_search_health still reports brave key. "
        f"Got {out}"
    )
    # The free backends should still be present
    assert "google_news" in out
    assert "bing_news" in out


def test_rf320_capability_no_brave_http_call_attempted(monkeypatch):
    """Capability — patch httpx.AsyncClient with a spy that raises if
    called with the brave domain. _search_brave should NOT trigger any
    HTTP attempt at all."""
    from aria_service.intel import web_search
    import httpx as _hx

    original_client = _hx.AsyncClient

    class _SpyClient(original_client):
        async def get(self, url, *a, **kw):
            if "search.brave.com" in url:
                raise AssertionError(
                    f"R-F320 regression: brave HTTP call attempted to {url}"
                )
            return await super().get(url, *a, **kw)

    monkeypatch.setattr(_hx, "AsyncClient", _SpyClient)
    # Even with API key + opt-in, _search_brave is a stub now.
    monkeypatch.setattr(web_search, "BRAVE_API_KEY", "fake-key-for-test")
    monkeypatch.setenv("ARIA_BRAVE_DISABLED", "0")
    result = asyncio.run(web_search._search_brave("any"))
    assert result == []
