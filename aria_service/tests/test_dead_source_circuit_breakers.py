"""Regression guard for Batch B dead-source circuit breakers (F12-F16, F21, F22).

Live observation 2026-04-27 18:30:11-21: Brave returned 402 Payment
Required on every call (~9 wasted POSTs per research cycle); Semantic
Scholar returned 429 every call; archive.is returned 429 on every
academic.oup.com fallback. ARIA kept hammering all of them.

Fix: wrap each backend caller with the existing per-name circuit
breaker (intel/circuit_breaker.py) so after 3 consecutive failures we
stop attempting that backend until cooldown elapses. Also added a
known-paywall-domain skip list so academic.oup.com etc. don't even
fire the direct fetch.
"""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

import pytest

from aria_service.intel import circuit_breaker as cb_mod


@pytest.fixture(autouse=True)
def _fresh_breakers():
    """Each test gets a clean breaker registry — module-level state."""
    cb_mod._breakers.clear()
    yield
    cb_mod._breakers.clear()


# ── F22: known-paywall domain skip ─────────────────────────────────────────

def test_oup_url_skipped_without_fetch():
    """academic.oup.com URLs must not even hit the network."""
    from aria_service.intel.researcher import _is_known_paywall
    assert _is_known_paywall("https://academic.oup.com/book/39611/chapter/339546069")
    assert _is_known_paywall("https://academic.oup.com/milmed/article/191/3-4/e522/8248833")


def test_paywall_list_covers_known_offenders():
    """The skip list must cover the journal aggregators we keep losing
    requests to. If you add a domain, also update this assertion so it's
    proven via a test rather than a rubber-stamp."""
    from aria_service.intel.researcher import _KNOWN_PAYWALL_DOMAINS
    must_be_present = {
        "academic.oup.com",
        "www.sciencedirect.com",
        "onlinelibrary.wiley.com",
        "link.springer.com",
    }
    assert must_be_present.issubset(set(_KNOWN_PAYWALL_DOMAINS))


def test_non_paywall_url_not_skipped():
    from aria_service.intel.researcher import _is_known_paywall
    assert not _is_known_paywall("https://www.reuters.com/world/article")
    assert not _is_known_paywall("https://defenceweb.co.za/feed/")


# ── F15: Brave search circuit breaker ──────────────────────────────────────

def test_brave_breaker_opens_after_three_402s():
    """Live failure mode: Brave returns 402 on every call. After 3
    consecutive failures the breaker must open so subsequent calls
    don't even fire the HTTP request."""
    from aria_service.intel import web_search

    # Force a non-empty key so the early-return guard doesn't bypass.
    with patch.object(web_search, "BRAVE_API_KEY", "test-key"):
        # Build a fake httpx response object that returns 402.
        class FakeResp:
            status_code = 402
            text = "{}"
        class FakeClient:
            async def __aenter__(self): return self
            async def __aexit__(self, *a): pass
            async def get(self, *a, **k): return FakeResp()

        with patch("aria_service.intel.web_search.httpx.AsyncClient", return_value=FakeClient()):
            for _ in range(3):
                r = asyncio.run(web_search._search_brave("test query"))
                assert r == []

        # Breaker should now be open. The 4th call must short-circuit
        # WITHOUT instantiating an httpx client at all — easiest way to
        # prove that is to patch httpx to raise if used.
        with patch("aria_service.intel.web_search.httpx.AsyncClient",
                   side_effect=AssertionError("breaker should have skipped this call")):
            r = asyncio.run(web_search._search_brave("test query"))
            assert r == []


# ── F16: Semantic Scholar circuit breaker ──────────────────────────────────

def test_semantic_scholar_breaker_opens_after_three_429s():
    from aria_service.intel.sources import academic

    class FakeResp:
        status_code = 429
        text = "rate limited"
    class FakeClient:
        async def __aenter__(self): return self
        async def __aexit__(self, *a): pass
        async def get(self, *a, **k): return FakeResp()

    with patch("aria_service.intel.sources.academic.httpx.AsyncClient", return_value=FakeClient()):
        for _ in range(3):
            r = asyncio.run(academic.search_semantic_scholar("test"))
            assert r == []

    # 4th call must short-circuit
    with patch("aria_service.intel.sources.academic.httpx.AsyncClient",
               side_effect=AssertionError("breaker should have skipped this call")):
        r = asyncio.run(academic.search_semantic_scholar("test"))
        assert r == []


# ── F21: archive.is fallback breaker ───────────────────────────────────────

def test_archive_is_breaker_opens_after_three_429s():
    """Live failure mode 2026-04-27: every paywalled OUP DOI hit
    archive.is and got 429. The breaker must stop us pinging it."""
    from aria_service.intel import researcher

    class FakeResp:
        status_code = 429
        text = "rate limited"
    class FakeClient:
        async def __aenter__(self): return self
        async def __aexit__(self, *a): pass
        async def get(self, *a, **k): return FakeResp()

    # Trip the archive.is breaker. Wayback should still be tried after.
    with patch("aria_service.intel.researcher.httpx.AsyncClient", return_value=FakeClient()):
        for _ in range(3):
            r = asyncio.run(researcher._try_archive_fallbacks("https://example.com/blocked"))
            assert r == ""

    # archive.is breaker must now be open. Wayback breaker should also be
    # open because the same FakeResp returned 429 to it too. So 4th call
    # short-circuits both → returns "" with no httpx instantiation.
    with patch("aria_service.intel.researcher.httpx.AsyncClient",
               side_effect=AssertionError("both breakers should have skipped")):
        r = asyncio.run(researcher._try_archive_fallbacks("https://example.com/blocked2"))
        assert r == ""


# ── Generic: success closes a previously-open breaker ─────────────────────

def test_success_closes_open_breaker():
    """If the underlying problem clears (e.g. operator tops up Brave
    credit), one successful call must close the breaker so we resume."""
    cb = cb_mod.get_breaker("test_backend", failure_threshold=2, cooldown_seconds=1)
    cb.record_failure()
    cb.record_failure()
    assert cb.state == "OPEN"

    # Wait for cooldown
    import time
    time.sleep(1.1)
    assert cb.is_open() is False        # transitions to HALF_OPEN
    assert cb.state == "HALF_OPEN"

    cb.record_success()
    assert cb.state == "CLOSED"
    assert cb.consecutive_failures == 0
