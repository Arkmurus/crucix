"""R-F2769 / R-F2949 — a routed model threads through the FULL wrapper chain,
subject to R-F2933's provider-specific-model safety guard.

Proves the routed `model=` reaches the actual provider through every wrapper
layer the production stack wraps around the chain:
    LLMResponseCache -> RateLimitedProvider -> MeteredProvider -> FallbackChain -> provider
for BOTH complete() and stream(). Without end-to-end threading, routing would
be a silent no-op (the default model would serve every call).

R-F2949 (2026-07-23): the original tests asserted a `claude-*` model threads
through to ANY provider. R-F2933 (commit c8dc74c5) deliberately removed that:
a `claude-*` model id is provider-SPECIFIC and must NOT leak to a non-Anthropic
provider — live 2026-07-23 a routed `claude-opus-4-8` leaked onto DeepSeek on
fallback and 400-cooled it mid-DD. So the correct contract is:
  - a claude model threads through to the ANTHROPIC provider (the real DD case);
  - a claude model is STRIPPED for a non-Anthropic provider (it uses its own
    default) — this is the 400-storm guard;
  - a non-claude (provider-agnostic) model threads through to ANY provider;
  - no model → the provider sees None (default path, byte-identical).

Uses fake providers that record the model they were called with — no network.
"""
from __future__ import annotations

import asyncio

from aria_service.llm.provider import LLMProvider, LLMResult
from aria_service.llm.fallback import FallbackProvider, provider_scope
from aria_service.llm.metered import MeteredProvider
from aria_service.llm.resilience import LLMRequestQueue, LLMResponseCache


class _RecorderProvider(LLMProvider):
    """Records the model it was called with. `name` is configurable so we can
    exercise both the anthropic (threads claude through) and non-anthropic
    (strips claude) branches of the R-F2933 guard."""

    def __init__(self, name="recorder"):
        self.name = name
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


def _drive_complete(rec, **kw):
    stack = _build_full_stack(rec)
    async def run():
        await stack.complete("sys", kw.pop("_um", "hi"), max_tokens=100, **kw)
        return rec.seen_model
    return asyncio.run(run())


def _drive_stream(rec, **kw):
    stack = _build_full_stack(rec)
    async def run():
        async for _ in stack.stream("sys", kw.pop("_um", "hi"), max_tokens=100, **kw):
            pass
        return rec.seen_stream_model
    return asyncio.run(run())


# ── The real DD case: claude model → anthropic provider → threads through ──
# Anthropic is preference-only (R-F2922: never in the default chain, so Claude
# is never a silent fallback), so DD reaches it via provider_scope("anthropic")
# — exactly what dd_orchestrator pins. Driving it any other way ("all providers
# failed") is itself proof R-F2922 keeps Claude out of the default path.
def test_claude_model_threads_to_anthropic_provider_complete():
    rec = _RecorderProvider(name="anthropic")
    with provider_scope("anthropic"):
        assert _drive_complete(rec, model="claude-opus-4-8", _um="hi-opus") == "claude-opus-4-8", \
            "DD's routed claude model must reach the anthropic provider"


def test_stream_never_reaches_anthropic_even_when_scoped():
    """R-F2922 safety: streaming is the CHAT path and must NEVER be served by
    Claude, even under provider_scope('anthropic'). DD uses complete(), not
    stream(). With a realistic [deepseek, anthropic] chain the stream must land
    on deepseek and leave anthropic untouched — chat cannot leak to Claude."""
    deepseek = _RecorderProvider(name="deepseek")
    anthropic = _RecorderProvider(name="anthropic")
    chain = FallbackProvider([deepseek, anthropic])
    stack = LLMResponseCache(LLMRequestQueue(MeteredProvider(chain)))

    async def run():
        with provider_scope("anthropic"):
            async for _ in stack.stream("sys", "hi", max_tokens=100, model="claude-opus-4-8"):
                pass
    asyncio.run(run())
    assert anthropic.seen_stream_model == "UNSET", "chat stream must NEVER reach Claude"
    assert deepseek.seen_stream_model is None, "stream must serve from deepseek with its own model"


# ── R-F2933 guard: claude model → non-anthropic provider → STRIPPED ──
def test_claude_model_stripped_for_non_anthropic_provider_complete():
    rec = _RecorderProvider(name="deepseek")
    assert _drive_complete(rec, model="claude-opus-4-8", _um="hi") is None, \
        "a claude model must NOT leak to DeepSeek (R-F2933 400-storm guard)"


def test_claude_model_stripped_for_non_anthropic_provider_stream():
    rec = _RecorderProvider(name="deepseek")
    assert _drive_stream(rec, model="claude-haiku-4-5", _um="hi") is None, \
        "a claude model must NOT leak to DeepSeek on stream (R-F2933 guard)"


# ── General routing still works: a provider-agnostic model threads anywhere ──
def test_non_claude_model_threads_to_any_provider_complete():
    rec = _RecorderProvider(name="deepseek")
    assert _drive_complete(rec, model="deepseek-reasoner", _um="hi") == "deepseek-reasoner", \
        "a non-claude routed model must still thread through end-to-end"


def test_non_claude_model_threads_to_any_provider_stream():
    rec = _RecorderProvider(name="deepseek")
    assert _drive_stream(rec, model="deepseek-reasoner", _um="hi") == "deepseek-reasoner", \
        "a non-claude routed model must still thread through on stream"


# ── Default path: no model → provider sees None (unchanged) ──
def test_no_model_is_byte_identical_default_path():
    rec = _RecorderProvider(name="deepseek")
    assert _drive_complete(rec, _um="hi-default") is None, \
        "default path must not inject a model"
