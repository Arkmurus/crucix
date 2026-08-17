"""R-F4112 (C-145) — CAPABILITY: unknown usage must not read as zero cost.

Live on aria-intel 2026-08-17, `/api/aria/cost/summary` over 24 h reported a
model literally named `fallback`:

    "by_model": {"deepseek-v4-flash": {...},
                 "fallback": {"calls": 142, "tokens": 0, "cost_usd": 0.0}}

142 of 1,000 calls — 14.2% of all LLM traffic — presented on the cost panel as
a model that costs nothing. It is not a free path. One of those records, pulled
whole:

    {"model":"fallback","provider":"fallback","feature":"autonomous_engine",
     "input_tokens":0,"output_tokens":0,"cost_usd":0.0,
     "latency_ms":23030,"success":true,"error":""}

Twenty-three seconds of work, recorded as free.

THE MECHANISM. `MeteredProvider._record_cost` derives the model from the
provider's *name* only when `result is None`, and the one site that passes
`result=None` with `success=True` is `metered.stream()` — reached when the
inner stream never fires `on_done`. `FallbackProvider.name` is the literal
string `"fallback"`, so a call with no usage information acquired a
plausible-looking model name and a $0 price tag.

THE ROOT CAUSE is one line further down: `provider.py.stream()` fires `on_done`
*after* its `yield`. An async generator whose consumer stops early never runs
the code after the yield, so the usage callback is skipped — while the tokens
have already been spent by the `complete()` above it.

THE FIX IS NOT TO ESTIMATE. Inventing a token count is a guess wearing a
measurement's clothes. A call whose usage we could not read must be
DISTINGUISHABLE from one that genuinely cost nothing, and the count of such
calls must be visible — otherwise the cap silently under-counts and the panel
says everything is fine.

Run: python -m pytest aria_service/tests/test_rf4101_metered_stream_usage_unknown.py -v
"""
from __future__ import annotations

import asyncio

import pytest


# ══════════════════════════════════════════════════════════════════════
# 1. ROOT CAUSE — on_done must fire even if the consumer walks away
# ══════════════════════════════════════════════════════════════════════

def test_on_done_fires_when_the_consumer_abandons_the_stream():
    """The tokens were spent by complete() before the first yield. A consumer
    that stops reading does not un-spend them."""
    from aria_service.llm.provider import LLMProvider, LLMResult

    class _P(LLMProvider):
        name = "stub"

        @property
        def is_configured(self):
            return True

        async def complete(self, system_prompt, user_message, *, max_tokens=4096,
                           timeout=120.0, model=None):
            return LLMResult(text="hello world", model="stub-1",
                             input_tokens=11, output_tokens=7)

    seen = []

    async def _drive():
        gen = _P().stream("s", "u", on_done=seen.append)
        async for _chunk in gen:
            break                      # walk away after the first chunk
        await gen.aclose()

    asyncio.run(_drive())

    assert seen, (
        "on_done never fired for an abandoned stream, so the usage of a call "
        "that already happened was silently discarded"
    )
    assert seen[0].input_tokens == 11


def test_on_done_still_fires_on_a_fully_consumed_stream():
    from aria_service.llm.provider import LLMProvider, LLMResult

    class _P(LLMProvider):
        name = "stub"

        @property
        def is_configured(self):
            return True

        async def complete(self, system_prompt, user_message, *, max_tokens=4096,
                           timeout=120.0, model=None):
            return LLMResult(text="t", model="stub-1", input_tokens=3, output_tokens=1)

    seen = []

    async def _drive():
        async for _ in _P().stream("s", "u", on_done=seen.append):
            pass

    asyncio.run(_drive())
    assert len(seen) == 1, "on_done must fire exactly once, not twice"


# ══════════════════════════════════════════════════════════════════════
# 2. THE HONESTY PROPERTY — unknown is not zero
# ══════════════════════════════════════════════════════════════════════

def test_a_usage_less_record_is_flagged_not_priced_as_free(monkeypatch):
    from aria_service.llm import metered

    captured = {}

    class _Inner:
        name = "fallback"
        is_configured = True

    mp = metered.MeteredProvider(_Inner())

    async def _fake_record_call(**kw):
        captured.update(kw)
        return {}

    from aria_service.intel import cost_tracker
    monkeypatch.setattr(cost_tracker, "record_call", _fake_record_call)

    async def _go():
        mp._record_cost(0.0, None, True, "", "autonomous_engine")
        for _ in range(20):
            await asyncio.sleep(0)

    asyncio.run(_go())

    assert captured, "no cost record was written at all"
    assert captured.get("usage_unknown") is True, (
        "a call with no usage information was recorded indistinguishably from "
        "one that genuinely cost nothing"
    )
    assert captured.get("model") != "fallback", (
        "the provider's NAME was used as a model name, so 142 usage-less calls "
        "appeared on the cost panel as a $0 model called 'fallback'"
    )


def test_a_normal_record_is_not_flagged(monkeypatch):
    from aria_service.llm import metered
    from aria_service.llm.provider import LLMResult

    captured = {}

    class _Inner:
        name = "deepseek"
        is_configured = True

    mp = metered.MeteredProvider(_Inner())

    async def _fake_record_call(**kw):
        captured.update(kw)
        return {}

    from aria_service.intel import cost_tracker
    monkeypatch.setattr(cost_tracker, "record_call", _fake_record_call)

    res = LLMResult(text="t", model="deepseek-v4-flash",
                    input_tokens=100, output_tokens=50)

    async def _go():
        mp._record_cost(0.0, res, True, "", "chat")
        for _ in range(20):
            await asyncio.sleep(0)

    asyncio.run(_go())

    assert captured.get("model") == "deepseek-v4-flash"
    assert not captured.get("usage_unknown"), "a measured call must not be flagged"


# ══════════════════════════════════════════════════════════════════════
# 3. THE SURFACE — blind spots must be countable
# ══════════════════════════════════════════════════════════════════════

def test_the_record_persists_the_flag():
    from ._source_probe import function_source
    from aria_service.intel import cost_tracker

    src = function_source(cost_tracker, "record_call")
    assert "usage_unknown" in src, (
        "the flag is computed but never stored, so nothing downstream can "
        "count how blind the meter is"
    )


def test_the_summary_counts_unmetered_calls():
    from ._source_probe import function_source
    from aria_service.intel import cost_tracker

    src = function_source(cost_tracker, "get_cost_summary")
    assert "usage_unknown" in src, (
        "the cap is fed by these records, so the operator must be able to see "
        "how many calls the meter could not read — otherwise a meter reading "
        "low is indistinguishable from a quiet month (§17)"
    )
