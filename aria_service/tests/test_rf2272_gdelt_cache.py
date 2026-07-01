"""R-F2272 — GDELT per-query cache makes the rate-limited leg reliably contribute.

GDELT serves real news but rate-limits to 1/5s. A DD fires many queries; before this cache,
GDELT's articles were lost to the 5s throttle on every query after the first (returned []).
The cache serves a prior real hit under the throttle so the promised leg lands in results.
"""
from __future__ import annotations
import asyncio
from aria_service.intel import web_search as ws


class _R:
    status_code = 200
    def json(self):
        return {"articles": [{"title": f"Art {i}", "url": f"http://news/{i}",
                              "domain": "news.example", "seendate": ""} for i in range(10)]}


class _C:
    def __init__(self, *a, **k): pass
    async def __aenter__(self): return self
    async def __aexit__(self, *a): return False
    async def get(self, *a, **k): return _R()


def test_cache_serves_gdelt_under_the_5s_throttle(monkeypatch):
    ws._GDELT_CACHE.clear()
    ws._GDELT_LAST_CALL = 0.0
    monkeypatch.setattr(ws.httpx, "AsyncClient", _C)
    # 1st call: real fetch → 10 articles, cached
    r1 = asyncio.run(ws._search_gdelt("QinetiQ Group", 10))
    assert len(r1) == 10 and r1[0].source == "gdelt"
    # 2nd call immediately (well within 5s) — WITHOUT the cache this returned []
    r2 = asyncio.run(ws._search_gdelt("QinetiQ Group", 10))
    assert len(r2) == 10, "cache must serve GDELT under the throttle so the leg contributes"
    # control: a DIFFERENT, uncached query under the throttle still returns [] (throttle intact)
    r3 = asyncio.run(ws._search_gdelt("Some Other Entity", 10))
    assert r3 == [], "throttle still protects uncached queries from hammering GDELT"
