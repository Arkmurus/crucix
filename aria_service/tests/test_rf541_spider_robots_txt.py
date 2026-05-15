"""R-F541 — knowledge_spider must respect robots.txt.

Closes the 6-month-old R-F233-acknowledged gap. Pre-R-F541 the spider
declared `_REDIS_ROBOTS_CACHE_KEY` (2026-04-15) but no code read or
wrote it; the docstring claim of "respects robots.txt" was removed
in R-F233 (2026-05-11). R-F541 actually wires the check by routing
the spider's _fetch through `aria_service.crawler.politeness.is_allowed`
— the same function the main crawler/runner.py uses, with the same
24h parser cache.

Tests pin:
  1. _fetch source calls politeness.is_allowed BEFORE the HTTP GET
  2. When politeness returns False (Disallow), _fetch returns None
     and the HTTP GET is NOT made
  3. When politeness raises (network glitch on /robots.txt), _fetch
     fails open and proceeds (legal exposure < operational reliability
     trade-off — the crawler-side runner uses the same fail-open shape)
  4. When politeness returns True, _fetch makes the HTTP GET normally
"""
from __future__ import annotations

import asyncio
import inspect

import pytest


def test_rf541_spider_fetch_calls_politeness_is_allowed_source_check():
    """Source-text pin: knowledge_spider._fetch must import + call
    politeness.is_allowed. Catches the case where someone deletes the
    wire-in by accident in a future refactor."""
    from aria_service.learning import knowledge_spider
    src = inspect.getsource(knowledge_spider._fetch)
    assert "politeness" in src, (
        "knowledge_spider._fetch must reference politeness "
        "(R-F541 wired the robots.txt check)"
    )
    assert "is_allowed" in src, (
        "knowledge_spider._fetch must call politeness.is_allowed "
        "(R-F541) — found politeness reference but not the call"
    )
    assert "R-F541" in src, (
        "R-F541 tag should remain in source so future readers find "
        "the rationale"
    )


def test_rf541_robots_disallow_skips_fetch(monkeypatch):
    """When robots.txt Disallows the URL, _fetch must return None
    WITHOUT making the HTTP GET. Catches the case where the
    politeness wire-in fires but its result is ignored."""
    from aria_service.learning import knowledge_spider

    async def _fake_disallow(url, user_agent="ARIAsBot/1.0"):
        return False

    from aria_service.crawler import politeness
    monkeypatch.setattr(politeness, "is_allowed", _fake_disallow)

    # Track whether httpx.get is called
    get_calls: list[str] = []

    class _FakeClient:
        async def get(self, url, **kw):
            get_calls.append(url)
            raise AssertionError(
                "HTTP GET should NOT fire when robots.txt Disallows. "
                "R-F541 must short-circuit before the network call."
            )

    result = asyncio.run(knowledge_spider._fetch(
        "https://example.com/private/secrets",
        _FakeClient(),
    ))
    assert result is None
    assert get_calls == [], (
        f"R-F541 failed to short-circuit — HTTP GET was made: {get_calls}"
    )


def test_rf541_robots_check_failure_fails_open(monkeypatch):
    """When politeness.is_allowed itself RAISES (e.g. transient
    network failure on /robots.txt), _fetch must continue with the
    HTTP GET. Operational reliability > legal exposure on transient
    errors — same trade-off as crawler/runner.py."""
    from aria_service.learning import knowledge_spider

    async def _raises(url, user_agent="ARIAsBot/1.0"):
        raise ConnectionError("simulated robots.txt fetch failure")

    from aria_service.crawler import politeness
    monkeypatch.setattr(politeness, "is_allowed", _raises)

    # Also stub url_safety to be lenient so we get past the SSRF guard
    from aria_service.intel import url_safety
    monkeypatch.setattr(url_safety, "is_safe_url",
                        lambda url: (True, "ok"))

    get_calls: list[str] = []

    class _FakeOKClient:
        async def get(self, url, **kw):
            get_calls.append(url)
            class _R:
                status_code = 200
                headers = {"Content-Type": "text/html"}
                text = "<html><body>Hello</body></html>"
                content = b"<html><body>Hello</body></html>"
            return _R()

    asyncio.run(knowledge_spider._fetch(
        "https://example.com/public/page",
        _FakeOKClient(),
    ))
    assert len(get_calls) == 1, (
        f"R-F541 fail-open broke — expected GET to fire despite "
        f"politeness raising, got {get_calls}"
    )


def test_rf541_robots_allow_proceeds_normally(monkeypatch):
    """When robots.txt Allows, the spider continues with the GET as
    before."""
    from aria_service.learning import knowledge_spider

    async def _fake_allow(url, user_agent="ARIAsBot/1.0"):
        return True

    from aria_service.crawler import politeness
    monkeypatch.setattr(politeness, "is_allowed", _fake_allow)

    from aria_service.intel import url_safety
    monkeypatch.setattr(url_safety, "is_safe_url",
                        lambda url: (True, "ok"))

    get_calls: list[str] = []

    class _FakeOKClient:
        async def get(self, url, **kw):
            get_calls.append(url)
            class _R:
                status_code = 200
                headers = {"Content-Type": "text/html"}
                text = "<html><body><p>Hello world</p></body></html>"
                content = b"<html><body><p>Hello world</p></body></html>"
            return _R()

    asyncio.run(knowledge_spider._fetch(
        "https://example.com/public/page",
        _FakeOKClient(),
    ))
    assert len(get_calls) == 1, (
        "R-F541 must NOT block fetches when robots.txt Allows"
    )
