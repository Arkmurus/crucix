"""R-F2877 — RateLimitedProvider.complete must forward the per-call `model=` kwarg.

LIVE SYMPTOM (2026-07-22, 10-cycle monitor): researcher article analysis failed on
EVERY research cycle with:
    Article analysis failed: RateLimitedProvider.complete() got an unexpected
    keyword argument 'model'

R-F2769 routed article fact-extraction to a cheap Claude model by adding `model=` to
the researcher's `llm.complete()` call AND to the underlying providers
(fallback/metered/anthropic). But the `RateLimitedProvider` WRAPPER — which wraps the
live LLM in main.py:1828, so it sits between the researcher and the inner provider —
was never updated. The kwarg died at the wrapper with a TypeError, so article
fact-extraction (the bulk of a DD run's LLM value) was dead whenever the LLM was
rate-limited, i.e. in production.

The fix mirrors the existing `prefer_provider` forwarding (R-F1366): accept `model`
and forward it to the inner ONLY when set, so a bare inner that doesn't accept the
kwarg is never broken by an empty value.
"""
import asyncio
from types import SimpleNamespace

from aria_service.llm.rate_limiter import RateLimitedProvider


class _Capture:
    """Inner provider that records the kwargs complete() was called with."""
    name = "capture"
    is_configured = True

    def __init__(self):
        self.seen: dict = {}

    async def complete(self, system_prompt, user_message, *,
                       max_tokens=4096, timeout=60.0, **kwargs):
        self.seen = dict(kwargs)
        return SimpleNamespace(text="ok", model="m", input_tokens=1,
                               output_tokens=1, routed_via="")


def test_rf2877_model_kwarg_does_not_raise():
    """CAPABILITY: the exact live symptom — model= must not raise TypeError."""
    async def go():
        inner = _Capture()
        w = RateLimitedProvider(inner)
        await w.complete("sys", "user", model="claude-haiku-4-5-20251001")
    asyncio.run(go())   # was: TypeError unexpected keyword argument 'model'


def test_rf2877_forwards_model_when_set():
    """A set model must reach the inner provider (so R-F2769 routing actually works)."""
    async def go():
        inner = _Capture()
        w = RateLimitedProvider(inner)
        await w.complete("sys", "user", model="claude-haiku-4-5-20251001")
        return inner.seen
    seen = asyncio.run(go())
    assert seen.get("model") == "claude-haiku-4-5-20251001"


def test_rf2877_omits_empty_model():
    """Empty model must NOT be forwarded — same R-F1366 shape as prefer_provider:
    a single-provider inner may not accept the kwarg, so only forward when set."""
    async def go():
        inner = _Capture()
        w = RateLimitedProvider(inner)
        await w.complete("sys", "user")
        return inner.seen
    seen = asyncio.run(go())
    assert "model" not in seen
