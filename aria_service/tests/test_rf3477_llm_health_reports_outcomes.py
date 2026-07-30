"""R-F3477 — the LLM health surface reported configuration, not outcomes.

Live 2026-07-30. In ONE five-minute window aria-intel logged:

    14 x  Article analysis failed: [fallback] all LLM providers failed — try again in a minute
          Code generation LLM call failed: [fallback] all LLM providers failed
          [Self-Improve] Diagnosis failed for autonomous/engine.py: all LLM providers failed

A concurrent probe of /health returned:

    "status": "operational", "resilient": true, "active_providers": ["anthropic"]

Both cannot be true. `resilient` is computed as ``len(active) > 0`` where a
provider is "active" merely because its cooldown timestamp has passed
(llm/fallback.py:743). That is CHAIN MEMBERSHIP. It cannot observe that every
recent call through that same provider failed, so it reports a healthy chain
during a total outage. §14 says a cooling provider is the chain working as
designed — true, and unchanged here. But "no provider is currently cooling" is
not evidence that anything works.

Second defect, same root, and it is what sent the 15-cycle DD down a false path.
`/health` also showed:

    "llm_fallback_stats": {"size":0, "hits":0, "misses":491, "hit_rate":0.0}

A cache with 491 misses and size 0 reads as "never written", and the DD recorded
it as a standing cost leak. It was not. ``_set_cached`` and ``_get_cached``
(llm/resilience.py:773-790) are a correct LRU+TTL. The real story: ``_misses``
is incremented BEFORE the call, and during the outage every ``complete()`` raised,
so the store line was never reached. The cache had nothing to store — and a
FAILED call was being counted as a cache miss, which is not a cache event at all.

So both numbers on that surface described configuration and error volume rather
than what actually happened.
"""
from __future__ import annotations

import time

import pytest


# ── resilient must follow outcomes ──────────────────────────────────────────

class _FakeProvider:
    def __init__(self, name: str) -> None:
        self.name = name
        self.is_configured = True


def _chain(monkeypatch, names=("deepseek", "anthropic")):
    from aria_service.llm import fallback as fb
    chain = fb.FallbackProvider.__new__(fb.FallbackProvider)
    chain.providers = [_FakeProvider(n) for n in names]
    chain._stats = {n: {"calls": 0, "failures": 0, "cooldown_until": 0} for n in names}
    if hasattr(fb.FallbackProvider, "_reset_chain_outcome"):
        chain._reset_chain_outcome()
    return chain


class TestResilientFollowsOutcomes:

    def test_healthy_chain_is_resilient(self, monkeypatch):
        chain = _chain(monkeypatch)
        assert chain.get_health()["resilient"] is True

    def test_chain_is_not_resilient_after_total_exhaustion(self, monkeypatch):
        """The live case: no provider cooling, yet every call failed."""
        chain = _chain(monkeypatch)
        chain._record_chain_exhausted()
        health = chain.get_health()
        assert health["resilient"] is False, (
            "reported resilient while the last chain attempt exhausted every provider"
        )
        assert health.get("last_exhaustion_age_s") is not None

    def test_a_success_clears_the_exhaustion(self, monkeypatch):
        chain = _chain(monkeypatch)
        chain._record_chain_exhausted()
        assert chain.get_health()["resilient"] is False
        chain._record_chain_success()
        assert chain.get_health()["resilient"] is True, (
            "a successful call must restore the healthy signal immediately"
        )

    def test_exhaustion_expires_so_the_chain_can_recover_unattended(self, monkeypatch):
        """Fail-safe: if nothing calls the chain again, the flag must not latch
        forever — otherwise one blip marks the chain dead permanently."""
        from aria_service.llm import fallback as fb
        chain = _chain(monkeypatch)
        monkeypatch.setattr(fb, "_CHAIN_EXHAUSTION_TTL_S", 0.05)
        chain._record_chain_exhausted()
        assert chain.get_health()["resilient"] is False
        time.sleep(0.08)
        assert chain.get_health()["resilient"] is True

    def test_cooling_alone_still_means_resilient(self, monkeypatch):
        """§14 unchanged: a cooling provider is the fallback chain WORKING."""
        chain = _chain(monkeypatch)
        chain._stats["deepseek"]["cooldown_until"] = time.time() + 3600
        health = chain.get_health()
        assert health["resilient"] is True
        assert health["cooling_providers"][0]["name"] == "deepseek"


# ── the cache miss counter must count cache events ──────────────────────────

class TestMissCounterCountsCacheEvents:

    @pytest.mark.asyncio
    async def test_a_failed_call_is_not_counted_as_a_cache_miss(self):
        """491 'misses' with size 0 read as a broken cache. They were errors."""
        from aria_service.llm import resilience as res

        class _Boom:
            async def complete(self, *_a, **_kw):
                raise RuntimeError("all LLM providers failed")

        cache = res.LLMResponseCache.__new__(res.LLMResponseCache)
        cache._inner = _Boom()
        cache._cache = __import__("collections").OrderedDict()
        cache._max_size = 10
        cache._ttl = 3600
        cache._hits = 0
        cache._misses = 0
        cache._errors = 0

        for _ in range(3):
            with pytest.raises(RuntimeError):
                await cache.complete("sys", "msg")

        stats = cache.get_stats()
        assert stats["misses"] == 0, (
            f"failed calls counted as cache misses: {stats}"
        )
        assert stats["errors"] == 3, f"errors not surfaced separately: {stats}"

    @pytest.mark.asyncio
    async def test_a_served_call_is_a_miss_then_a_hit(self):
        from aria_service.llm import resilience as res

        class _Ok:
            async def complete(self, *_a, **_kw):
                return res.LLMResult(text="a real answer, long enough to cache",
                                     model="m", routed_via="p",
                                     input_tokens=1, output_tokens=1)

        cache = res.LLMResponseCache.__new__(res.LLMResponseCache)
        cache._inner = _Ok()
        cache._cache = __import__("collections").OrderedDict()
        cache._max_size = 10
        cache._ttl = 3600
        cache._hits = 0
        cache._misses = 0
        cache._errors = 0

        first = await cache.complete("sys", "msg")
        second = await cache.complete("sys", "msg")
        stats = cache.get_stats()
        assert first.routed_via != "cache"
        assert second.routed_via == "cache"
        assert stats["misses"] == 1 and stats["hits"] == 1, stats
        assert stats["size"] == 1, stats
        assert stats["hit_rate"] == 0.5, stats
