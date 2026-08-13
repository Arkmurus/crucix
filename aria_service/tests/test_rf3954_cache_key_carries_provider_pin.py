"""R-F3954 / C-45 — the LLM response cache could serve a DeepSeek answer to a
Claude-pinned DD run.

`LLMResponseCache` is the OUTERMOST wrapper (`main.py:2084` →
`app.state.llm_provider`), and DD pins Claude through the `provider_scope`
contextvar, which `FallbackProvider` only resolves at `fallback.py:1087` —
*inside* the cache. So the cache key was computed from prompt bytes alone:

    raw = f"{system_prompt}|{user_message}"          # resilience.py:773

Two calls with byte-identical prompts therefore collided across the
authorship boundary. Within the 1-hour TTL a DeepSeek-authored answer was
returned verbatim to a Claude-pinned DD run, tagged `model="cache"`.

R-F3034's whole rationale is that "an honest incomplete report beats a
DeepSeek-authored verdict wearing a Claude badge". The non-degrading pin in
`fallback.py` is sound; the cache sits outside it and undid it.

These tests fail on the pre-fix key and pass after it.
"""
from __future__ import annotations

import asyncio

import pytest

from aria_service.llm.fallback import provider_scope
from aria_service.llm.provider import LLMProvider, LLMResult
from aria_service.llm.resilience import LLMResponseCache


class _RecordingProvider(LLMProvider):
    """Inner provider that reports which pin it was called under."""

    def __init__(self) -> None:
        self.calls: list[dict] = []

    @property
    def name(self) -> str:  # type: ignore[override]
        return "recording"

    @property
    def is_configured(self) -> bool:
        return True

    async def complete(
        self,
        system_prompt: str,
        user_message: str,
        *,
        max_tokens: int = 4096,
        timeout: float = 60.0,
        prefer_provider: str = "",
        model: str = "",
    ) -> LLMResult:
        # Mirror what FallbackProvider does: an explicit argument wins, else
        # the context preference decides who actually serves.
        from aria_service.llm.fallback import get_preferred_provider
        effective = (prefer_provider or get_preferred_provider() or "deepseek").lower()
        self.calls.append({"effective": effective, "model": model})
        return LLMResult(
            text=f"answer-from-{effective}",
            model=effective,
            routed_via=effective,
            input_tokens=1,
            output_tokens=1,
        )


SYS = "You are a due-diligence analyst."
USR = "Summarise the compliance posture of Modirum Gespi Ltd."


def _fresh() -> tuple[LLMResponseCache, _RecordingProvider]:
    inner = _RecordingProvider()
    return LLMResponseCache(inner), inner


# ── the defect itself ────────────────────────────────────────────────────────

def test_context_pin_does_not_collide_with_unpinned_call():
    """A Claude-pinned DD must never be served a DeepSeek-authored answer."""
    cache, inner = _fresh()

    async def _run():
        # 1. A general (unpinned) chat turn warms the cache with DeepSeek.
        general = await cache.complete(SYS, USR)
        # 2. A DD run, pinned to Claude via the contextvar, asks the same thing.
        with provider_scope("anthropic"):
            dd = await cache.complete(SYS, USR)
        return general, dd

    general, dd = asyncio.run(_run())

    assert general.text == "answer-from-deepseek"
    # Pre-fix this returned the DeepSeek text tagged model="cache".
    assert dd.text == "answer-from-anthropic", (
        "the Claude-pinned call was served the DeepSeek answer from cache — "
        "the cache key does not carry the provider pin"
    )
    assert dd.model != "cache"
    assert len(inner.calls) == 2, "the pinned call must reach the provider chain"
    assert inner.calls[1]["effective"] == "anthropic"


