"""R-F2257 — GDELT global-news backend (free API, datacenter-tolerant).

The robustness pivot: an API source that SERVES a datacenter IP (429 rate-limit, not a
CAPTCHA block) instead of the scrapers that collapse from datacenter egress. Wired into
the RRF backend fusion. Mock-based functional test (no live API) proves parsing +
throttle + graceful-empty; source-contract proves it's in the fused backend list.
"""
from __future__ import annotations
import asyncio
from pathlib import Path

import aria_service.intel.web_search as ws


class _Resp:
    def __init__(self, status, payload=None, text=""):
        self.status_code = status; self._p = payload; self.text = text
    def json(self):
        if self._p is None: raise ValueError("not json")
        return self._p


class _Client:
    _resp = None
    def __init__(self, *a, **k): pass
    async def __aenter__(self): return self
    async def __aexit__(self, *a): return False
    async def get(self, *a, **k): return _Client._resp


def _run(coro): return asyncio.run(coro)


def test_gdelt_parses_articles_into_searchresults(monkeypatch):
    _Client._resp = _Resp(200, {"articles": [
        {"title": "QinetiQ wins defence contract", "url": "https://www.defensenews.com/x",
         "domain": "defensenews.com", "seendate": "20260701T000000Z"},
        {"title": "No url here", "url": "", "domain": "x.com"},  # dropped (no url)
    ]})
    monkeypatch.setattr(ws.httpx, "AsyncClient", _Client)
    ws._GDELT_LAST_CALL = 0.0
    r = _run(ws._search_gdelt("QinetiQ Group", 8))
    assert len(r) == 1                      # the url-less article is dropped
    assert r[0].source == "gdelt"
    assert "defensenews.com" in r[0].url


def test_gdelt_429_is_graceful_not_a_crash(monkeypatch):
    _Client._resp = _Resp(429, None, text="Please limit requests to one every 5 seconds")
    monkeypatch.setattr(ws.httpx, "AsyncClient", _Client)
    ws._GDELT_LAST_CALL = 0.0
    assert _run(ws._search_gdelt("Modirum Gespi", 8)) == []  # rate-limit → [], no exception


def test_gdelt_throttle_skips_rapid_second_call(monkeypatch):
    _Client._resp = _Resp(200, {"articles": [{"title": "t", "url": "https://a.com", "domain": "a.com"}]})
    monkeypatch.setattr(ws.httpx, "AsyncClient", _Client)
    ws._GDELT_LAST_CALL = 0.0
    first = _run(ws._search_gdelt("Some Entity Ltd", 8))
    second = _run(ws._search_gdelt("Some Entity Ltd", 8))  # <5s later → throttled
    assert len(first) == 1 and second == []


def test_gdelt_is_in_the_fused_backend_list():
    src = (Path(__file__).resolve().parent.parent / "intel" / "web_search.py").read_text(encoding="utf-8")
    assert "_search_gdelt(query, MAX_RESULTS_PER_BACKEND)" in src  # wired into backend_tasks
    assert 'get_breaker as _get_cb_g' in src and 'search:gdelt' in src  # circuit-broken
