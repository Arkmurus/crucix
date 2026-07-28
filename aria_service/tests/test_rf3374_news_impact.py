"""R-F3374 — news→entity impact traces: analysis grounded in retrieved evidence, with independence enforced.

WHAT THIS AXIS ADDS. The four shipped corpora teach ARIA to establish FACTS —
resolve an entity, screen it, follow a chain, refuse a false premise. This teaches
her to INTERPRET: given real news about a company or person, what does it actually
mean for exposure, and what does the evidence NOT support?

WHY IT NEEDED A NEW INVARIANT. The earlier axes verify against a structured
payload (a match list, a company number) where truth is exact. News is prose, and
"what this means" is a judgement no validator can score. So the verifier does not
attempt to grade the analysis. It enforces the two things that ARE checkable and
that are exactly where fabrication enters:

  1. SOURCE GROUNDING — every source cited must be one the search actually
     returned. Same rule as R-F3366, now over retrieval instead of a screen.
  2. INDEPENDENCE — `memory://` results are ARIA'S OWN RAG, not outside
     corroboration. The live search genuinely returns them mixed in with web
     results (verified: a query for Rolls-Royce returned memory://52add3a6c5b4
     alongside a real kalkinemedia article). Counting them as corroboration is a
     documented defect class in this repo — dd_orchestrator keeps
     `_ADVERSE_SELF_SOURCE_MARKERS = ("memory://", "brain_hook:", "aria://",
     "rag://")` precisely because self-reference was once scored as support.
     A claim backed only by ARIA's own memory is SINGLE-SOURCE and must say so.

That is the C-3 independence gate expressed as training data: the model learns
that corroboration means two INDEPENDENT outside sources, and that saying
"single-source" is the correct answer when that is the truth.
"""
from __future__ import annotations

import json

import pytest

from scripts.train import build_tooluse_corpus as B


# ── a real search payload (shape + content verified live) ──────────────────

MIXED = {
    "query": "Rolls-Royce Holdings defence contract 2026",
    "results": [
        {"title": "research:web_search:defence procurement", "source": "memory:documents",
         "url": "memory://52add3a6c5b4", "snippet": "internal note"},
        {"title": "research:web_search:defence offset", "source": "memory:facts",
         "url": "memory://3fdfdb415d04", "snippet": "internal note"},
        {"title": "Is Rolls-Royce Building Momentum From Its Defence Order Pipeline",
         "source": "aria_search", "url": "https://kalkinemedia.com/uk/news/top-stories/rr",
         "snippet": "Rolls-Royce reported a rising defence order pipeline."},
    ],
}

TWO_INDEPENDENT = {
    "query": "Serco contract award",
    "results": [
        {"title": "Serco wins MoD contract", "source": "aria_search",
         "url": "https://www.reuters.com/serco-mod", "snippet": "Serco was awarded a contract."},
        {"title": "Serco secures defence deal", "source": "aria_search",
         "url": "https://www.ft.com/serco-defence", "snippet": "Serco secured a defence deal."},
    ],
}

ONLY_MEMORY = {
    "query": "Obscure Co news",
    "results": [
        {"title": "internal", "source": "memory:facts", "url": "memory://aaaa1111",
         "snippet": "we previously noted something"},
    ],
}

EMPTY = {"query": "Nothing Co news", "results": []}


# ── independence: memory:// is not corroboration ───────────────────────────

def test_memory_urls_are_not_independent_sources():
    assert B._independent_sources(MIXED) == {"kalkinemedia.com"}


def test_two_web_sources_count_as_independent():
    assert B._independent_sources(TWO_INDEPENDENT) == {"reuters.com", "ft.com"}


def test_self_reference_only_yields_no_independent_sources():
    assert B._independent_sources(ONLY_MEMORY) == set()


