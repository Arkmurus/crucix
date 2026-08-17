"""R-F4136 (C-166) — measure WHICH question the ranking stalls are asking
before building the answer.

C-166 records that `_rank_knowledge_facts` dominates the wedge dumps and
recommends making the O(corpus) scan sublinear. Two very different roots
produce identical dumps:

  * **per-call cost** — one chat turn pays one scan, or
  * **amplification** — a research loop issues one scan PER ARTICLE / PER
    ENTITY across a 6-thread pool, re-scanning the corpus dozens of times for
    one request.

`deep_researcher` alone holds ELEVEN call sites, several inside per-item loops,
so amplification is the likely root — but likely is not measured (§22), and the
two demand opposite fixes: an index for the first, collapsing the repeat for the
second. Building a sublinear index into the chat hot path to fix a cause nobody
confirmed is how you spend a week on the wrong thing.

**A prototype was built and it FAILED — recorded so nobody repeats it.** A
per-fact 512-bit trigram bloom with exact substring verification, measured on a
realistic 567,000-fact corpus (60k-word Zipf vocabulary, 704 chars/fact):

    common word  old 0.882s | new 0.294s  ->  3.0x
    rare word    old 0.326s | new 0.091s  ->  3.6x
    substring    old 0.347s | new 0.327s  ->  1.1x
    no match     old 0.269s | new 0.059s  ->  4.5x
    ALL RESULTS IDENTICAL: True     signatures: 36 MB, build 597s

Semantics held perfectly; the SPEED did not. 704-char facts carry ~550 distinct
trigrams, which saturates any affordable bloom — 359 of 512 bits set — so a
7-trigram query word has a ~8% false-positive rate and the filter barely
filters. 3x is not worth a new index on the hot path.

That run also corrected C-166's headline number: the real per-call cost is
**0.27-0.88s**, not the 1.0-1.5s recorded there, which came from a degenerate
10-word synthetic where every query matched most of the corpus.
"""
from __future__ import annotations

import time

import pytest

from aria_service.intel import knowledge as k


@pytest.fixture(autouse=True)
def _clean_stats(monkeypatch):
    monkeypatch.setattr(k, "_rank_stats", {}, raising=False)
    monkeypatch.setattr(k, "_rank_amplification_announced", False, raising=False)
    yield


def _install(monkeypatch, n=50):
    facts = [{"id": f"f{i}", "topic": f"topic{i}", "content": f"content sanctions {i}",
              "accessCount": 0, "confidence": 0.9, "createdAt": "2026-01-01"}
             for i in range(n)]
    monkeypatch.setattr(k, "_cache", {"facts": facts}, raising=False)
    k._search_lc.clear()
    monkeypatch.setattr(k, "_search_lc_facts_id", 0, raising=False)


def test_a_ranking_call_is_counted_and_attributed(monkeypatch):
    _install(monkeypatch)
    k._rank_knowledge_facts("sanctions", 5)
    s = k.ranking_stats()
    assert s["total_calls"] == 1, s
    # The caller is THIS test module, not knowledge.py — knowledge adds itself
    # to the skip list, so the instrument names who ASKED, not the plumbing.
    assert any("test_rf4136" in c for c in s["callers"]), s["callers"]


def test_the_instrument_never_reports_knowledge_as_its_own_caller(monkeypatch):
    """The failure mode that would make this useless: every call attributed to
    `aria_service.intel.knowledge`, which answers nothing. C-154 established
    that plumbing wins frame counts unless it is excluded explicitly."""
    _install(monkeypatch)
    k.search_knowledge("sanctions")          # goes through the shell
    assert "aria_service.intel.knowledge" not in k.ranking_stats()["callers"]


def test_the_early_returns_are_counted_too(monkeypatch):
    """A caller hammering the cheap path is amplification too. If only the
    expensive path were counted, a hot loop of empty queries would read as a
    quiet process while it burned the pool."""
    _install(monkeypatch)
    k._rank_knowledge_facts("a b", 5)        # all words <= 2 chars -> early []
    monkeypatch.setattr(k, "_cache", {}, raising=False)
    k._rank_knowledge_facts("sanctions", 5)  # empty cache -> early []
    assert k.ranking_stats()["total_calls"] == 2


def test_search_knowledge_and_search_fact_records_both_route_through_it(monkeypatch):
    """Both public entry points must be visible, or the total under-reports."""
    _install(monkeypatch)
    k.search_knowledge("sanctions")
    k.search_fact_records("sanctions", limit=3)
    assert k.ranking_stats()["total_calls"] == 2


def test_ranking_results_are_unchanged_by_the_instrument(monkeypatch):
    """The shell must be transparent. It wraps the scan in try/finally, so a
    regression here would change what recall returns."""
    _install(monkeypatch)
    got = [f["id"] for f in k._rank_knowledge_facts("sanctions", 5)]
    direct = [f["id"] for f in k._rank_knowledge_facts_inner("sanctions", 5)]
    assert got == direct and got, (got, direct)


