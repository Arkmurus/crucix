"""R-F3018 — deep research returns PARTIAL results instead of being cancelled.

THE DEFECT. Inside a DD, `deep_researcher.investigate()` ran under an outer
`asyncio.wait_for(..., 40s)`. `wait_for` CANCELS: at 40s every article read and
every fact learned was discarded and the DD received `{}`. The report then printed
"deep research did not complete within 40s (bounded) — partial result", which was
false twice over — the result was not partial, it was zero. Two budgets also
disagreed by 5×: the outer 40s vs this function's own 200s person-drill-down guard,
which therefore could never be honoured.

THE FIX. The budget is COOPERATIVE and owned by investigate(): checked at every
stage boundary, sub-budgets derived from what REMAINS, the article gather harvested
with `asyncio.wait(timeout=)` so finished work survives, and the function RETURNS
what it has with partial/stopped_after set.
"""
import asyncio
import time
from unittest.mock import patch, AsyncMock

import pytest

from aria_service.intel import deep_researcher as dr

# R-F3782/§16 — NOT inspect.getsource: it slices at line numbers captured
# AT IMPORT, so a mid-run edit silently returns a DIFFERENT function's body.
from ._source_probe import module_source


class _FakeLLM:
    is_configured = True

    def __init__(self, delay=0.0):
        self.delay = delay
        self.calls = 0

    async def complete(self, *a, **k):
        self.calls += 1
        await asyncio.sleep(self.delay)
        class _R:
            text = '{"key_findings": ["x"], "risks": []}'
        return _R()


def _article(i):
    return {"title": f"a{i}", "link": f"https://example.com/{i}"}


def test_rf3018_investigate_accepts_a_deadline():
    import inspect
    assert "deadline_s" in inspect.signature(dr.investigate).parameters


def test_rf3018_slow_search_returns_partial_not_nothing():
    """THE CAPABILITY: with a budget too small for the whole sweep, the caller gets
    a real result object marked partial — not a cancellation that destroys it."""
    async def go():
        slow_calls = {"n": 0}

        async def _slow_search(q, *a, **k):
            slow_calls["n"] += 1
            await asyncio.sleep(0.6)
            return [_article(slow_calls["n"])]

        async def _fetch(url, *a, **k):
            return "body text " * 40

        async def _analyse(*a, **k):
            return {"facts": [{"topic": "t", "content": "c", "confidence": "PROBABLE"}]}

        llm = _FakeLLM()
        with patch.object(dr, "_web_search", new=_slow_search), \
             patch.object(dr, "_fetch_article_text", new=_fetch), \
             patch.object(dr, "_analyse_article", new=_analyse), \
             patch.object(dr, "_load_hypotheses", new=AsyncMock(return_value=[])), \
             patch.object(dr, "_save_hypotheses", new=AsyncMock(return_value=None)), \
             patch.object(dr, "_get_read_urls", new=AsyncMock(return_value=set())), \
             patch.object(dr, "_mark_read", new=AsyncMock(return_value=None)), \
             patch.object(dr, "_process_analysis", new=AsyncMock(return_value=(1, 0))), \
             patch.object(dr, "search_knowledge", return_value=""):
            t0 = time.time()
            out = await dr.investigate(llm, "Acme Defence Ltd due diligence",
                                       depth="quick", investigate_people=0,
                                       deadline_s=3.0)
            elapsed = time.time() - t0
        assert isinstance(out, dict), "must RETURN, not be cancelled"
        assert out["partial"] is True, "budget cut the sweep short — say so"
        assert out["stopped_after"], "must name WHERE it stopped"
        assert out["budget_s"] == 3.0
        # honours the budget with a small allowance for the in-flight call
        assert elapsed < 3.0 + 2.5, f"overran its cooperative budget: {elapsed:.1f}s"
        return out
    asyncio.run(go())


