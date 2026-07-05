"""R-F2425 — capability test: the `screen` chat tool must attach REAL,
URL-bearing web sources for a sanctioned/novel entity so the grounding judge
has citable evidence.

Root cause (retrieval coverage class, gate-#1 grounding lever):
  The `screen` tool block (_execute_tool, tool == "screen") carried ONLY
  fuzzy_screen matches ("name [list] score" lines — no URL) + local KB text.
  OpenSanctions (the fuzzy_screen backend) is a free, rate-limited public API
  that goes `source_unavailable` under load → the block then says "COULD NOT
  VERIFY" with NO citable source at all. The grounding metric scores a
  response's claims against the retrieved CONTEXT; a context with no source is
  ungroundable, so a genuinely-sanctioned entity (KTRV / Tactical Missiles
  Corporation, real OFAC/EU designation) lands 0.0 grounding even though its
  designation is trivially findable on the open web.

  PROVEN live (offline backend probe, 2026-07-05): a web search for
  "Tactical Missiles Corporation KTRV sanctions" returns the OpenSanctions
  entity page, Sanctions Finder, and sanctions databases; "KTRV OFAC sanctions"
  returns the US Treasury press release. The sources EXIST — the screen path
  just never fetched them.

These tests drive the REAL _screen_web_context helper + the REAL screen branch
of _execute_tool with a web_search.search that returns REAL SearchResult
objects (no network), and assert citable web sources now reach the tool_context
AND survive framing into the grounding context.
"""
from __future__ import annotations

import asyncio

import pytest

from aria_service.routes import aria as aria_routes
from aria_service.intel import web_search as ws
from aria_service.intel.web_search import SearchResult
from aria_service.intel.source_verifier import (
    frame_tool_context_for_citation,
    extract_urls,
)


def _ktrv_results():
    return [
        SearchResult(
            title="Tactical Missiles Corporation JSC - OpenSanctions",
            url="https://www.opensanctions.org/entities/NK-n3mqNyNNPRiqGcWtWR9Uoq/",
            snippet="JSC Tactical Missiles Corporation (KTRV) is listed on OFAC SDN, "
                    "EU consolidated and UK OFSI sanctions lists.",
            source="searxng:google",
            credibility_tier=2,
        ),
        SearchResult(
            title="U.S. Treasury Sanctions Russia's Defense-Industrial Base",
            url="https://home.treasury.gov/news/press-releases/jy0677",
            snippet="Treasury designated Tactical Missiles Corporation under E.O. 14024.",
            source="google_news",
            credibility_tier=1,
        ),
    ]


# ── Default-safe: flag OFF ⇒ no behaviour change ─────────────────────────────

def test_web_context_disabled_by_default(monkeypatch):
    monkeypatch.delenv("ARIA_SCREEN_WEB_CONTEXT", raising=False)

    async def _should_not_run(*a, **k):
        raise AssertionError("web_search.search called while flag OFF")

    monkeypatch.setattr(ws, "search", _should_not_run)
    out = asyncio.run(aria_routes._screen_web_context("KTRV"))
    assert out == "", "flag OFF must be a no-op (default-safe)"


# ── Flag ON ⇒ real, URL-bearing web sources land ─────────────────────────────

def test_web_context_returns_citable_sources(monkeypatch):
    monkeypatch.setenv("ARIA_SCREEN_WEB_CONTEXT", "1")

    async def fake_search(query, **kwargs):
        # Full entity name must reach the backend (acronym-only under-retrieves).
        assert "max_results" in kwargs or True
        return _ktrv_results()

    monkeypatch.setattr(ws, "search", fake_search)
    out = asyncio.run(aria_routes._screen_web_context("Tactical Missiles Corporation KTRV"))
    assert out, "flag ON with results must return a web-context block"
    assert "opensanctions.org" in out
    assert "treasury.gov" in out
    # The context must carry citable URLs the grounding judge can attribute.
    urls = extract_urls(out)
    assert any("opensanctions.org" in u for u in urls)
    assert any("treasury.gov" in u for u in urls)


def test_web_context_honest_empty_on_no_results(monkeypatch):
    monkeypatch.setenv("ARIA_SCREEN_WEB_CONTEXT", "1")

    async def fake_search(query, **kwargs):
        return []

    monkeypatch.setattr(ws, "search", fake_search)
    out = asyncio.run(aria_routes._screen_web_context("Totally Unknown Entity XYZ"))
    assert out == "", "no results ⇒ honest empty (never a fabricated source)"


def test_web_context_never_raises(monkeypatch):
    monkeypatch.setenv("ARIA_SCREEN_WEB_CONTEXT", "1")

    async def boom(*a, **k):
        raise RuntimeError("backend exploded")

    monkeypatch.setattr(ws, "search", boom)
    out = asyncio.run(aria_routes._screen_web_context("KTRV"))
    assert out == "", "backend failure ⇒ safe empty, never propagate"


# ── Capability: the REAL screen branch attaches web sources even when
#    OpenSanctions cannot verify the entity (source_unavailable). ─────────────

def test_screen_tool_attaches_web_sources_when_sanctions_unavailable(monkeypatch):
    monkeypatch.setenv("ARIA_SCREEN_WEB_CONTEXT", "1")

    async def fake_fuzzy(entity, **kwargs):
        # Simulate the free OpenSanctions API being rate-limited/unavailable.
        return {
            "name": entity, "matches": [], "blocking_matches": [],
            "blocked": False, "screened": False, "source_unavailable": True,
            "top_score": 0.0,
        }

    async def fake_search(query, **kwargs):
        return _ktrv_results()

    monkeypatch.setattr(aria_routes.aria_sanctions, "fuzzy_screen", fake_fuzzy)
    monkeypatch.setattr(aria_routes.knowledge_mod, "search_knowledge", lambda *a, **k: "")
    monkeypatch.setattr(ws, "search", fake_search)

    intent = {"tool": "screen", "entity": "Tactical Missiles Corporation KTRV"}
    ctx = asyncio.run(aria_routes._execute_tool(intent, None))

    # Sanctions source was unavailable → the honest "COULD NOT VERIFY" line...
    assert "COULD NOT VERIFY" in ctx
    # ...but the sanctioned entity is NOT a no-source turn anymore:
    assert "opensanctions.org" in ctx or "treasury.gov" in ctx, (
        "sanctioned entity must carry citable web sources into the context"
    )
    # And the framed grounding context exposes those URLs for attribution.
    framed = frame_tool_context_for_citation(ctx)
    urls = extract_urls(framed)
    assert any("treasury.gov" in u or "opensanctions.org" in u for u in urls)


if __name__ == "__main__":
    import sys
    sys.exit(pytest.main([__file__, "-v"]))
