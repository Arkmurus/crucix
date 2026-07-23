"""R-F2947 — a DNS miss on a crawl target is NOT an engine_failure gap.

Live 2026-07-23: the autonomous crawl loop recorded ~70 `[engine_failure]
fetch_for_crawl` capability gaps in 6 minutes, ALL of them DNS misses on
speculative permuted-TLD domains that don't exist (`[Errno -2] Name or service
not known`). R-F2489's §21a wiring caught every `httpx.HTTPError` — and
`httpx.ConnectError` (DNS) is one — so each non-resolving domain flooded the
self-improve/review queue with un-fixable noise, drowning real gaps.

These capability tests drive the REAL fetch_for_crawl path and assert:
  1. a DNS name-not-known failure records NO engine_failure gap;
  2. it DISABLES the dead auto-registered (tier-4) domain so the loop stops
     hammering it every cycle;
  3. an operator/DD-curated (tier<4) domain is NOT auto-disabled;
  4. a genuine protocol HTTPError (not a dead target) STILL reaches the brain.
"""
from __future__ import annotations

import asyncio

import httpx
import pytest

from aria_service.crawler import fetcher
from aria_service.crawler import politeness


def _run(coro):
    return asyncio.run(coro)


class _RaisingClient:
    """Fake httpx.AsyncClient whose GET raises a chosen exception."""
    def __init__(self, exc):
        self._exc = exc

    def __call__(self, *a, **k):
        return self

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def get(self, url):
        raise self._exc


@pytest.fixture
def _wire(monkeypatch):
    """Capture every wire_failure call and stub the DB + politeness gates."""
    calls = {"wire_failure": [], "disabled": []}
    monkeypatch.setattr(fetcher, "wire_failure",
                        lambda **kw: calls["wire_failure"].append(kw))

    async def _is_allowed(url, user_agent="x"):
        return True
    monkeypatch.setattr(politeness, "is_allowed", _is_allowed)
    monkeypatch.setattr(politeness, "acquire", lambda domain: asyncio.sleep(0))

    async def _mark(domain, ts=None):
        return None
    monkeypatch.setattr(fetcher.db, "mark_domain_crawled", _mark)

    async def _disable(domain, reason=""):
        calls["disabled"].append((domain, reason))
        return True
    monkeypatch.setattr(fetcher.db, "disable_domain", _disable)
    return calls


def _install_domain(monkeypatch, tier):
    async def _get_domain(domain):
        return {"domain": domain, "enabled": True, "tier": tier}
    monkeypatch.setattr(fetcher.db, "get_domain", _get_domain)


def _install_client(monkeypatch, exc):
    monkeypatch.setattr(httpx, "AsyncClient", _RaisingClient(exc))


_NXDOMAIN = httpx.ConnectError("[Errno -2] Name or service not known")


def test_dns_miss_records_no_engine_gap(monkeypatch, _wire):
    _install_domain(monkeypatch, tier=4)
    _install_client(monkeypatch, _NXDOMAIN)
    out = _run(fetcher.fetch_for_crawl("https://blasketrenewables.com.ao/"))
    assert out["status_class"] == "dns_error", out
    assert _wire["wire_failure"] == [], (
        "a DNS miss must NOT be recorded as an engine_failure gap — that is the "
        "flood R-F2947 fixes")


def test_dns_miss_disables_dead_tier4_domain(monkeypatch, _wire):
    _install_domain(monkeypatch, tier=4)
    _install_client(monkeypatch, _NXDOMAIN)
    _run(fetcher.fetch_for_crawl("https://cisco-sd.pt/"))
    assert _wire["disabled"], "a confirmed NXDOMAIN tier-4 domain must be disabled"
    assert _wire["disabled"][0][0] == "cisco-sd.pt"


def test_curated_domain_is_not_auto_disabled(monkeypatch, _wire):
    """A tier<4 (operator/DD-curated) domain going NXDOMAIN is a real signal,
    not something to silently kill — do not disable it."""
    _install_domain(monkeypatch, tier=1)
    _install_client(monkeypatch, _NXDOMAIN)
    out = _run(fetcher.fetch_for_crawl("https://curated-source.example/"))
    assert out["status_class"] == "dns_error"
    assert _wire["disabled"] == [], "must not auto-disable a curated tier-1 source"


def test_transient_dns_failure_is_not_treated_as_nxdomain(monkeypatch, _wire):
    """errno -3 'Temporary failure in name resolution' can recover — do not
    disable, do not gap, just count it as an error."""
    _install_domain(monkeypatch, tier=4)
    _install_client(monkeypatch,
                    httpx.ConnectError("[Errno -3] Temporary failure in name resolution"))
    out = _run(fetcher.fetch_for_crawl("https://maybe-real.example/"))
    assert out["status_class"] == "error"
    assert _wire["disabled"] == [], "a transient resolver blip must not disable"
    assert _wire["wire_failure"] == [], "transient connect error is not an engine gap"


def test_genuine_protocol_error_still_reaches_brain(monkeypatch, _wire):
    """A non-connection HTTPError (e.g. redirect loop / protocol) is NOT a dead
    target — it stays wired to the brain as before (R-F2489 §21a preserved)."""
    _install_domain(monkeypatch, tier=4)
    _install_client(monkeypatch, httpx.TooManyRedirects("exceeded max redirects"))
    _run(fetcher.fetch_for_crawl("https://loopy.example/"))
    assert len(_wire["wire_failure"]) == 1, (
        "a genuine protocol failure must still record an engine_failure gap")
    assert _wire["wire_failure"][0]["gap_type"] == "engine_failure"


def test_is_dns_name_failure_classifier():
    assert fetcher._is_dns_name_failure("[Errno -2] Name or service not known")
    assert fetcher._is_dns_name_failure("nodename nor servname provided")
    assert not fetcher._is_dns_name_failure("[Errno -3] Temporary failure in name resolution")
    assert not fetcher._is_dns_name_failure("Connection refused")