def test_an_exception_in_the_scan_still_counts_and_still_propagates(monkeypatch):
    """`finally`, not `except`: the instrument must not swallow a real error,
    and must not lose the call either."""
    def boom(q, l):
        raise RuntimeError("scan exploded")
    monkeypatch.setattr(k, "_rank_knowledge_facts_inner", boom, raising=True)
    monkeypatch.setattr(k, "_cache", {"facts": []}, raising=False)
    with pytest.raises(RuntimeError):
        k._rank_knowledge_facts("sanctions", 5)
    assert k.ranking_stats()["total_calls"] == 1


def test_a_broken_instrument_never_breaks_a_ranking(monkeypatch):
    """An instrument that can take down recall is worse than no instrument."""
    _install(monkeypatch)
    monkeypatch.setattr(k, "_rank_caller",
                        lambda: (_ for _ in ()).throw(RuntimeError("probe died")),
                        raising=True)
    assert k._rank_knowledge_facts("sanctions", 5), "recall died with the probe"


def test_amplification_reaches_the_brain_ONCE_per_process(monkeypatch):
    """§21a. Once per process, not once per call: past the threshold EVERY
    call qualifies, so a per-call gap is the self-sustaining flood that has
    already filled the 500-slot capability ledger."""
    _install(monkeypatch)
    calls: list[dict] = []
    import aria_service.intel.engine_wiring as ew
    monkeypatch.setattr(ew, "wire_failure", lambda **kw: calls.append(kw), raising=True)
    monkeypatch.setattr(k, "_RANK_CALLS_ALERT", 3, raising=False)
    for _ in range(12):
        k._rank_knowledge_facts("sanctions", 5)
    assert len(calls) == 1, f"expected one announcement, got {len(calls)}"
    assert calls[0]["gap_type"] == "ranking_amplification"
    assert calls[0]["module"] == "knowledge"
    assert k.ranking_stats()["amplification_announced"] is True


def test_the_announcement_can_actually_fire_and_is_not_decorative(monkeypatch):
    """A guard that cannot fire is not a guard (R-F3858). Below the threshold
    it must stay silent — otherwise the test above would pass on a stub that
    always announces."""
    _install(monkeypatch)
    calls: list[dict] = []
    import aria_service.intel.engine_wiring as ew
    monkeypatch.setattr(ew, "wire_failure", lambda **kw: calls.append(kw), raising=True)
    monkeypatch.setattr(k, "_RANK_CALLS_ALERT", 10_000, raising=False)
    monkeypatch.setattr(k, "_RANK_SECONDS_ALERT", 1e9, raising=False)
    for _ in range(5):
        k._rank_knowledge_facts("sanctions", 5)
    assert calls == [], "announced without crossing any threshold"


def test_stats_distinguish_amplification_from_per_call_cost(monkeypatch):
    """The whole point: the payload must ANSWER C-166's question. One caller
    with many calls reads differently from many callers with one each."""
    _install(monkeypatch)
    for _ in range(4):
        k._rank_knowledge_facts("sanctions", 5)
    s = k.ranking_stats()
    row = next(iter(s["callers"].values()))
    assert row["calls"] == 4
    assert s["mean_seconds"] is not None and s["total_seconds"] >= 0
    assert row["facts_scanned"] == 4 * 50, row


def test_the_instrument_costs_far_less_than_what_it_measures(monkeypatch):
    """An instrument that costs what it measures is not an instrument.

    The scan is 0.27-0.88s on the live corpus; the frame walk plus a
    perf_counter pair must be orders of magnitude below that. Asserted as a
    generous absolute ceiling rather than a ratio, because a ratio against a
    50-fact test corpus would be meaningless.
    """
    _install(monkeypatch)
    k._rank_knowledge_facts("sanctions", 5)          # warm imports
    t0 = time.perf_counter()
    for _ in range(200):
        who = k._rank_caller()
        k._record_rank_call(who, time.perf_counter(), 50)
    per_call = (time.perf_counter() - t0) / 200
    assert per_call < 0.002, f"instrument costs {per_call*1000:.2f} ms/call"


def test_get_stats_exposes_it(monkeypatch):
    """Collected but unqueryable is a private variable. C-166 needs an answer
    from PRODUCTION, where the research loops actually run."""
    import asyncio
    _install(monkeypatch)
    k._rank_knowledge_facts("sanctions", 5)

    async def fake_load():
        return {"facts": [], "queries": [], "learnings": []}
    monkeypatch.setattr(k, "_load", fake_load, raising=True)
    out = asyncio.run(k.get_stats())
    assert out["ranking"]["total_calls"] == 1, out["ranking"]
