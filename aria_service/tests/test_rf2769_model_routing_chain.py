"""R-F2769 — Phase 2 activation: model override threads through the FULL chain.

Proves the routed `model=` reaches the actual provider through every wrapper
layer the production stack wraps around the chain:
    LLMResponseCache -> RateLimitedProvider -> MeteredProvider -> FallbackChain -> provider
for BOTH complete() and stream(). Without end-to-end threading, routing would
be a silent no-op (the default model would serve every call).

Uses a fake provider that records the model it was called with — no network.
"""
from __future__ import annotations

import asyncio

from aria_service.llm.provider import LLMProvider, LLMResult
from aria_service.llm.fallback import FallbackProvider
from aria_service.llm.metered import MeteredProvider
from aria_service.llm.resilience import LLMRequestQueue, LLMResponseCache


class _RecorderProvider(LLMProvider):
    name = "recorder"

    def __init__(self):
        self.seen_model = "UNSET"
        self.seen_stream_model = "UNSET"

    @property
    def is_configured(self) -> bool:
        return True

    async def complete(self, system_prompt, user_message, *, max_tokens=4096,
                       timeout=60.0, model=None):
        self.seen_model = model
        return LLMResult(text="ok", model=model or "default", input_tokens=1, output_tokens=1)

    async def stream(self, system_prompt, user_message, *, max_tokens=4096,
                     timeout=120.0, on_done=None, model=None):
        self.seen_stream_model = model
        if on_done:
            on_done(LLMResult(text="ok", model=model or "default", input_tokens=1, output_tokens=1))
        yield "ok"


def _build_full_stack(rec):
    # Mirror main.py's wrap order: cache(ratelimited(metered(chain))).
    chain = FallbackProvider([rec])
    metered = MeteredProvider(chain)
    rl = LLMRequestQueue(metered)
    return LLMResponseCache(rl)


def test_model_threads_through_full_chain_complete():
    rec = _RecorderProvider()
    stack = _build_full_stack(rec)

    async def run():
        await stack.complete("sys", "hi-haiku", max_tokens=100, model="claude-haiku-4-5")
        return rec.seen_model
    seen = asyncio.run(run())
    assert seen == "claude-haiku-4-5", f"routed model must reach the provider, got {seen!r}"


def test_model_threads_through_full_chain_stream():
    rec = _RecorderProvider()
    stack = _build_full_stack(rec)

    async def run():
        async for _ in stack.stream("sys", "hi-opus", max_tokens=100, model="claude-opus-4-8"):
            pass
        return rec.seen_stream_model
    seen = asyncio.run(run())
    assert seen == "claude-opus-4-8", f"routed model must reach provider.stream, got {seen!r}"


def test_no_model_is_byte_identical_default_path():
    # The default (non-routing) path must pass NO model kwarg → provider sees None.
    rec = _RecorderProvider()
    stack = _build_full_stack(rec)

    async def run():
        await stack.complete("sys", "hi-default", max_tokens=100)  # no model=
        return rec.seen_model
    seen = asyncio.run(run())
    assert seen is None, f"default path must not inject a model, got {seen!r}"
