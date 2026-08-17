"""R-F4128 (C-163) — the coverage matrix reported 867/867 gaps and could not say why.

Measured live 2026-08-17 on aria-intel::

    /api/aria/learning/coverage
      cells 867 · gap_count 867 · populated_cells 0 · gap_pct 100.0
      coverage_score 0.0 · freshness_measured_cells 0
      every cell fact_count 0 · one cell signal_count 1

The operator's report was "the heatmap is not displaying any values". It is not a
rendering fault: there are no values. But the payload could not distinguish
between the three things that produce an identical 867/867:

  1. the fact source returned nothing,
  2. facts arrived but carried no matchable text,
  3. facts matched a domain but never a jurisdiction (or vice versa).

`signal_count: 1` on one cell proves the tally loop runs and that matching CAN
succeed, so by elimination the facts contributed nothing — but elimination is not
measurement, and `all_facts()`'s own docstring records this EXACT symptom before
(R-F164: "every coverage cell returned fact_count=0, leaving the dashboard heatmap
at 867/867 absent indefinitely"), caused then by a silent `hasattr` returning
False. Same number, different mechanism, and no instrument either time.

**Why the diagnostics live in the payload and not in a probe endpoint.** I tried
to measure `len(all_facts())` with a `flyctl ssh python3` one-off and got 0 — a
worthless reading, because a detached process has no loaded cache. That is the
trap §17 records for the cost meter, where the same shape produced a fabricated
"spent 0.0". The only honest place to measure a computation is INSIDE the
computation, in the process that runs it.

So the matcher now reports what it actually saw, on every build. This is
deliberately not "only on failure": a diagnostics block that appears only when
the result is empty cannot describe the dangerous case — a matrix that is
populated but from far fewer facts than expected.
"""
from __future__ import annotations

import asyncio

import pytest

from aria_service.intel import coverage_heatmap as ch


@pytest.fixture(scope="module")
def payload():
    # §3b — the builder is async; calling it bare yields a coroutine.
    return asyncio.run(ch.build_heatmap())


def test_the_payload_carries_matcher_diagnostics(payload):
    d = payload.get("matcher_diagnostics")
    assert isinstance(d, dict), (
        "an empty matrix must be able to name its own cause; there is no "
        "matcher_diagnostics block")


def test_it_reports_what_the_fact_source_returned(payload):
    d = payload["matcher_diagnostics"]
    for key in ("facts_seen", "signals_seen", "facts_source"):
        assert key in d, f"missing {key}"
    assert isinstance(d["facts_seen"], int)
    # The distinguishing field: WHY the list was what it was.
    assert d["facts_source"] in ("ok", "attribute_missing", "error"), d["facts_source"]


def test_it_separates_the_three_causes_of_an_empty_matrix(payload):
    """no facts / no text / no match are three different defects."""
    d = payload["matcher_diagnostics"]
    for key in ("facts_with_text", "facts_matched_domain", "facts_matched_both"):
        assert key in d, f"missing {key} — cannot tell an empty source from a "
        assert isinstance(d[key], int)
    # Monotonic by construction: each stage is a subset of the one before it.
    assert d["facts_matched_both"] <= d["facts_matched_domain"] <= d["facts_with_text"] <= d["facts_seen"], d


def test_it_reports_the_knowledge_cache_state(payload):
    """Distinguishes 'all_facts() returned empty' from 'the cache itself is empty'
    — the discriminator that inference could not settle."""
    d = payload["matcher_diagnostics"]
    assert "knowledge_cache_facts" in d, (
        "without the cache count, an empty facts list cannot be attributed to "
        "the accessor or to the cache behind it")
    v = d["knowledge_cache_facts"]
    assert v is None or isinstance(v, int), v


def test_diagnostics_are_emitted_when_the_matrix_is_POPULATED(monkeypatch):
    """A block that appears only on failure cannot describe the dangerous case:
    a populated matrix built from far fewer facts than expected. So feed the
    matcher a fact that genuinely matches and assert the block is still there —
    behaviourally, not by grepping the source for an `if`."""
    import aria_service.intel.knowledge as k

    monkeypatch.setattr(ch, "_HEATMAP_TTL_S", 0.0)   # the 120s cache would serve a stale build
    monkeypatch.setattr(k, "all_facts", lambda: [{
        "topic": "sanctions screening",
        "content": "sanctions screening obligations in the United States",
        "entity": "", "summary": "", "detail": "", "source": "",
    }])
    out = asyncio.run(ch.build_heatmap())
    d = out["matcher_diagnostics"]
    assert d["facts_seen"] == 1, d
    assert d["facts_with_text"] == 1, d
    assert d["facts_matched_domain"] >= 1, (
        "a fact containing both domain tokens must match a domain", d)


def test_an_unreadable_fact_source_is_reported_not_swallowed(monkeypatch):
    """`facts = _k.all_facts() if hasattr(...) else []` swallowed a missing
    attribute silently — that is the R-F164 mechanism verbatim. It must now be
    visible in the reading."""
    import aria_service.intel.knowledge as k

    def _boom():
        raise RuntimeError("cache unavailable")

    monkeypatch.setattr(ch, "_HEATMAP_TTL_S", 0.0)   # else the cache serves an earlier build
    monkeypatch.setattr(k, "all_facts", _boom)
    out = asyncio.run(ch.build_heatmap())
    d = out["matcher_diagnostics"]
    assert d["facts_source"] == "error", d
    assert d["facts_seen"] == 0
