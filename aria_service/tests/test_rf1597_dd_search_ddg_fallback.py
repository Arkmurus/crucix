"""R-F1597 — DD search falls back to the working DDG web_search().

Operator 2026-06-15: a WhatsApp deep investigation on deltaguard.org returned
ZERO external search results ("Ни один поисковый запрос ... не вернул
результатов") even though a direct web_search() returns 5 DDG hits. Root cause
(verified): the DD path uses researcher._web_search → web_search.py's
multi-backend engine, whose backends are dead/gated here (_search_brave → [],
no key; _search_searxng → [], no instances). The WORKING, breaker-free DDG
search lives in researcher.web_search() but the DD path never called it.

R-F1597: when _web_search's primary path returns nothing, fall back to
web_search() (DDG) before the legacy Google-News-only path.
"""
from __future__ import annotations

import pytest

from aria_service.intel import researcher as R
from aria_service.intel import web_search as WS


@pytest.mark.asyncio
async def test_rf1597_ddg_fallback_fires_when_backends_empty(monkeypatch):
    # Primary multi-backend engine → empty (mimics dead Brave/SearXNG here).
    async def _empty_ml(*a, **k):
        return []
    monkeypatch.setattr(WS, "search_multilingual", _empty_ml)
    # Internal curated index → empty for a foreign company.
    async def _empty_idx(*a, **k):
        return []
    monkeypatch.setattr(R, "_query_internal_index", _empty_idx)
    # The working DDG web_search() → returns hits (as verified live).
    async def _ddg(query, max_results=20, timeout=10.0):
        return {
            "ok": True, "provider": "ddg",
            "results": [
                {"title": "Delta Guard licence dispute", "url": "https://news.bg/a", "snippet": "…"},
                {"title": "Делта Гард", "url": "https://capital.bg/b", "snippet": "…"},
            ],
        }
    monkeypatch.setattr(R, "web_search", _ddg)

    out = await R._web_search("Delta Guard Bulgaria security")

    assert out, "R-F1597: DD search returned nothing despite DDG fallback having results"
    links = [(r.get("link") or "") for r in out]
    assert any("news.bg/a" in l for l in links), f"DDG results not surfaced: {links}"
    assert all(r.get("source") for r in out), "source not populated on fallback results"


@pytest.mark.asyncio
async def test_rf1597_no_fallback_when_primary_has_results(monkeypatch):
    """Regression: when the primary path returns results, the DDG fallback
    must NOT be consulted (don't double-search / change normal behaviour)."""
    class _R:
        def __init__(self, u):
            self.title = "x"; self.url = u; self.snippet = "s"; self.source = "brave"
            self.credibility_tier = "T2"; self.relevance_score = 0.9; self.language = "en"
    async def _ml(*a, **k):
        return [_R("https://primary.example/x")]
    monkeypatch.setattr(WS, "search_multilingual", _ml)
    async def _empty_idx(*a, **k):
        return []
    monkeypatch.setattr(R, "_query_internal_index", _empty_idx)
    async def _boom(*a, **k):
        raise AssertionError("DDG fallback must NOT run when primary has results")
    monkeypatch.setattr(R, "web_search", _boom)

    out = await R._web_search("some query with primary hits")
    assert any("primary.example/x" in (r.get("link") or "") for r in out)
