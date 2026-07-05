"""R-F2426 — capability test: deep_research must run adverse-media / sanctions
angles for an adverse-media request so the evidence facet of the OSINT surface
is actually queried.

Root cause (retrieval coverage class, gate-#1 grounding lever):
  deep_research's base angles are ALL corporate-generic — [entity, "entity
  company", "entity headquarters location", "entity directors leadership",
  "entity news"]. For a query like "Wagner Group adverse media" the retrieval
  never issues the query that surfaces the evidence. PROVEN live (offline
  backend probe, 2026-07-05): "Wagner Group adverse media war crimes" returns
  Europol, US Treasury/OFAC and news sources; the corporate angles return
  corporate noise. The adverse/sanctions facet was structurally unqueried →
  thin context → 0.0 grounding.

These tests drive the REAL deep_research with a mocked search_multilingual (no
network) and assert that, when the adverse-angles flag is ON, the sanctions /
adverse-media angles are actually issued (and surface sources), while the flag
OFF leaves the angle set byte-for-byte unchanged.
"""
from __future__ import annotations

import asyncio

import pytest

from aria_service.intel import researcher as R
from aria_service.intel import web_search as ws


class _Hit:
    def __init__(self, url, title, snippet):
        self.url = url
        self.title = title
        self.snippet = snippet
        self.source = "duckduckgo"
        self.source_tier = "UNVERIFIED"


def _patch_common(monkeypatch):
    """Neutralise network legs (rag, extraction) so the test is deterministic."""
    async def _empty_search(*a, **k):
        return []

    # rag_store.search is called twice (brave-answer recall + memory expansion)
    import aria_service.intel.rag_store as rs
    monkeypatch.setattr(rs, "search", _empty_search, raising=False)

    async def _no_extract(url, timeout=10.0):
        return {"extraction_ok": False, "url": url}

    monkeypatch.setattr(R, "extract_url_text", _no_extract, raising=False)


def _run_and_capture(monkeypatch, entity, flag_on):
    called: list[str] = []

    async def fake_ml(angle, languages=None, max_results=8, translate_query=False):
        called.append(angle)
        # Give the adverse angles a hit so we can prove they surface sources.
        low = angle.lower()
        if any(t in low for t in ("sanctions", "adverse", "war crimes", "fraud", "investigation")):
            return [_Hit(f"https://treasury.gov/{len(called)}", f"OFAC hit: {angle}", "designation snippet")]
        return [_Hit(f"https://example.com/corp/{len(called)}", f"Corp: {angle}", "corporate snippet")]

    monkeypatch.setattr(ws, "search_multilingual", fake_ml)
    _patch_common(monkeypatch)
    if flag_on:
        monkeypatch.setenv("ARIA_DEEP_RESEARCH_ADVERSE_ANGLES", "1")
    else:
        monkeypatch.delenv("ARIA_DEEP_RESEARCH_ADVERSE_ANGLES", raising=False)

    r = asyncio.run(R.deep_research(entity, max_queries=6, max_extracts=2, overall_budget=20.0))
    return r, called


def test_adverse_angles_off_by_default(monkeypatch):
    r, called = _run_and_capture(monkeypatch, "Wagner Group adverse media", flag_on=False)
    assert r["ok"]
    joined = " ".join(r["queries_run"]).lower()
    # Default-safe: no injected sanctions/war-crimes angle.
    assert "war crimes" not in joined
    assert not any('sanctions ofac eu designation' in q.lower() for q in r["queries_run"])


def test_adverse_angles_added_and_surface_sources(monkeypatch):
    r, called = _run_and_capture(monkeypatch, "Wagner Group adverse media", flag_on=True)
    assert r["ok"]
    queries = r["queries_run"]
    joined = " ".join(queries).lower()
    # The sanctions + war-crimes discovery angles were issued...
    assert "sanctions ofac eu designation" in joined
    assert "war crimes" in joined
    # ...and, because the entity phrase signalled adverse intent, they were
    # PREPENDED so they survive the max_queries truncation.
    assert any("sanctions ofac eu designation" in q.lower() for q in queries[:3])
    # The cleaned subject ("Wagner Group") is used, not the raw adverse phrase.
    assert any('"wagner group"' in q.lower() for q in queries)
    # The adverse angles actually surfaced sources (grounding evidence).
    per_angle = r["snippet_count_per_angle"]
    adverse_hits = sum(
        v for q, v in per_angle.items()
        if any(t in q.lower() for t in ("sanctions", "adverse", "war crimes"))
    )
    assert adverse_hits > 0, "adverse angles returned zero snippets"


def test_benign_entity_appends_not_prepends(monkeypatch):
    # A plain company lookup with the flag on: corporate angles keep priority.
    r, called = _run_and_capture(monkeypatch, "QinetiQ Group plc", flag_on=True)
    assert r["ok"]
    queries = r["queries_run"]
    # Not signalled ⇒ adverse angles appended, so the first angle is still the
    # generic corporate discovery angle.
    assert queries[0].lower().startswith("qinetiq")
    assert not queries[0].lower().startswith('"qinetiq group plc" sanctions')


if __name__ == "__main__":
    import sys
    sys.exit(pytest.main([__file__, "-v"]))
