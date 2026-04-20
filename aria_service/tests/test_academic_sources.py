"""Tests for the academic source adapters and output harvester.

Both modules shipped 2026-04-20 as the forward-roadmap items from the
2026-04-19 audit triage:
  - intel/sources/academic.py       — Semantic Scholar / OpenAlex / CrossRef
  - learning/output_harvester.py    — score + dry-run capture

These tests are all deterministic and network-free — they validate the
module contracts, the field maps, and the scoring / redaction logic.
Live upstream calls are out of scope (too brittle for CI).
"""
from __future__ import annotations

import asyncio
import os


# ═══════════════════════════════════════════════════════════════════════
# Academic source adapters
# ═══════════════════════════════════════════════════════════════════════

def test_academic_module_exposes_contract():
    from aria_service.intel.sources import academic
    import inspect
    assert inspect.iscoroutinefunction(academic.search_semantic_scholar)
    assert inspect.iscoroutinefunction(academic.search_openalex)
    assert inspect.iscoroutinefunction(academic.search_crossref)
    assert inspect.iscoroutinefunction(academic.search_all)


def test_academic_killswitch_disables_all():
    """ACADEMIC_APIS_ENABLED=0 must short-circuit every adapter to []."""
    from aria_service.intel.sources import academic
    saved = os.environ.get("ACADEMIC_APIS_ENABLED")
    os.environ["ACADEMIC_APIS_ENABLED"] = "0"
    try:
        r1 = asyncio.run(academic.search_semantic_scholar("anything"))
        r2 = asyncio.run(academic.search_openalex("anything"))
        r3 = asyncio.run(academic.search_crossref("anything"))
        r4 = asyncio.run(academic.search_all("anything"))
        assert r1 == [] and r2 == [] and r3 == [] and r4 == []
    finally:
        if saved is None:
            os.environ.pop("ACADEMIC_APIS_ENABLED", None)
        else:
            os.environ["ACADEMIC_APIS_ENABLED"] = saved


def test_academic_empty_query_returns_empty():
    from aria_service.intel.sources import academic
    assert asyncio.run(academic.search_semantic_scholar("")) == []
    assert asyncio.run(academic.search_openalex("")) == []
    assert asyncio.run(academic.search_crossref("")) == []


def test_web_search_wires_academic_backend():
    """Regression: the _search_academic fan-out must be in the
    backend_tasks list so every search() call surfaces academic
    results alongside Brave / SearXNG / Google News."""
    import inspect
    from aria_service.intel import web_search
    src = inspect.getsource(web_search.search)
    assert "_search_academic" in src, (
        "web_search.search() must call _search_academic in its "
        "backend fan-out. Regression: the academic adapters aren't "
        "wired into the primary search path."
    )
    assert callable(web_search._search_academic)


# ═══════════════════════════════════════════════════════════════════════
# Output harvester
# ═══════════════════════════════════════════════════════════════════════

def test_output_harvester_redacts_pii():
    from aria_service.learning import output_harvester as oh
    bad = (
        "Contact alice@example.com at +44 20 7946 0958 "
        "regarding account 123456789012."
    )
    scored = oh.score_response("", bad)
    # Shouldn't count as a good response (length too short, no cite)
    # but the redaction itself should have fired.
    assert scored["had_pii"] is True


def test_output_harvester_disabled_by_default():
    """Dry-run is the default. `is_enabled()` must be False unless
    ARIA_OUTPUT_HARVEST_ENABLED=1 is explicitly set."""
    from aria_service.learning import output_harvester as oh
    saved = os.environ.pop("ARIA_OUTPUT_HARVEST_ENABLED", None)
    try:
        assert oh.is_enabled() is False
    finally:
        if saved is not None:
            os.environ["ARIA_OUTPUT_HARVEST_ENABLED"] = saved


def test_output_harvester_dry_run_writes_nothing():
    """Even a high-scoring response must NOT write to disk when
    ARIA_OUTPUT_HARVEST_ENABLED is unset / 0."""
    from aria_service.learning import output_harvester as oh
    saved = os.environ.pop("ARIA_OUTPUT_HARVEST_ENABLED", None)
    try:
        high_quality = (
            "*🟢 BOTTOM LINE* — The procurement clause is [CONFIRMED].\n\n"
            "**Analysis**\n"
            "- Reference: https://www.nato.int/cps/en/stanag-4569.htm\n"
            "- Clause 4 applies to armour protection levels.\n"
            "- Supply-chain certification required under Clause 7.\n\n"
            "## Next step\n"
            "Escalate to procurement officer for review."
            + "\n\nExtended detail." * 30
        )
        result = asyncio.run(oh.harvest("analyse NATO clause 4", high_quality))
        assert result["ok"] is True
        assert result["dry_run"] is True
        assert result["written"] is False
    finally:
        if saved is not None:
            os.environ["ARIA_OUTPUT_HARVEST_ENABLED"] = saved


def test_output_harvester_scoring_signals():
    """Each signal should flip the score by roughly its declared
    weight. This keeps future refactors honest about the scoring math."""
    from aria_service.learning import output_harvester as oh

    # Baseline: empty-ish response should score 0.15 — passes
    # only the no_refusal_pattern signal (and length_reasonable is
    # False because len < 400).
    baseline = "Short reply."
    s0 = oh.score_response("q", baseline)
    assert s0["signals"]["no_refusal_pattern"] is True
    assert s0["signals"]["length_reasonable"] is False

    # Cited response — +0.30 over baseline
    cited = baseline + " https://example.com"
    s1 = oh.score_response("q", cited)
    assert s1["signals"]["cited_source"] is True
    assert s1["score"] > s0["score"]

    # Refusal — loses the no_refusal_pattern bonus
    refusal = "I can't help with that."
    s2 = oh.score_response("q", refusal)
    assert s2["signals"]["no_refusal_pattern"] is False


def test_output_harvester_handles_exception_gracefully():
    """harvest() must NEVER raise — it's called fire-and-forget from
    the chat pipeline."""
    from aria_service.learning import output_harvester as oh
    # Pass a non-string; module should return an error dict, not raise.
    r = asyncio.run(oh.harvest("q", None))  # type: ignore[arg-type]
    assert r["ok"] is False


def test_output_harvester_wired_into_chat_pipeline():
    """Both the non-streaming and streaming chat branches in
    aria_engine.py must call output_harvester.harvest() after the
    final response. Regression: accidental removal of the hook."""
    import inspect
    from aria_service import aria_engine
    src = inspect.getsource(aria_engine)
    # Should appear at least twice (one for /chat, one for /chat/stream)
    assert src.count("output_harvester") >= 3, (
        "output_harvester hook missing from one of the chat branches "
        "— check aria_engine.py has the import and task.create calls."
    )