def test_all_self_source_schemes_are_excluded():
    payload = {"results": [
        {"url": "memory://a", "source": "memory:facts"},
        {"url": "rag://b", "source": "rag"},
        {"url": "aria://c", "source": "aria"},
        {"url": "brain_hook:d", "source": "brain"},
        {"url": "https://bbc.co.uk/x", "source": "aria_search"},
    ]}
    assert B._independent_sources(payload) == {"bbc.co.uk"}


# ── the trace ──────────────────────────────────────────────────────────────

def test_news_trace_is_a_grounded_tool_trace():
    t = B.build_news_impact_trace("Rolls-Royce Holdings plc", MIXED)
    assert t["label"] == "tooluse_news_impact"
    roles = [m["role"] for m in t["messages"]]
    assert "tool" in roles and roles[-1] == "assistant"
    call = next(m for m in t["messages"] if m.get("tool_calls"))
    assert call["tool_calls"][0]["function"]["name"] == "web_search"
    assert B.validate_trace(t) == [], B.validate_trace(t)


def test_single_independent_source_is_declared_single_source():
    """One outside source plus ARIA's own memory is still ONE source."""
    t = B.build_news_impact_trace("Rolls-Royce Holdings plc", MIXED)
    final = t["messages"][-1]["content"].lower()
    assert "single-source" in final or "one source" in final, final


def test_two_independent_sources_are_treated_as_corroborated():
    t = B.build_news_impact_trace("Serco Group plc", TWO_INDEPENDENT)
    assert B.validate_trace(t) == []
    final = t["messages"][-1]["content"].lower()
    assert "corroborat" in final, final


def test_no_independent_evidence_refuses_to_analyse():
    t = B.build_news_impact_trace("Obscure Co", ONLY_MEMORY)
    assert B.validate_trace(t) == []
    final = t["messages"][-1]["content"].lower()
    assert "own memory" in final or "no independent" in final, final


def test_empty_results_are_stated_not_filled_in():
    t = B.build_news_impact_trace("Nothing Co", EMPTY)
    assert B.validate_trace(t) == []
    final = t["messages"][-1]["content"].lower()
    assert "no" in final and ("found" in final or "returned" in final), final


# ── grounding: the analysis may not invent a source or an outlet ───────────

def test_citing_an_outlet_that_was_not_returned_is_rejected():
    t = B.build_news_impact_trace("Serco Group plc", TWO_INDEPENDENT)
    t["messages"][-1]["content"] += " The BBC also reported this [from bbc.co.uk]."
    errs = B.validate_trace(t)
    assert errs, "an outlet the search never returned was accepted"
    assert any("bbc.co.uk" in e for e in errs), errs


def test_claiming_corroboration_on_a_single_source_is_rejected():
    t = B.build_news_impact_trace("Rolls-Royce Holdings plc", MIXED)
    t["messages"][-1]["content"] = (
        "This is corroborated by multiple independent sources [from kalkinemedia.com]."
    )
    errs = B.validate_trace(t)
    assert errs, "corroboration was claimed from one independent source"


def test_memory_only_evidence_may_not_be_called_corroborated():
    t = B.build_news_impact_trace("Obscure Co", ONLY_MEMORY)
    t["messages"][-1]["content"] = "Corroborated by our records."
    assert B.validate_trace(t), "ARIA's own memory was accepted as corroboration"


# ── no regression across the four shipped axes ────────────────────────────

def test_prior_axes_still_validate():
    clean = {"result": "CLEAR", "status": "CLEAR",
             "sanctions": {"matched": False, "matches": [], "verdict": "CLEAR"}}
    assert B.validate_trace(B.build_trace("Tesco plc", clean)) == []
    assert B.validate_trace(B.build_challenge_trace("Tesco plc", clean, premise="clean")) == []
    assert B.validate_trace(B.build_resolution_trace("Chemring", {"results": [
        {"title": "CHEMRING GROUP PLC", "company_status": "active", "company_number": "00086662"}]})) == []
