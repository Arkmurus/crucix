"""R-F2391 — honest grounding attribution in source_verifier.

Background (measured live 2026-07-03): the composite's 45%-weight verification
signal was structurally empty because tool-using answers (screen / investigate /
self_introspect) don't embed clause-15 `[from snippet #N]` markers, so
verify_response scored them `no_citations` (grounded_rate=None) — the signal
never sampled. R-F2391 closes this HONESTLY:

  1. frame_tool_context_for_citation() numbers the tool output into cite-able
     `Snippet #N:` blocks + instructs inline citation, so ARIA actually cites.
  2. verify_response() now VALIDATES `[from snippet #N]` / `[EXTRACT N]` markers
     against the context the LLM saw — a marker naming a snippet that was NOT
     provided is fabricated provenance and is DISCOUNTED from the grounded rate
     (never inflated). "A tool ran" alone still grounds nothing.

These are capability tests: they drive verify_response() (the real path) and
assert the user-visible verdict/rate, including the anti-inflation guarantee.
"""
from __future__ import annotations

from aria_service.intel import source_verifier as sv


# ── frame_tool_context_for_citation ─────────────────────────────────────────

def test_framing_numbers_snippets_and_instructs_citation():
    framed = sv.frame_tool_context_for_citation(
        "First source: OFAC lists Entity A.\n\nSecond source: EU does not."
    )
    assert "Snippet #1:" in framed
    assert "Snippet #2:" in framed
    # R-F2396 — native style: instructs source-name citation + confidence words,
    # and must NOT carry the foreign token or pre-filled tag examples (those trip
    # aria_engine.py:627 I1_VERIFICATION_TAG_FAKE).
    assert "source's own name" in framed              # native source-name citation
    assert "confidence word" in framed                # tag-on-sentence discipline
    assert "CONFIRMED" in framed and "ASSESSED" in framed
    assert "[from snippet #N]" not in framed          # no foreign token
    assert "[CONFIRMED]" not in framed                # no pre-filled tag example


def test_framing_empty_context_is_safe_noop():
    assert sv.frame_tool_context_for_citation("") == ""
    assert sv.frame_tool_context_for_citation("   ") == ""


def test_framing_single_blob_becomes_snippet_1():
    framed = sv.frame_tool_context_for_citation("one blob of tool output with no blank lines")
    assert "Snippet #1:" in framed
    assert "Snippet #2:" not in framed


# ── verify_response: genuine grounding is credited ──────────────────────────

def test_genuine_snippet_citation_is_grounded():
    ctx = "Snippet #1: OFAC SDN list includes Rosoboronexport. https://ofac.treasury.gov/x"
    resp = "Rosoboronexport is on the OFAC SDN list [from snippet #1]."
    v = sv.verify_response(resp, ctx)
    assert v["verdict"] == "grounded"
    assert v["grounded_rate"] == 1.0
    assert v["invalid_refs"] == 0
    assert v["tool_refs"] == 1


# ── verify_response: fabricated snippet refs are NOT inflated ────────────────

def test_fabricated_snippet_ref_scored_honestly_low():
    """A `[from snippet #7]` when only snippet #1 was provided is fabricated
    provenance — it must score ungrounded (0.0), never a blanket 1.0."""
    ctx = "Snippet #1: The only source block provided."
    resp = "This claim has a source [from snippet #7]."
    v = sv.verify_response(resp, ctx)
    assert v["invalid_refs"] == 1
    assert v["grounded_rate"] == 0.0
    assert v["verdict"] == "ungrounded"


def test_partial_grounding_when_one_of_two_refs_fabricated():
    ctx = "Snippet #1: Real source A.\n\nSnippet #2: Real source B."
    resp = "Fact one [from snippet #1]. Fact two [from snippet #9]."
    v = sv.verify_response(resp, ctx)
    assert v["tool_refs"] == 2
    assert v["invalid_refs"] == 1
    assert v["grounded_rate"] == 0.5
    assert v["verdict"] == "partial"


# ── verify_response: "a tool ran" alone grounds nothing ─────────────────────

def test_tool_ran_but_no_markers_is_no_citations_not_grounded():
    """screen/investigate-style answer: tool_context present, but the response
    embeds NO citation markers. Must stay no_citations (grounded_rate None) —
    NOT credited just because a tool produced context."""
    ctx = "Snippet #1: OFAC screening result for the entity."
    resp = "Based on the screening, the entity appears on a watchlist. I recommend enhanced DD."
    v = sv.verify_response(resp, ctx)
    assert v["verdict"] == "no_citations"
    assert v["grounded_rate"] is None


def test_no_tool_context_is_no_tool():
    v = sv.verify_response("Some conversational answer with no sources.", "")
    assert v["verdict"] == "no_tool"
    assert v["grounded_rate"] is None


# ── count_invalid_snippet_refs helper ───────────────────────────────────────

def test_count_invalid_snippet_refs_direct():
    ctx = "Snippet #1: a\n\nSnippet #2: b"
    assert sv.count_invalid_snippet_refs("x [from snippet #1] y [from snippet #2]", ctx) == 0
    assert sv.count_invalid_snippet_refs("x [from snippet #3]", ctx) == 1
    assert sv.count_invalid_snippet_refs("no markers here", ctx) == 0
