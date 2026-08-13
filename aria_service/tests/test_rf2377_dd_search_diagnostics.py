"""R-F2377 — DD search diagnostics and adverse-media result normalization."""
from __future__ import annotations

import asyncio

from aria_service.intel import researcher
from aria_service.intel import web_search
from aria_service.intel.web_search import SearchResult


def test_search_health_surfaces_brave_search_gate(monkeypatch):
    monkeypatch.setattr(web_search, "BRAVE_API_KEY", "test-key", raising=False)
    monkeypatch.setattr(web_search, "_BRAVE_GLOBALLY_OFF", False, raising=False)
    # R-F3946 — a DD-purpose scope is what "scope_enabled" now means.
    web_search.enable_brave_for_scope(True, purpose="dd")

    out = asyncio.run(web_search.get_search_health())

    brave = out.get("brave_search")
    assert isinstance(brave, dict)
    assert brave["configured"] is True
    assert brave["globally_disabled"] is False
    assert brave["scope_enabled"] is True
    assert brave["available_for_scoped_user_search"] is True

    web_search.enable_brave_for_scope(False)


def test_adverse_media_deep_search_accepts_searchresult_hits(monkeypatch):
    """Capability guard: the adverse-media executor must not discard DD search
    hits merely because the search layer returns SearchResult dataclasses."""

    def fake_templates(**kwargs):
        return [{
            "source_class": "regulatory_us_ofac",
            "query": '"Acme Defence" site:treasury.gov',
            "purpose": "OFAC enforcement actions / settlement / designation",
        }]

    async def fake_web_search(query, timeout=10.0, **_kw):  # R-F2832 kwarg
        return [SearchResult(
            title="Treasury designates Acme Defence",
            url="https://home.treasury.gov/acme-defence",
            snippet="Acme Defence was designated in an enforcement action.",
            source="aria_search",
            credibility_tier=1,
        )]

    import aria_service.intel.dd_disciplines as disciplines
    monkeypatch.setattr(disciplines, "adverse_media_query_templates", fake_templates)
    monkeypatch.setattr(researcher, "_web_search", fake_web_search)

    out = asyncio.run(researcher.run_adverse_media_deep_search(
        "Acme Defence",
        max_templates=1,
        max_results_per_template=1,
    ))

    assert out["ok"] is True
    assert out["findings_count"] == 1
    finding = out["findings"][0]
    assert finding["source_url"] == "https://home.treasury.gov/acme-defence"
    assert finding["credibility_tier"] == 1
    assert out["coverage_by_class"]["regulatory_us_ofac"] == 1
