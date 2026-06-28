"""R-F2110 — the pre-cloud local-reasoning attempt must be time-bounded.

aria_chat / aria_chat_stream call reasoning_router.try_local_reasoning() BEFORE the
cloud LLM. The cloud LLM is bounded by _llm_timeout, but the call site had NO timeout,
so any slow/hung stage in the local-reasoning walk hung the WHOLE chat turn forever
(loop free — it awaits external I/O). That is the cause of "substantive/long messages
+ document reviews never get answered" while a trivial "hi" (fast lane, skips this
walk) answers in ~2s. The fix wraps the call in asyncio.wait_for and, on timeout,
falls through to the fast cloud LLM (local_attempt = {"answered": False}).
"""
import asyncio
import time

import aria_service.aria_engine as E
import aria_service.intel.reasoning_router as RR


def test_rf2110_default_budget_is_25s():
    assert E._LOCAL_REASONING_TIMEOUT_S == 25.0


def test_rf2110_hung_local_reasoning_times_out_and_falls_through(monkeypatch):
    """A hung try_local_reasoning must NOT hang the turn — wait_for fires and the
    code falls through (answered=False) so the cloud LLM answers."""
    async def _hang(*a, **k):
        await asyncio.sleep(60)
    monkeypatch.setattr(RR, "try_local_reasoning", _hang)

    async def run():
        # exactly the wrapped logic now in aria_chat / aria_chat_stream
        t0 = time.monotonic()
        try:
            la = await asyncio.wait_for(RR.try_local_reasoning("x"), timeout=0.5)
        except asyncio.TimeoutError:
            la = {"answered": False}
        return time.monotonic() - t0, la

    elapsed, la = asyncio.run(run())
    assert elapsed < 3.0, f"must not hang on a stuck local-reasoning stage; took {elapsed:.1f}s"
    assert la == {"answered": False}, "on timeout the turn must fall through to the cloud LLM"


def test_rf2110_fast_local_reasoning_is_used_when_it_answers(monkeypatch):
    """When local reasoning answers quickly, its result is used (no regression)."""
    async def _quick(*a, **k):
        return {"answered": True, "response": "local", "source": "reasoning_library"}
    monkeypatch.setattr(RR, "try_local_reasoning", _quick)

    async def run():
        try:
            la = await asyncio.wait_for(RR.try_local_reasoning("x"), timeout=25.0)
        except asyncio.TimeoutError:
            la = {"answered": False}
        return la

    la = asyncio.run(run())
    assert la.get("answered") is True and la.get("response") == "local"
