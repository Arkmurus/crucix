"""R-F3300 - the stage that RETAINS evidence had no deadline check, so it lost all of it.

THE LIVE SYMPTOM. AZURE PARKING LTD, run dd_06f5888f0809, deep mode:

    digital: deep research did not complete within 300s (bounded) - partial
             result, NOT a clean check
    articles_read: 0 | facts_learned: 0 | search_angles: []

Zero articles after 300 seconds is not plausible as work; it is work thrown away.

WHY IT IS THAT LOOP AND NOT ANOTHER STAGE. Two details in the report identify it:

  * The gap carries the OUTER backstop wording from dd_orchestrator's
    _bounded_dd_op. The engine's own cooperative message (R-F3018, "deep research
    was bounded at Ns and stopped after X") is absent, so _mark_partial never
    fired anywhere.
  * The article stage only marks partial when tasks remain `_pending`. A run in
    which every article task COMPLETES sails past that checkpoint with partial
    still False.

Every other stage was already guarded: search fan-out checks _remaining() before
each query, the article gather uses asyncio.wait(timeout=_article_budget), the
person drill-down derives its budget, and synthesis bounds its own LLM call. The
sequential post-processing loop had no check at all, and it awaits state-store
writes per article (_process_analysis stores facts, _mark_read marks urls), so on
a slow or reconnecting store a few dozen articles take minutes.

It is the worst place in the function to be cancelled, because it is precisely
where gathered evidence gets retained. asyncio.wait_for CANCELS, so the caller
received {} and the customer's report said 0 articles.

The floor is the synthesis reserve, matching the search fan-out, so a cut still
leaves the closing assessment its budget and the call lands INSIDE the caller's
bound rather than being killed by it.

These tests drive the real investigate(). Only _process_analysis is slow, which
isolates the guarded loop: without the guard the call runs to the full stub cost
and blows its deadline, which is the assertion that fails pre-fix.

R-F3306 - WHAT THE FIRST FIX EXPOSED. Deployed, the next live run
(dd_f89fdb2e18f6) stopped being cancelled and reported honestly for the first
time: "bounded at 297s and stopped after article read (28 of 33 articles
analysed) - 0 article(s) analysed, 0 fact(s) retained". Twenty-eight articles
fetched, read and LLM-analysed, and not one fact reached the customer. R-F3300
had converted a silent total loss into an HONEST total loss.

The cause was the budget split, not the guard: _article_budget was everything
except the synthesis reserve, so the article stage always left retention nothing
and the floor fired on its first iteration. Reserving a fixed slice would only
move the guess, since per-article retention cost depends on state-store latency.
R-F3306 retains each article as it is analysed, which removes the need to predict
and makes "analysed but discarded" unrepresentable: a cut can now only lose the
un-analysed tail.

Both fixes are proven failing-first by reverting them here, and the revert of
R-F3306 reproduces the live symptom exactly: "article read (6 of 8 articles
analysed)" with articles_read=0.
"""
from __future__ import annotations

import asyncio
import time

import pytest

from aria_service.intel import deep_researcher as dr


# Tuned so the UNGUARDED loop genuinely blows the caller's bound. An earlier
# draft used 0.6s per article against a 5s deadline; the whole post-processing
# pass then cost ~4.8s, which still fit inside the caller's deadline+3s margin,
# so three of these four tests passed with the fix REMOVED and proved nothing.
# Verified both ways after retuning: guard removed -> 3 of 4 fail.
# A second miss worth recording: 4.0s was then too TIGHT. The engine spaces
# search angles by 0.5s (R-F1594 rate-limiting), so the fixture's own startup is
# ~3s and the article stage was reporting "no budget left" before post-processing
# ever began, which tests a different, already-guarded branch.
# Budget must be large enough that retention genuinely starts, and small enough
# that the UNGUARDED loop still overruns the caller's deadline+3s cancel.
_N_ARTICLES = 8           # several angles return the same ones, so ~10+ jobs
_PER_ARTICLE_S = 1.5      # ~15s+ of retention work when unguarded
_DEADLINE_S = 9.0         # caller cancels at 12.0s, so unguarded MUST overrun


class _StubLLM:
    """Fast, deterministic. The LLM is not what this test is about."""

    name = "stub"
    is_configured = True

    async def complete(self, system_prompt, user_message, **kw):
        from aria_service.llm.provider import LLMResult
        return LLMResult(text='{"summary": "stub", "key_findings": []}', model="stub")


