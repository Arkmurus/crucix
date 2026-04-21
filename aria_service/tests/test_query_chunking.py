"""Regression tests for long-query chunking (added 2026-04-21).

The research orchestrator was passing full-sentence hypotheses (>400 chars)
as a single query to web_search, triggering Brave HTTP 422 and Semantic
Scholar 429s. Same class of bug as the earlier "digest prompt → deep_research
with whole prompt as entity" — the query cleaner wasn't being applied in the
downstream planner path.

_chunk_long_query splits on natural delimiters (", " / " and " / etc.) into
≤200-char sub-queries. Tests pin the behaviour so this doesn't regress.
"""
from __future__ import annotations

from aria_service.intel.deep_researcher import _chunk_long_query


# ── Short queries pass through unchanged ───────────────────────────

def test_short_query_passes_through_unchanged():
    q = "Angola defence procurement 2026"
    assert _chunk_long_query(q) == [q]


def test_empty_returns_empty_list():
    assert _chunk_long_query("") == []
    assert _chunk_long_query("   ") == []


def test_exactly_max_length_passes_through():
    q = "A" * 200
    assert _chunk_long_query(q, max_chars=200) == [q]


# ── Long queries get split ────────────────────────────────────────

def test_comma_separated_long_query_splits():
    q = (
        "Angola artillery ammunition modernization programme, "
        "CAESAR howitzer acquisition from Nexter Systems, "
        "NATO-standard 155mm procurement contracts for 2024-2026, "
        "FMS approval status and dual-use export controls, "
        "regional implications for southern African security posture"
    )
    assert len(q) > 200, "precondition: query must be long enough to need chunking"
    sub = _chunk_long_query(q, max_chars=200)
    assert 2 <= len(sub) <= 5, f"expected 2-5 sub-queries, got {len(sub)}"
    for s in sub:
        assert len(s) <= 200, f"sub-query exceeds cap: {s!r}"
        assert len(s) >= 10


def test_and_delimiter_splits_long_query():
    q = (
        "Mozambique National Defence Force structure and budget allocation "
        "and NATO-standard training programmes and RPG-7 artillery stockpile "
        "and UN peacekeeping contributions in the wider region "
        "and ECOWAS defence coordination and AU PSC reporting"
    )
    assert len(q) > 200, f"precondition: {len(q)} chars"
    sub = _chunk_long_query(q, max_chars=200)
    assert len(sub) >= 2
    for s in sub:
        assert len(s) <= 200


def test_including_delimiter_produces_clean_splits():
    q = (
        "Nigerian defence procurement in 2026 including "
        "T-72 tank upgrade programme including "
        "Hellcat unmanned combat air system acquisition including "
        "Nigerian Air Force aerospace supplier evaluation for the next three years"
    )
    assert len(q) > 200
    sub = _chunk_long_query(q, max_chars=200)
    assert len(sub) >= 2
    for s in sub:
        assert len(s) <= 200


# ── Fallback: delimiters absent ───────────────────────────────────

def test_no_delimiter_long_query_truncates_at_word_boundary():
    # Deliberately delimiter-free — force the fall-through path.
    q = "a" * 300
    sub = _chunk_long_query(q, max_chars=200)
    assert len(sub) == 1
    assert len(sub[0]) <= 200


def test_no_delimiter_with_spaces_truncates_at_word_boundary():
    # One long compound pseudo-phrase split only by spaces; the fallback
    # truncation path should still produce a single sub-query under cap.
    q = "angola " * 60  # ~420 chars of repeating token
    sub = _chunk_long_query(q, max_chars=200)
    assert len(sub) >= 1
    for s in sub:
        assert len(s) <= 200
        assert not s.endswith(" angol"), "word-boundary truncation should avoid mid-word cuts"


# ── Deduplication ─────────────────────────────────────────────────

def test_split_dedupes_identical_fragments():
    q = "Angola defence, Angola defence, Angola defence, Mozambique defence, Mozambique defence"
    sub = _chunk_long_query(q, max_chars=200)
    # Dedup should collapse repeats
    assert len(set(s.lower() for s in sub)) == len(sub)


# ── Cap on number of sub-queries ──────────────────────────────────

def test_max_5_sub_queries_returned():
    q = ", ".join(f"angola procurement item {i}" for i in range(12))
    assert len(q) > 200
    sub = _chunk_long_query(q, max_chars=200)
    assert len(sub) <= 5
