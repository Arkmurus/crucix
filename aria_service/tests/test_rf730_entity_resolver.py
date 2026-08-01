"""R-F730 (2026-05-20) — regression tests for entity resolution pre-flight.

Pre-R-F730: `_detect_tool_intent` extracted a bare entity string and
passed it straight to tool dispatch. "Artur Group Angola" → web_search
→ "Arturo's Restaurant" hits. No disambiguation, no alias check.

R-F730 adds `entity_resolver.resolve(query)` that returns:
  - entity_type (person | company | unknown)
  - canonical name
  - aliases (transliterated + short-form variants via person_resolver)
  - prior_facts (knowledge.search_knowledge hits)
  - prior_signals (intel_ledger substring matches, capped)
  - confidence (0.0–1.0)

…and `render_context_block(resolved)` formats this into a
[ENTITY HINTS] prepended-context block. Both chat paths now call
resolve() before the LLM (per §13 stream-bypass rule).

Tests:
  - classification heuristics (person/company/unknown)
  - resolve() returns the expected shape
  - fail-soft on missing modules / empty input
  - render_context_block produces non-empty output when there's signal
  - wiring present in both chat paths
"""
from __future__ import annotations

import asyncio
import inspect

# R-F3597 — resolve source BY NAME through the current AST. `inspect.getsource`
# slices the file at the IMPORTED line numbers, so a concurrent edit during a
# long run returns a DIFFERENT function's body (measured 2026-07-31).
from ._source_probe import function_source


def test_classify_person_with_title():
    from aria_service.intel import entity_resolver as er

    assert er._classify_entity_type("Mr Sergei Petrov") == "person"
    assert er._classify_entity_type("Dr. Anna Schmidt") == "person"
    assert er._classify_entity_type("Gen Alex Smith") == "person"


def test_classify_company_with_suffix():
    from aria_service.intel import entity_resolver as er

    assert er._classify_entity_type("Acme Corp") == "company"
    assert er._classify_entity_type("Smith Industries Ltd") == "company"
    assert er._classify_entity_type("Beijing Aero Holdings") == "company"
    assert er._classify_entity_type("Maersk Group") == "company"


def test_classify_person_by_name_tokens():
    """Two+ capitalised tokens with no org suffix → person."""
    from aria_service.intel import entity_resolver as er

    assert er._classify_entity_type("Sergei Petrov") == "person"
    assert er._classify_entity_type("Anna Schmidt Jones") == "person"


def test_classify_unknown_for_free_text():
    from aria_service.intel import entity_resolver as er

    assert er._classify_entity_type("") == "unknown"
    assert er._classify_entity_type("what is going on") == "unknown"
    assert er._classify_entity_type("a") == "unknown"


def test_resolve_returns_expected_shape_for_empty_input():
    """Empty / very-short input returns a stub with entity_type='unknown'."""
    from aria_service.intel import entity_resolver as er

    out = asyncio.run(er.resolve(""))
    assert isinstance(out, dict)
    assert out["entity_type"] == "unknown"
    assert out["confidence"] == 0.0
    assert out["aliases"] == []
    assert out["prior_facts"] == []
    assert out["prior_signals"] == []


def test_resolve_returns_expected_shape_for_person():
    """A clear person name returns canonical + aliases (best-effort)."""
    from aria_service.intel import entity_resolver as er

    out = asyncio.run(er.resolve("Sergei Petrov"))
    assert isinstance(out, dict)
    assert out["entity_type"] == "person"
    assert out["query"] == "Sergei Petrov"
    # canonical may equal query or be a normalised form
    assert isinstance(out["canonical"], str) and len(out["canonical"]) > 0
    assert isinstance(out["aliases"], list)


def test_resolve_returns_expected_shape_for_company():
    """Company classification works; prior_facts/signals may be empty
    in test env (no preloaded data)."""
    from aria_service.intel import entity_resolver as er

    out = asyncio.run(er.resolve("Acme Corp Ltd"))
    assert out["entity_type"] == "company"
    assert isinstance(out["prior_facts"], list)
    assert isinstance(out["prior_signals"], list)


def test_resolve_caps_query_at_300_chars():
    """Defensive: a giant paste must not blow up downstream."""
    from aria_service.intel import entity_resolver as er

    big = "x" * 1000
    out = asyncio.run(er.resolve(big))
    assert len(out["query"]) <= 300


def test_render_context_block_empty_for_no_signal():
    """No prior facts / aliases / signals → empty string (no-op)."""
    from aria_service.intel import entity_resolver as er

    block = er.render_context_block({
        "query": "Acme Corp",
        "canonical": "Acme Corp",
        "aliases": [],
        "prior_facts": [],
        "prior_signals": [],
    })
    assert block == ""


def test_render_context_block_includes_aliases():
    """When aliases differ from the query, block surfaces them."""
    from aria_service.intel import entity_resolver as er

    block = er.render_context_block({
        "query": "Sergey Petrov",
        "canonical": "Sergei Petrov",
        "aliases": ["Sergei Petrov", "S. Petrov", "S.A. Petrov"],
        "prior_facts": [],
        "prior_signals": [],
    })
    assert "[ENTITY HINTS" in block
    assert "Sergei Petrov" in block
    assert "Aliases" in block


def test_render_context_block_includes_prior_facts():
    """Prior facts get surfaced as bullet lines."""
    from aria_service.intel import entity_resolver as er

    block = er.render_context_block({
        "query": "Acme Corp",
        "canonical": "Acme Corp",
        "aliases": [],
        "prior_facts": [{"summary": "Director changed 2024-03-15"}],
        "prior_signals": [],
    })
    assert "Prior facts" in block
    assert "Director changed" in block


def test_render_context_block_safe_on_garbage_input():
    """Defensive: None / wrong-shape inputs return empty."""
    from aria_service.intel import entity_resolver as er

    assert er.render_context_block(None) == ""
    assert er.render_context_block({}) == ""
    assert er.render_context_block("not a dict") == ""


def test_resolve_is_fail_soft_when_dependencies_missing(monkeypatch):
    """If knowledge / intel_ledger / person_resolver raise, resolve
    still returns a valid dict — never propagates the exception."""
    from aria_service.intel import entity_resolver as er

    def _boom(*args, **kwargs):
        raise RuntimeError("simulated")

    # Patch the SYNC fact fetcher (which runs in to_thread) and the
    # signal scan sync helper to raise. resolve() must still complete.
    monkeypatch.setattr(er, "_fetch_prior_facts_sync", _boom)
    monkeypatch.setattr(er, "_scan_signals_sync", _boom)

    out = asyncio.run(er.resolve("Acme Corp"))
    assert isinstance(out, dict)
    assert out["query"] == "Acme Corp"
    # On failure, prior_facts / prior_signals stay empty
    assert out["prior_facts"] == []
    assert out["prior_signals"] == []


# ── Wiring presence checks ──

def test_rf730_wiring_present_in_aria_chat_impl():
    """R-F730: entity resolver pre-flight must be called inside
    `_aria_chat_impl`. Catches accidental revert."""
    from aria_service import aria_engine

    src = function_source(aria_engine, "_aria_chat_impl")
    assert "R-F730" in src
    assert "entity_resolver" in src


def test_rf730_wiring_present_in_aria_chat_stream_impl():
    """R-F730: stream-path mirror per CLAUDE.md §13 stream-bypass rule."""
    from aria_service import aria_engine

    src = function_source(aria_engine, "_aria_chat_stream_impl")
    assert "R-F730" in src
    assert "entity_resolver" in src