@pytest.fixture
def _fast_engine(monkeypatch):
    """Everything cheap except the one stage under test."""
    articles = [
        {"title": f"Article {i}", "link": f"https://example.invalid/{i}",
         "snippet": "s", "source": "example.invalid"}
        for i in range(_N_ARTICLES)
    ]

    async def _search(query, *a, **kw):
        return list(articles)

    async def _fetch(url, *a, **kw):
        return "x" * 2000

    async def _analyse(*a, **kw):
        return {"facts": [{"fact": "f"}], "validates": None, "challenges": None}

    processed: list[int] = []

    async def _slow_process(parsed, topic, hypotheses, *a, **kw):
        # The real one awaits state-store writes. On a reconnecting store this is
        # seconds per article, which is the live condition being reproduced.
        await asyncio.sleep(_PER_ARTICLE_S)
        processed.append(1)
        return (1, 0)

    monkeypatch.setattr(dr, "_web_search", _search)
    monkeypatch.setattr(dr, "_fetch_article_text", _fetch)
    monkeypatch.setattr(dr, "_analyse_article", _analyse)
    monkeypatch.setattr(dr, "_process_analysis", _slow_process)
    monkeypatch.setattr(dr, "_mark_read", lambda *a, **kw: asyncio.sleep(0))
    monkeypatch.setattr(dr, "_load_hypotheses", lambda *a, **kw: asyncio.sleep(0, result=[]))
    monkeypatch.setattr(dr, "_save_hypotheses", lambda *a, **kw: asyncio.sleep(0))
    monkeypatch.setattr(dr, "_get_read_urls", lambda *a, **kw: asyncio.sleep(0, result=set()))
    monkeypatch.setattr(dr, "search_knowledge", lambda *a, **kw: [])
    return processed


@pytest.mark.asyncio
async def test_it_returns_inside_its_budget_instead_of_being_cancelled(_fast_engine):
    """THE CAPABILITY TEST. The whole defect is overrunning and being killed.

    dd_orchestrator wraps this in wait_for at deadline+3s. Overrun by more than
    that and every article read and fact learned is discarded, which is what the
    customer's report showed as articles_read=0.

    Pre-fix the post-processing loop runs all six articles unconditionally
    (~3.6s of sleeps on top of the rest) and blows straight through the deadline.
    """
    t0 = time.time()
    out = await dr.investigate(
        _StubLLM(), "AZURE PARKING LTD", depth="quick",
        investigate_people=0, deadline_s=_DEADLINE_S,
    )
    elapsed = time.time() - t0

    assert elapsed <= _DEADLINE_S + 3.0, (
        f"investigate() ran {elapsed:.1f}s against a {_DEADLINE_S}s budget. "
        "The caller cancels at budget+3s and discards EVERYTHING it gathered."
    )
    assert isinstance(out, dict) and out, "must return its work, not nothing"


@pytest.mark.asyncio
async def test_it_keeps_what_it_processed_rather_than_losing_all_of_it(_fast_engine):
    """Partial evidence is the point. Zero is the bug."""
    out = await dr.investigate(
        _StubLLM(), "AZURE PARKING LTD", depth="quick",
        investigate_people=0, deadline_s=_DEADLINE_S,
    )
    assert out.get("articles_read", 0) > 0, (
        "a bounded run must RETURN the articles it managed to process; "
        "articles_read=0 after a full budget is the live defect"
    )


@pytest.mark.asyncio
async def test_the_cut_is_declared_and_names_the_stage(_fast_engine):
    """An honest partial, not a silent truncation.

    The report must be able to say the sweep was not exhaustive. A cut that does
    not set `partial` reads downstream as a complete, clean digital section,
    which is a false clean.
    """
    out = await dr.investigate(
        _StubLLM(), "AZURE PARKING LTD", depth="quick",
        investigate_people=0, deadline_s=_DEADLINE_S,
    )
    if out.get("articles_read", 0) < _N_ARTICLES:
        assert out.get("partial") is True, (
            "processed fewer articles than gathered but did not declare partial"
        )
        # R-F3306 moved WHERE the cut lands. With retention incremental, the
        # binding constraint is normally reading, not retention, so "article read"
        # is the expected stage and "fact retention" now means the state store was
        # slow enough to bind first. Either is honest; an unnamed stage is not.
        stage = out.get("stopped_after") or ""
        assert any(s in stage for s in ("article read", "fact retention", "search fan-out")), (
            "the stage must name itself; locating the last unnamed failure of "
            f"this kind took three attempts (R-F3296). got: {stage!r}"
        )



# NOTE: an earlier test here asserted the analysed==retained invariant using the
# retention-slow fixture above. It could only ever SKIP, because that fixture
# makes retention the binding constraint by design, so the cut never lands at the
# article stage. A test that always skips is the same "green but proves nothing"
# problem this file exists to document, so it was removed rather than left in the
# summary line. The invariant is asserted, non-vacuously, by
# test_nothing_analysed_is_thrown_away_when_reading_binds below.