def test_rf3018_unbounded_run_is_not_partial():
    """No deadline → behave exactly as before (no false 'partial' label)."""
    async def go():
        async def _search(q, *a, **k):
            return [_article(1)]

        llm = _FakeLLM()
        with patch.object(dr, "_web_search", new=_search), \
             patch.object(dr, "_fetch_article_text", new=AsyncMock(return_value="body " * 40)), \
             patch.object(dr, "_analyse_article", new=AsyncMock(return_value={"facts": []})), \
             patch.object(dr, "_load_hypotheses", new=AsyncMock(return_value=[])), \
             patch.object(dr, "_save_hypotheses", new=AsyncMock(return_value=None)), \
             patch.object(dr, "_get_read_urls", new=AsyncMock(return_value=set())), \
             patch.object(dr, "_mark_read", new=AsyncMock(return_value=None)), \
             patch.object(dr, "_process_analysis", new=AsyncMock(return_value=(0, 0))), \
             patch.object(dr, "search_knowledge", return_value=""):
            out = await dr.investigate(llm, "Acme Defence Ltd due diligence",
                                       depth="quick", investigate_people=0)
        assert out["partial"] is False and out["stopped_after"] == ""
        assert out["budget_s"] is None
    asyncio.run(go())


def test_rf3018_person_walk_skipped_when_budget_cannot_fit_it():
    """The 200s person budget could never be honoured under a 40s caller. With
    almost no time left the walk must be SKIPPED (and said so), never started."""
    async def go():
        called = {"walk": False}

        async def _walk(*a, **k):
            called["walk"] = True
            return []

        # Searches answer but return NO articles, so the first stage that runs out
        # of budget is the person walk itself — which is what this test asserts.
        async def _empty_search(q, *a, **k):
            return []

        llm = _FakeLLM()
        with patch.object(dr, "_web_search", new=_empty_search), \
             patch.object(dr, "_fetch_article_text", new=AsyncMock(return_value="body " * 40)), \
             patch.object(dr, "_analyse_article", new=AsyncMock(return_value={"facts": []})), \
             patch.object(dr, "_discover_and_investigate_people", new=_walk), \
             patch.object(dr, "_load_hypotheses", new=AsyncMock(return_value=[])), \
             patch.object(dr, "_save_hypotheses", new=AsyncMock(return_value=None)), \
             patch.object(dr, "_get_read_urls", new=AsyncMock(return_value=set())), \
             patch.object(dr, "_mark_read", new=AsyncMock(return_value=None)), \
             patch.object(dr, "_process_analysis", new=AsyncMock(return_value=(0, 0))), \
             patch.object(dr, "search_knowledge", return_value=""):
            out = await dr.investigate(llm, "Acme Defence Ltd due diligence",
                                       depth="quick", investigate_people=2,
                                       seed_people=["Jane Doe"], deadline_s=6.0)
        assert called["walk"] is False, "must not start a walk it cannot finish"
        assert out["partial"] is True and "person drill-down" in out["stopped_after"]
    asyncio.run(go())


def test_rf3018_orchestrator_passes_a_deadline_and_reports_honestly():
    """The DD-side contract: a deadline is handed down, and the data_gap describes
    what WAS gathered rather than asserting a false 'partial result'."""
    import inspect
    from aria_service.intel import dd_orchestrator as ddo
    src = module_source(ddo)
    assert "deadline_s=_dr_deadline" in src, "the bound must be handed to the engine"
    assert 'dr.get("partial")' in src, "the honest gap must key off the engine's own flag"
    # the gap text must not resurrect the false wording
    #
    # R-F3595 — this was `src[i:i + 1200]`, a FIXED BYTE WINDOW, and it broke without
    # anything it guards being touched: R-F3502 added an explanatory comment inside the
    # `partial` branch, pushing the gap text past 1200 characters. Measured — the same
    # assertion fails identically on the pre-R-F3502 source, so it had been red on
    # every full run since, for a reason unrelated to the property it exists to check.
    #
    # A guard that fires on unrelated edits gets muted, and then it protects nothing.
    # The property is "the honest wording lives in the partial branch, and the false
    # wording is gone" — so the window now ends where the BRANCH ends (the next
    # top-level def), not at an arbitrary offset.
    i = src.index('dr.get("partial")')
    _next_def = src.find("\ndef ", i)
    window = src[i:_next_def if _next_def != -1 else len(src)]
    assert "was bounded at" in window and "article(s) analysed" in window, (
        "the honest 'bounded at N / article(s) analysed' gap wording is no longer in "
        "the partial branch"
    )
    # and the false wording it replaced must not come back
    assert "partial result" not in window.lower() or "not a partial result" in window.lower()