def test_pin_then_unpinned_also_separated():
    """The collision is symmetric — a DD answer must not leak into general chat."""
    cache, inner = _fresh()

    async def _run():
        with provider_scope("anthropic"):
            dd = await cache.complete(SYS, USR)
        general = await cache.complete(SYS, USR)
        return dd, general

    dd, general = asyncio.run(_run())
    assert dd.text == "answer-from-anthropic"
    assert general.text == "answer-from-deepseek"
    assert len(inner.calls) == 2


def test_explicit_prefer_provider_argument_separates_keys():
    """The R-F1366 per-call pin (how the coder pins DeepSeek) must separate too."""
    cache, inner = _fresh()

    async def _run():
        a = await cache.complete(SYS, USR, prefer_provider="anthropic")
        b = await cache.complete(SYS, USR, prefer_provider="deepseek")
        return a, b

    a, b = asyncio.run(_run())
    assert a.text == "answer-from-anthropic"
    assert b.text == "answer-from-deepseek"
    assert len(inner.calls) == 2


def test_model_override_separates_keys():
    """R-F2769 routes a per-call model; two models are two different answers."""
    cache, inner = _fresh()

    async def _run():
        a = await cache.complete(SYS, USR, model="claude-opus-4-8")
        b = await cache.complete(SYS, USR, model="claude-sonnet-4-6")
        return a, b

    asyncio.run(_run())
    assert len(inner.calls) == 2, "a different model must not hit the same cache entry"
    assert inner.calls[0]["model"] != inner.calls[1]["model"]


# ── the cache must still be a cache ──────────────────────────────────────────

def test_identical_pin_still_hits_cache():
    """Separating keys must not disable caching — the same pin still hits."""
    cache, inner = _fresh()

    async def _run():
        with provider_scope("anthropic"):
            first = await cache.complete(SYS, USR)
            second = await cache.complete(SYS, USR)
        return first, second

    first, second = asyncio.run(_run())
    assert first.text == "answer-from-anthropic"
    assert second.model == "cache", "a repeat under the same pin must be served from cache"
    assert len(inner.calls) == 1, "the cache stopped caching"
    assert cache._hits == 1


def test_unpinned_repeat_still_hits_cache():
    cache, inner = _fresh()

    async def _run():
        await cache.complete(SYS, USR)
        return await cache.complete(SYS, USR)

    second = asyncio.run(_run())
    assert second.model == "cache"
    assert len(inner.calls) == 1


# ── the key contract, asserted directly ──────────────────────────────────────

def test_cache_key_is_sensitive_to_the_effective_pin():
    cache, _ = _fresh()
    unpinned = cache._cache_key(SYS, USR)
    with provider_scope("anthropic"):
        pinned = cache._cache_key(SYS, USR)
    assert unpinned != pinned, "the key ignores the context pin"


def test_cache_key_still_separates_on_prompt():
    """The original contract survives — different prompts, different keys."""
    cache, _ = _fresh()
    assert cache._cache_key(SYS, USR) != cache._cache_key(SYS, USR + "?")
    assert cache._cache_key(SYS, USR) != cache._cache_key(SYS + "!", USR)


def test_docstring_does_not_claim_a_temperature_key():
    """The class docstring claimed `sha256(prompt + temperature)`; it never was.

    A docstring that describes a key the code does not compute is how this
    defect survived review — the reader checks the docstring, not the bytes.
    """
    doc = (LLMResponseCache.__doc__ or "").lower()
    assert "sha256(prompt + temperature)" not in doc, (
        "the class docstring still describes a temperature-keyed cache"
    )
    assert "pin" in doc or "prefer_provider" in doc, (
        "the class docstring must state that the key carries the provider pin"
    )


def test_pin_is_normalised_so_case_does_not_fragment_the_cache():
    cache, inner = _fresh()

    async def _run():
        with provider_scope("Anthropic"):
            await cache.complete(SYS, USR)
        with provider_scope("anthropic"):
            return await cache.complete(SYS, USR)

    second = asyncio.run(_run())
    assert second.model == "cache", "provider_scope normalises case; the key must too"
    assert len(inner.calls) == 1
