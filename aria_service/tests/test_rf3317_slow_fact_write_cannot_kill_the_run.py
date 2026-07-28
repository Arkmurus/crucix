"""R-F3317 - one slow fact-store write must not cost the whole run.

PROVEN, not hypothesised. R-F3316's diagnostic on its first live run
(dd_cd7e7adc36e9) reported:

    deep research did not complete within 300s (bounded), last stage:
    fact retention (angles_run=11, jobs=33, analysed=33, retained=14)

All 33 articles were read and analysed, 14 were already banked, and the caller's
hard cancel landed DURING retention and discarded all 14. articles_read came back
0 for the fourth run in a row.

The retention loop already checks the budget before EVERY item, so the only way to
overshoot is for a single item to run longer than the entire remaining budget.
_process_analysis writes facts to the knowledge store; a slow write does exactly
that. A check before an unbounded await cannot bound it - that is the defect.

Four earlier attempts at this timeout were guesses because a hard cancel reports
nothing (R-F3258 topic guard, R-F3300 loop floor, R-F3306 incremental retention,
and R-F3316 which finally made the cancel say where it died). This one is aimed
at a measured stage with measured counters.
"""
from __future__ import annotations

import asyncio
import time

import pytest

from aria_service.intel import deep_researcher as dr


class _StubLLM:
    name = "stub"
    is_configured = True

    async def complete(self, system_prompt, user_message, **kw):
        from aria_service.llm.provider import LLMResult
        return LLMResult(text='{"summary": "stub", "key_findings": []}', model="stub")


@pytest.fixture
def _one_write_hangs(monkeypatch):
    """Fast everywhere except ONE fact-store write, which hangs like the live box."""
    articles = [
        {"title": f"A{i}", "link": f"https://example.invalid/{i}", "snippet": "s",
         "source": "example.invalid"}
        for i in range(6)
    ]
    calls = {"n": 0}

    async def _process(parsed, topic, hypotheses, *a, **kw):
        calls["n"] += 1
        if calls["n"] == 2:
            await asyncio.sleep(120)      # the slow knowledge-store write
        return (1, 0)

    monkeypatch.setattr(dr, "_web_search", lambda q, *a, **kw: asyncio.sleep(0, result=list(articles)))
    monkeypatch.setattr(dr, "_fetch_article_text", lambda u, *a, **kw: asyncio.sleep(0, result="x" * 2000))
    monkeypatch.setattr(dr, "_analyse_article", lambda *a, **kw: asyncio.sleep(
        0, result={"facts": [{"fact": "f"}], "validates": None, "challenges": None}))
    monkeypatch.setattr(dr, "_process_analysis", _process)
    monkeypatch.setattr(dr, "_mark_read", lambda *a, **kw: asyncio.sleep(0))
    monkeypatch.setattr(dr, "_load_hypotheses", lambda *a, **kw: asyncio.sleep(0, result=[]))
    monkeypatch.setattr(dr, "_save_hypotheses", lambda *a, **kw: asyncio.sleep(0))
    monkeypatch.setattr(dr, "_get_read_urls", lambda *a, **kw: asyncio.sleep(0, result=set()))
    monkeypatch.setattr(dr, "search_knowledge", lambda *a, **kw: [])
    return calls


@pytest.mark.asyncio
async def test_a_hanging_write_does_not_blow_the_caller_bound(_one_write_hangs):
    """THE CAPABILITY TEST. dd_orchestrator cancels at budget+3s and loses everything.

    Pre-fix the run sits in that one write for 120s and is killed by the caller.
    """
    budget = 12.0
    t0 = time.time()
    out = await dr.investigate(
        _StubLLM(), "AZURE PARKING LTD", depth="quick",
        investigate_people=0, deadline_s=budget,
    )
    elapsed = time.time() - t0

    assert elapsed <= budget + 3.0, (
        f"investigate() ran {elapsed:.1f}s against a {budget}s budget because one "
        "fact-store write was unbounded. The caller cancels at budget+3s and "
        "discards every article already retained."
    )
    assert isinstance(out, dict) and out


@pytest.mark.asyncio
async def test_the_articles_banked_before_the_slow_write_survive(_one_write_hangs):
    """The whole point: abandon the slow write, KEEP what is already retained."""
    out = await dr.investigate(
        _StubLLM(), "AZURE PARKING LTD", depth="quick",
        investigate_people=0, deadline_s=12.0,
    )
    assert out.get("articles_read", 0) >= 1, (
        "the article retained before the slow write must survive; returning 0 is "
        "the live defect (dd_cd7e7adc36e9: 14 retained, 0 delivered)"
    )


@pytest.mark.asyncio
async def test_the_cut_says_a_write_was_the_cause(_one_write_hangs):
    """Name the cause, so the next person does not re-derive it from scratch."""
    out = await dr.investigate(
        _StubLLM(), "AZURE PARKING LTD", depth="quick",
        investigate_people=0, deadline_s=12.0,
    )
    assert out.get("partial") is True
    stage = out.get("stopped_after") or ""
    assert "fact retention" in stage, stage
    assert "fact-store write" in stage, (
        f"the stop reason must distinguish a slow WRITE from simply running out "
        f"of clock. got: {stage!r}"
    )


@pytest.mark.asyncio
async def test_an_unbounded_run_is_unaffected(_one_write_hangs):
    """deadline_s=None must not impose a write timeout that was never asked for."""
    task = asyncio.ensure_future(dr.investigate(
        _StubLLM(), "AZURE PARKING LTD", depth="quick",
        investigate_people=0, deadline_s=None,
    ))
    done, pending = await asyncio.wait({task}, timeout=3.0)
    assert not done, "an unbounded run must still be waiting on the 120s write"
    task.cancel()
