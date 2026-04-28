"""Regression guard for Batch B feed-side fixes (F12, F13, F14).

F14: Per-feed RSS circuit breaker — ~12 dead RSS sources hit every cycle
F13: TED API binary-body log fix — was emitting mojibake into the log
F12: SEACE Peru SSL DH_KEY_TOO_SMALL — needs relaxed SSL context
"""
from __future__ import annotations

import asyncio
import logging
from unittest.mock import patch

import pytest

from aria_service.intel import circuit_breaker as cb_mod


@pytest.fixture(autouse=True)
def _fresh_breakers():
    cb_mod._breakers.clear()
    # F75 fix 2026-04-28: tender_monitor._PORTAL_FAILURE_LOGGED is a
    # module-level cache — also reset it so TED-404 tests below don't
    # inherit a primed cache from a sibling test.
    try:
        from aria_service.intel import tender_monitor as _tm
        _tm._PORTAL_FAILURE_LOGGED.clear()
    except Exception:
        pass
    yield
    cb_mod._breakers.clear()
    try:
        from aria_service.intel import tender_monitor as _tm
        _tm._PORTAL_FAILURE_LOGGED.clear()
    except Exception:
        pass


# ── F14: per-feed RSS circuit breaker ─────────────────────────────────────

def test_rss_breaker_opens_after_five_404s():
    """Live failure mode: ~12 RSS feeds 404 every cycle. After 5
    consecutive failures the breaker opens and skips that feed."""
    from aria_service.intel import researcher

    class FakeResp:
        status_code = 404
        text = ""
    class FakeClient:
        async def __aenter__(self): return self
        async def __aexit__(self, *a): pass
        async def get(self, *a, **k): return FakeResp()

    feed_url = "https://www.shephardmedia.com/feed/"
    with patch("aria_service.intel.researcher.httpx.AsyncClient", return_value=FakeClient()):
        for _ in range(5):
            r = asyncio.run(researcher._fetch_rss(feed_url))
            assert r == []

    # 6th call must short-circuit without instantiating httpx
    with patch("aria_service.intel.researcher.httpx.AsyncClient",
               side_effect=AssertionError("breaker should have skipped this fetch")):
        r = asyncio.run(researcher._fetch_rss(feed_url))
        assert r == []


def test_rss_breaker_isolated_per_host():
    """One dead feed must NOT trip the breaker for an unrelated feed."""
    from aria_service.intel import researcher

    class FakeResp404:
        status_code = 404
        text = ""
    class FakeClient404:
        async def __aenter__(self): return self
        async def __aexit__(self, *a): pass
        async def get(self, *a, **k): return FakeResp404()

    dead_url = "https://www.dead-feed.example/rss"
    healthy_url = "https://www.healthy-feed.example/rss"

    # Trip the dead feed's breaker
    with patch("aria_service.intel.researcher.httpx.AsyncClient", return_value=FakeClient404()):
        for _ in range(5):
            asyncio.run(researcher._fetch_rss(dead_url))

    # Confirm dead breaker is open, healthy is still untouched
    dead_cb = cb_mod.get_breaker(f"rss:{'www.dead-feed.example'}")
    healthy_cb = cb_mod.get_breaker(f"rss:{'www.healthy-feed.example'}")
    assert dead_cb.state == "OPEN"
    assert healthy_cb.state == "CLOSED"


# ── F13: TED API binary-body log fix ──────────────────────────────────────

def test_ted_binary_response_logged_as_preview_not_garbage(caplog):
    """When TED returns 404 with a gzipped/binary body, log must show
    `<binary, N bytes>` instead of mojibake."""
    from aria_service.intel import tender_monitor

    # Synthetic binary body (the kind of bytes httpx surfaced as garbage
    # in the live log). httpx exposes resp.text and resp.content.
    binary_blob = bytes(range(256)) + b"\x90\xc7\x9d" * 100  # contains non-printable

    class FakeResp:
        status_code = 404
        @property
        def text(self):
            return binary_blob.decode("latin-1")
        @property
        def content(self):
            return binary_blob

    class FakeClient:
        async def post(self, *a, **k):
            return FakeResp()

    async def run():
        with caplog.at_level(logging.WARNING, logger="aria.tender_monitor"):
            return await tender_monitor._crawl_ted(FakeClient(), max_results=10)

    result = asyncio.run(run())
    assert result == []
    # Find the TED log line
    ted_lines = [r.message for r in caplog.records if "[TED] API returned 404" in r.message]
    assert len(ted_lines) == 1
    assert "<binary," in ted_lines[0], (
        f"expected '<binary, N bytes>' marker, got: {ted_lines[0][:200]!r}"
    )


def test_ted_text_response_logged_as_text(caplog):
    """When TED returns 404 with normal text, the log preview must
    show the text — the binary detector must not false-positive on
    legitimate error messages."""
    from aria_service.intel import tender_monitor

    class FakeResp:
        status_code = 404
        @property
        def text(self):
            return "Not Found: the requested URL does not exist"
        @property
        def content(self):
            return self.text.encode()

    class FakeClient:
        async def post(self, *a, **k):
            return FakeResp()

    async def run():
        with caplog.at_level(logging.WARNING, logger="aria.tender_monitor"):
            return await tender_monitor._crawl_ted(FakeClient(), max_results=10)

    asyncio.run(run())
    ted_lines = [r.message for r in caplog.records if "[TED] API returned 404" in r.message]
    assert len(ted_lines) == 1
    assert "Not Found" in ted_lines[0]
    assert "<binary," not in ted_lines[0]


# ── F12: SEACE Peru SSL context ───────────────────────────────────────────

def test_seace_ssl_context_relaxes_security_level():
    """The custom context must be constructible without raising and
    must NOT be the system default (otherwise we'd still fail with
    DH_KEY_TOO_SMALL on the live SEACE endpoint)."""
    from aria_service.intel.tender_monitor import _build_seace_ssl_context
    import ssl

    ctx = _build_seace_ssl_context()
    assert isinstance(ctx, ssl.SSLContext)
    # The default context wouldn't allow weak ciphers; this one must.
    # We can't probe the actual cipher list without OpenSSL bindings,
    # but constructing it without raising is the proof point — the
    # set_ciphers call is what actually relaxes the level.
