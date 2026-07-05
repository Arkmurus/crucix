"""R-F2429 (Blocker 3) — capability test: when fuzzy_screen returns a match, the
screen tool must surface the OpenSanctions entity URL (already fetched this turn)
as the citable grounding source WITHOUT depending on a second, load-competing
web fetch.

Root cause (proven live, a99e smoke 2026-07-05):
  The R-F2425 screen web-leg fired a SEPARATE web search to find a citable
  source and timed out under load ("R-F2425 screen web-context timed out for
  'Sberbank'/'Kalashnikov Concern'") — even though fuzzy_screen had already
  POSTed to api.opensanctions.org/match/default → 200 OK and every match dict
  carries a real source URL (https://www.opensanctions.org/entities/<id>/). The
  redundant fetch competed for the event loop and timed out, so a listed entity
  landed 0.0 grounding despite the source being in hand.

Fix (`_screen_match_source_context`): build the citable block from the in-hand
matches (pay-once, §15); only fall back to the web fetch when NO match carried a
URL. §14 never-false-clean preserved — an unlisted entity yields honest empty,
never a fabricated source.

These tests drive the REAL screen branch of `_execute_tool`.
"""
from __future__ import annotations

import asyncio

import pytest

from aria_service.routes import aria as aria_routes
from aria_service.intel import web_search as ws
from aria_service.intel.source_verifier import (
    frame_tool_context_for_citation,
    extract_urls,
)


def _sberbank_screen_result(entity):
    """A realistic fuzzy_screen return with a listed match carrying its
    OpenSanctions entity URL (as sanctions._normalise_match builds it)."""
    return {
        "name": entity,
        "screened": True,
        "source_unavailable": False,
        "blocked": True,
        "top_score": 0.97,
        "matches": [
            {
                "name": "Sberbank of Russia",
                "list": "ru_nsd_isin",
                "lists": ["OFAC SDN", "EU Consolidated", "UK OFSI"],
                "score": 0.97,
                "topics": ["sanction", "role.pep"],
                "jurisdictions": [
                    {"code": "us", "label": "US OFAC SDN"},
                    {"code": "eu", "label": "EU Consolidated"},
                ],
                "url": "https://www.opensanctions.org/entities/NK-abc123/",
            },
        ],
    }


def _install(monkeypatch, screen_result, *, fail_web=True):
    monkeypatch.setenv("ARIA_SCREEN_WEB_CONTEXT", "1")

    async def fake_fuzzy(entity, **kwargs):
        return screen_result(entity) if callable(screen_result) else screen_result

    monkeypatch.setattr(aria_routes.aria_sanctions, "fuzzy_screen", fake_fuzzy)
    monkeypatch.setattr(aria_routes.knowledge_mod, "search_knowledge", lambda *a, **k: "")

    web_called = {"n": 0}

    async def maybe_web(query, **kwargs):
        web_called["n"] += 1
        if fail_web:
            raise AssertionError(
                "redundant web fetch was called even though the match carried a URL"
            )
        return []

    monkeypatch.setattr(ws, "search", maybe_web)
    return web_called


def test_match_url_surfaced_without_second_web_fetch(monkeypatch):
    web_called = _install(monkeypatch, _sberbank_screen_result, fail_web=True)

    intent = {"tool": "screen", "entity": "Sberbank"}
    ctx = asyncio.run(aria_routes._execute_tool(intent, None))

    # The in-hand OpenSanctions URL is now a citable grounding source.
    assert "opensanctions.org/entities/NK-abc123" in ctx
    # And it did NOT trigger the redundant, load-competing web fetch.
    assert web_called["n"] == 0, "web fetch fired despite an in-hand match URL"
    # The framed grounding context exposes the URL for attribution.
    framed = frame_tool_context_for_citation(ctx)
    assert any("opensanctions.org" in u for u in extract_urls(framed))


def test_unlisted_entity_falls_back_to_web_then_honest_empty(monkeypatch):
    # No match with a URL → fall back to the web leg; if that also yields
    # nothing, the block is an honest empty (no fabricated source, §14).
    empty_result = {
        "name": "Totally Unknown Entity XYZ",
        "screened": True,
        "source_unavailable": False,
        "blocked": False,
        "top_score": 0.0,
        "matches": [],
    }
    web_called = _install(monkeypatch, empty_result, fail_web=False)

    intent = {"tool": "screen", "entity": "Totally Unknown Entity XYZ"}
    ctx = asyncio.run(aria_routes._execute_tool(intent, None))

    # Fallback web leg WAS attempted (no in-hand URL to surface)...
    assert web_called["n"] >= 1
    # ...and with no web results either, NO fabricated source appears.
    assert "opensanctions.org/entities" not in ctx
    assert "SANCTIONS SOURCE CONTEXT" not in ctx


def test_source_unavailable_is_not_a_clearance_and_carries_no_url(monkeypatch):
    unavailable = {
        "name": "Kalashnikov Concern",
        "screened": False,
        "source_unavailable": True,
        "blocked": False,
        "top_score": 0.0,
        "matches": [],
    }
    web_called = _install(monkeypatch, unavailable, fail_web=False)

    intent = {"tool": "screen", "entity": "Kalashnikov Concern"}
    ctx = asyncio.run(aria_routes._execute_tool(intent, None))

    # Unavailable source must render UNVERIFIED, never a clean; the match-source
    # helper returns "" (no URL) and does not fabricate one.
    assert "COULD NOT VERIFY" in ctx
    assert "SANCTIONS SOURCE CONTEXT" not in ctx


def test_helper_gated_off_by_default(monkeypatch):
    # Flag OFF ⇒ no match-source block (default-safe, byte-for-byte unchanged).
    monkeypatch.delenv("ARIA_SCREEN_WEB_CONTEXT", raising=False)
    out = aria_routes._screen_match_source_context(_sberbank_screen_result("Sberbank"))
    assert out == ""


if __name__ == "__main__":
    import sys
    sys.exit(pytest.main([__file__, "-v"]))