@pytest.fixture
def _reading_is_the_bottleneck(monkeypatch):
    """Production-shaped: LLM analysis dominates, retention is cheap.

    The live run is this shape, and it is the shape that produced "28 of 33
    articles analysed, 0 retained". The other fixture inverts it to exercise the
    R-F3300 floor; this one exercises the R-F3306 invariant, and without it that
    invariant only ever reached a skip.
    """
    articles = [
        {"title": f"Article {i}", "link": f"https://example.invalid/{i}",
         "snippet": "s", "source": "example.invalid"}
        for i in range(_N_ARTICLES)
    ]

    async def _search(query, *a, **kw):
        return list(articles)

    async def _slow_analyse(*a, **kw):
        # Sized against the engine's real concurrency. _process_one_article runs
        # under Semaphore(6) and depth="quick" yields ~10 jobs, so at 1.5s the two
        # rounds finished INSIDE the budget, no cut occurred, and this test passed
        # even with the fix reverted. 3.0s forces the second round to be cut.
        await asyncio.sleep(3.0)          # the expensive LLM read
        return {"facts": [{"fact": "f"}], "validates": None, "challenges": None}

    async def _fast_process(parsed, topic, hypotheses, *a, **kw):
        await asyncio.sleep(0.01)         # a healthy store
        return (1, 0)

    monkeypatch.setattr(dr, "_web_search", _search)
    monkeypatch.setattr(dr, "_fetch_article_text", lambda *a, **kw: asyncio.sleep(0, result="x" * 2000))
    monkeypatch.setattr(dr, "_analyse_article", _slow_analyse)
    monkeypatch.setattr(dr, "_process_analysis", _fast_process)
    monkeypatch.setattr(dr, "_mark_read", lambda *a, **kw: asyncio.sleep(0))
    monkeypatch.setattr(dr, "_load_hypotheses", lambda *a, **kw: asyncio.sleep(0, result=[]))
    monkeypatch.setattr(dr, "_save_hypotheses", lambda *a, **kw: asyncio.sleep(0))
    monkeypatch.setattr(dr, "_get_read_urls", lambda *a, **kw: asyncio.sleep(0, result=set()))
    monkeypatch.setattr(dr, "search_knowledge", lambda *a, **kw: [])


@pytest.mark.asyncio
async def test_nothing_analysed_is_thrown_away_when_reading_binds(_reading_is_the_bottleneck):
    """THE LIVE SHAPE. Cut at the article stage, and every analysed article kept.

    Pre-R-F3306 the article stage took the budget down to the synthesis reserve
    and the separate retention pass got none of it, so this returned 0.
    """
    import re

    out = await dr.investigate(
        _StubLLM(), "AZURE PARKING LTD", depth="quick",
        investigate_people=0, deadline_s=_DEADLINE_S,
    )

    stage = out.get("stopped_after") or ""
    m = re.search(r"article read \((\d+) of (\d+) articles analysed\)", stage)

    # VERIFY THE INSTRUMENT. If the run finished every article there was no cut,
    # and this test proves nothing about losing work at a boundary. It must fail
    # rather than pass vacuously: at 1.5s per article it did exactly that, and
    # went green with the fix reverted.
    assert m, (
        "the run was NOT cut at the article stage, so this test is vacuous. "
        f"Re-size the fixture against Semaphore(6). stopped_after={stage!r}"
    )

    analysed = int(m.group(1))
    retained = out.get("articles_read", 0)
    assert retained > 0, (
        "the run analysed articles and retained none, which is exactly the live "
        f"defect (dd_f89fdb2e18f6: 28 analysed, 0 retained). stopped_after={stage!r}"
    )
    assert retained == analysed, (
        f"reported {analysed} articles analysed but retained {retained}"
    )


@pytest.mark.asyncio
async def test_an_unbounded_call_is_unaffected(_fast_engine):
    """deadline_s=None must keep the original behaviour: process everything.

    Not asserted as == _N_ARTICLES: several search angles return the same
    articles, and with _mark_read stubbed the read-url filter never grows, so the
    job count legitimately exceeds the distinct article count. Asserting the
    engine's internal fan-out arithmetic would be asserting the fixture.
    """
    unbounded = await dr.investigate(
        _StubLLM(), "AZURE PARKING LTD", depth="quick",
        investigate_people=0, deadline_s=None,
    )
    assert unbounded.get("partial") in (None, False), "no budget means no partial"
    assert unbounded.get("articles_read", 0) >= _N_ARTICLES, (
        "an unbounded run must process every gathered job, not stop early"
    )

    bounded = await dr.investigate(
        _StubLLM(), "AZURE PARKING LTD", depth="quick",
        investigate_people=0, deadline_s=_DEADLINE_S,
    )
    assert bounded.get("articles_read", 0) < unbounded.get("articles_read", 0), (
        "the budget must actually bite: a bounded run that processes as much as "
        "an unbounded one means the guard never engaged and this suite proves "
        "nothing"
    )
