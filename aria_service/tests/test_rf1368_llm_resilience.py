"""
R-F1368: LLM resilience layer — unit + capability tests.

Tests three components:
  1. LLMHealthChecker — background health probe for ARIA-LLM
  2. LLMRequestQueue — semaphore-based concurrency limiter
  3. LLMResponseCache — LRU cache for repeated queries

Each test drives the actual component (not a mock helper) and asserts
the user-visible outcome.
"""
from __future__ import annotations

import asyncio
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from aria_service.llm.provider import LLMProvider, LLMResult, ProviderError


# ═══════════════════════════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════════════════════════

class _FakeProvider(LLMProvider):
    """Minimal LLMProvider that returns a canned response."""
    name = "fake"

    def __init__(self, response: str = "fake response", fail: bool = False):
        self._response = response
        self._fail = fail
        self._call_count = 0

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
    ) -> LLMResult:
        self._call_count += 1
        if self._fail:
            raise ProviderError("fake", "simulated failure", kind="server")
        return LLMResult(text=self._response, model="fake", input_tokens=10, output_tokens=20)

    async def stream(
        self,
        system_prompt: str,
        user_message: str,
        *,
        max_tokens: int = 4096,
        timeout: float = 120.0,
        on_done=None,
    ):
        self._call_count += 1
        if self._fail:
            raise ProviderError("fake", "simulated failure", kind="server")
        for char in self._response:
            yield char
        if on_done:
            on_done(LLMResult(text=self._response, model="fake"))


class _SlowProvider(LLMProvider):
    """Provider that delays before responding — for queue tests."""
    name = "slow"

    def __init__(self, delay: float = 0.5, response: str = "slow response"):
        self._delay = delay
        self._response = response
        self._call_count = 0

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
    ) -> LLMResult:
        self._call_count += 1
        await asyncio.sleep(self._delay)
        return LLMResult(text=self._response, model="slow")

    async def stream(
        self,
        system_prompt: str,
        user_message: str,
        *,
        max_tokens: int = 4096,
        timeout: float = 120.0,
        on_done=None,
    ):
        self._call_count += 1
        await asyncio.sleep(self._delay)
        for char in self._response:
            yield char
        if on_done:
            on_done(LLMResult(text=self._response, model="slow"))


# ═══════════════════════════════════════════════════════════════════════════════
# 1. LLMResponseCache tests
# ═══════════════════════════════════════════════════════════════════════════════

class TestLLMResponseCache:
    """Unit + capability tests for the LRU response cache."""

    def test_cache_hit_returns_cached_response(self):
        """Same prompt twice — second call returns cached, doesn't hit inner."""
        from aria_service.llm.resilience import LLMResponseCache

        inner = _FakeProvider(response="hello world")
        cache = LLMResponseCache(inner, max_size=10, ttl=3600)

        # First call — cache miss, goes to inner
        result1 = asyncio.run(cache.complete("sys", "hello"))
        assert result1.text == "hello world"
        assert inner._call_count == 1

        # Second call — cache hit, skips inner
        result2 = asyncio.run(cache.complete("sys", "hello"))
        assert result2.text == "hello world"
        assert result2.routed_via == "cache"
        assert inner._call_count == 1  # not incremented

    def test_cache_miss_different_prompts(self):
        """Different prompts produce different cache entries."""
        from aria_service.llm.resilience import LLMResponseCache

        inner = _FakeProvider(response="response")
        cache = LLMResponseCache(inner, max_size=10, ttl=3600)

        r1 = asyncio.run(cache.complete("sys", "prompt A"))
        r2 = asyncio.run(cache.complete("sys", "prompt B"))
        assert inner._call_count == 2  # both were misses

    def test_cache_eviction_lru(self):
        """Cache evicts oldest entries when max_size is exceeded."""
        from aria_service.llm.resilience import LLMResponseCache

        inner = _FakeProvider(response="long enough data to cache")
        cache = LLMResponseCache(inner, max_size=3, ttl=3600)

        # Fill cache with 3 entries
        asyncio.run(cache.complete("sys", "A"))
        asyncio.run(cache.complete("sys", "B"))
        asyncio.run(cache.complete("sys", "C"))
        assert inner._call_count == 3

        # Access A to make it most recently used
        asyncio.run(cache.complete("sys", "A"))
        assert inner._call_count == 3  # still cached

        # Add D — should evict B (oldest)
        asyncio.run(cache.complete("sys", "D"))
        assert inner._call_count == 4  # D was a miss

        # B should now be evicted
        asyncio.run(cache.complete("sys", "B"))
        assert inner._call_count == 5  # B was evicted, miss again

    def test_cache_ttl_expiry(self):
        """Cache entries expire after TTL."""
        from aria_service.llm.resilience import LLMResponseCache

        inner = _FakeProvider(response="long enough data to cache")
        cache = LLMResponseCache(inner, max_size=10, ttl=1)  # 1 second TTL

        asyncio.run(cache.complete("sys", "hello"))
        assert inner._call_count == 1

        # Immediate re-query — cache hit
        asyncio.run(cache.complete("sys", "hello"))
        assert inner._call_count == 1

        # Wait for TTL to expire
        time.sleep(1.1)

        # Should be a miss now
        asyncio.run(cache.complete("sys", "hello"))
        assert inner._call_count == 2

    def test_cache_clear(self):
        """Clear resets cache and stats."""
        from aria_service.llm.resilience import LLMResponseCache

        inner = _FakeProvider(response="long enough data to cache")
        cache = LLMResponseCache(inner, max_size=10, ttl=3600)

        asyncio.run(cache.complete("sys", "hello"))
        assert cache.get_stats()["hits"] == 0
        assert cache.get_stats()["misses"] == 1

        n = cache.clear()
        assert n == 1
        assert cache.get_stats()["hits"] == 0
        assert cache.get_stats()["misses"] == 0

    def test_cache_stats(self):
        """get_stats returns correct hit rate."""
        from aria_service.llm.resilience import LLMResponseCache

        inner = _FakeProvider(response="long enough data to cache")
        cache = LLMResponseCache(inner, max_size=10, ttl=3600)

        asyncio.run(cache.complete("sys", "A"))  # miss
        asyncio.run(cache.complete("sys", "A"))  # hit
        asyncio.run(cache.complete("sys", "B"))  # miss

        stats = cache.get_stats()
        assert stats["hits"] == 1
        assert stats["misses"] == 2
        assert stats["hit_rate"] == pytest.approx(0.333, abs=0.01)
        assert stats["size"] == 2

    def test_cache_does_not_cache_short_responses(self):
        """Responses shorter than 10 chars are not cached."""
        from aria_service.llm.resilience import LLMResponseCache

        inner = _FakeProvider(response="short")
        cache = LLMResponseCache(inner, max_size=10, ttl=3600)

        asyncio.run(cache.complete("sys", "hello"))
        assert inner._call_count == 1

        # Second call — should be a miss because short response wasn't cached
        asyncio.run(cache.complete("sys", "hello"))
        assert inner._call_count == 2

    def test_cache_name_and_configured(self):
        """Cache wrapper exposes inner provider's name and is_configured."""
        from aria_service.llm.resilience import LLMResponseCache

        inner = _FakeProvider()
        cache = LLMResponseCache(inner)
        assert cache.name == "fake"
        assert cache.is_configured is True


# ═══════════════════════════════════════════════════════════════════════════════
# 2. LLMRequestQueue tests
# ═══════════════════════════════════════════════════════════════════════════════

class TestLLMRequestQueue:
    """Unit + capability tests for the request queue."""

    def test_queue_dispatches_sequentially(self):
        """Queue dispatches calls through to inner provider."""
        from aria_service.llm.resilience import LLMRequestQueue

        inner = _FakeProvider(response="queued response")
        queue = LLMRequestQueue(inner, max_concurrent=5, max_queue_size=10)

        result = asyncio.run(queue.complete("sys", "hello"))
        assert result.text == "queued response"
        assert inner._call_count == 1

    def test_queue_limits_concurrency(self):
        """Queue limits concurrent calls to max_concurrent."""
        from aria_service.llm.resilience import LLMRequestQueue

        inner = _SlowProvider(delay=0.3)
        queue = LLMRequestQueue(inner, max_concurrent=2, max_queue_size=10)

        async def _run():
            # Fire 4 concurrent calls — only 2 should run at once
            tasks = [
                asyncio.create_task(queue.complete("sys", f"req{i}"))
                for i in range(4)
            ]
            results = await asyncio.gather(*tasks, return_exceptions=True)
            successes = [r for r in results if isinstance(r, LLMResult)]
            return len(successes)

        n = asyncio.run(_run())
        assert n == 4  # all should succeed eventually
        assert inner._call_count == 4

    def test_queue_full_raises(self):
        """Queue raises QueueFullError when saturated."""
        from aria_service.llm.resilience import LLMRequestQueue, QueueFullError

        inner = _SlowProvider(delay=1.0)
        queue = LLMRequestQueue(inner, max_concurrent=1, max_queue_size=2)

        async def _run():
            # Start 3 calls — 1 runs, 2 queue, 3rd should be dropped
            tasks = [
                asyncio.create_task(queue.complete("sys", f"req{i}"))
                for i in range(4)
            ]
            results = await asyncio.gather(*tasks, return_exceptions=True)
            errors = [r for r in results if isinstance(r, ProviderError)]
            return len(errors)

        n = asyncio.run(_run())
        assert n >= 1  # at least one was dropped

    def test_queue_stats(self):
        """get_stats returns correct counters."""
        from aria_service.llm.resilience import LLMRequestQueue

        inner = _FakeProvider()
        queue = LLMRequestQueue(inner, max_concurrent=5, max_queue_size=10)

        asyncio.run(queue.complete("sys", "hello"))
        stats = queue.get_stats()
        assert stats["total_dispatched"] == 1
        assert stats["max_concurrent"] == 5
        assert stats["max_queue_size"] == 10

    def test_queue_name_and_configured(self):
        """Queue wrapper exposes inner provider's name and is_configured."""
        from aria_service.llm.resilience import LLMRequestQueue

        inner = _FakeProvider()
        queue = LLMRequestQueue(inner)
        assert queue.name == "fake"
        assert queue.is_configured is True

    def test_queue_stream(self):
        """Queue dispatches streaming calls through to inner."""
        from aria_service.llm.resilience import LLMRequestQueue

        inner = _FakeProvider(response="stream data")
        queue = LLMRequestQueue(inner, max_concurrent=5, max_queue_size=10)

        async def _run():
            chunks = []
            async for chunk in queue.stream("sys", "hello"):
                chunks.append(chunk)
            return "".join(chunks)

        text = asyncio.run(_run())
        assert text == "stream data"
        assert inner._call_count == 1


# ═══════════════════════════════════════════════════════════════════════════════
# 3. LLMHealthChecker tests
# ═══════════════════════════════════════════════════════════════════════════════

class TestLLMHealthChecker:
    """Unit + capability tests for the health checker."""

    def test_disabled_when_no_endpoint(self):
        """Health checker is disabled when ARIA_LLM_URL is not set."""
        from aria_service.llm.resilience import LLMHealthChecker

        hc = LLMHealthChecker(endpoint="")
        assert hc.is_available() is False
        status = hc.get_status()
        assert status["enabled"] is False

    def test_initial_state_available(self):
        """Before first probe, health checker assumes available."""
        from aria_service.llm.resilience import LLMHealthChecker

        hc = LLMHealthChecker(endpoint="http://localhost:9999")
        # Before any probe, no breaker yet — assume available
        assert hc.is_available() is True
        status = hc.get_status()
        assert status["enabled"] is True

    def test_probe_classify_status(self):
        """_classify_status maps HTTP codes correctly."""
        from aria_service.llm.resilience import LLMHealthChecker

        hc = LLMHealthChecker(endpoint="http://localhost:9999")
        assert hc._classify_status(401) == "auth"
        assert hc._classify_status(403) == "auth"
        assert hc._classify_status(402) == "billing"
        assert hc._classify_status(429) == "rate_limit"
        assert hc._classify_status(500) == "server"
        assert hc._classify_status(503) == "server"
        assert hc._classify_status(200) == "server"  # not a failure code

    def test_get_status_shape(self):
        """get_status returns expected fields."""
        from aria_service.llm.resilience import LLMHealthChecker

        hc = LLMHealthChecker(endpoint="http://localhost:9999")
        status = hc.get_status()
        assert "enabled" in status
        assert "endpoint" in status
        assert "status" in status
        assert "consecutive_failures" in status
        assert "last_latency_ms" in status
        assert "breaker" in status


# ═══════════════════════════════════════════════════════════════════════════════
# 4. Integration: cache + queue + health checker together
# ═══════════════════════════════════════════════════════════════════════════════

class TestResilienceIntegration:
    """Tests that the three components compose correctly."""

    def test_cache_wraps_queue_wraps_provider(self):
        """Full chain: cache -> queue -> provider works end-to-end."""
        from aria_service.llm.resilience import LLMRequestQueue, LLMResponseCache

        inner = _FakeProvider(response="integrated response data")
        queue = LLMRequestQueue(inner, max_concurrent=5)
        cache = LLMResponseCache(queue, max_size=10, ttl=3600)

        # First call — cache miss, goes through queue to provider
        result = asyncio.run(cache.complete("sys", "hello"))
        assert result.text == "integrated response data"
        assert inner._call_count == 1

        # Second call — cache hit, skips both queue and provider
        result = asyncio.run(cache.complete("sys", "hello"))
        assert result.text == "integrated response data"
        assert result.routed_via == "cache"
        assert inner._call_count == 1  # not incremented

    def test_cache_miss_goes_through_queue(self):
        """Cache miss dispatches through the queue to the provider."""
        from aria_service.llm.resilience import LLMRequestQueue, LLMResponseCache

        inner = _FakeProvider(response="fresh")
        queue = LLMRequestQueue(inner, max_concurrent=5)
        cache = LLMResponseCache(queue, max_size=10, ttl=3600)

        result = asyncio.run(cache.complete("sys", "new query"))
        assert result.text == "fresh"
        assert queue.get_stats()["total_dispatched"] == 1

    def test_cache_stream_not_cached(self):
        """Streaming bypasses cache (streaming is not cached)."""
        from aria_service.llm.resilience import LLMRequestQueue, LLMResponseCache

        inner = _FakeProvider(response="streamed")
        queue = LLMRequestQueue(inner, max_concurrent=5)
        cache = LLMResponseCache(queue, max_size=10, ttl=3600)

        async def _run():
            chunks = []
            async for chunk in cache.stream("sys", "hello"):
                chunks.append(chunk)
            return "".join(chunks)

        text = asyncio.run(_run())
        assert text == "streamed"
        assert inner._call_count == 1

        # Stream again — should go through again (not cached)
        text2 = asyncio.run(_run())
        assert text2 == "streamed"
        assert inner._call_count == 2  # streaming not cached


# ═══════════════════════════════════════════════════════════════════════════════
# 5. Capability test: the operator's actual path
# ═══════════════════════════════════════════════════════════════════════════════

class TestCapabilityLLMResilience:
    """Capability tests that drive the actual resilience components.

    These tests prove the user-visible behaviour:
      - Repeated queries return cached responses (faster, cheaper)
      - Queue limits prevent provider overload
      - Health checker detects provider failures
    """

    def test_capability_repeated_query_cached(self):
        """Capability: same question twice returns cached answer on second call.

        This is the user-visible benefit: repeated questions don't re-hit
        the LLM, saving cost and latency.
        """
        from aria_service.llm.resilience import LLMResponseCache

        inner = _FakeProvider(response="The answer is 42.")
        cache = LLMResponseCache(inner, max_size=10, ttl=3600)

        # First query — cache miss, LLM called
        r1 = asyncio.run(cache.complete("You are a helpful assistant.", "What is the meaning of life?"))
        assert r1.text == "The answer is 42."
        assert inner._call_count == 1

        # Same query again — cache hit, LLM NOT called
        r2 = asyncio.run(cache.complete("You are a helpful assistant.", "What is the meaning of life?"))
        assert r2.text == "The answer is 42."
        assert r2.routed_via == "cache"
        assert inner._call_count == 1  # LLM not called again

    def test_capability_queue_prevents_overload(self):
        """Capability: queue prevents too many concurrent LLM calls.

        With max_concurrent=2, firing 6 slow requests should still
        complete all 6 (just serialised), not drop any.
        """
        from aria_service.llm.resilience import LLMRequestQueue

        inner = _SlowProvider(delay=0.1, response="slow but steady")
        queue = LLMRequestQueue(inner, max_concurrent=2, max_queue_size=20)

        async def _fire_many():
            tasks = [
                asyncio.create_task(queue.complete("sys", f"req{i}"))
                for i in range(6)
            ]
            results = await asyncio.gather(*tasks, return_exceptions=True)
            return [r for r in results if isinstance(r, LLMResult)]

        results = asyncio.run(_fire_many())
        assert len(results) == 6
        assert all(r.text == "slow but steady" for r in results)

    def test_capability_health_checker_detects_failure(self):
        """Capability: health checker records failures to circuit breaker.

        After N consecutive probe failures, the breaker opens and
        is_available() returns False.
        """
        from aria_service.llm.resilience import LLMHealthChecker

        hc = LLMHealthChecker(
            endpoint="http://localhost:1",  # unreachable
            check_interval=3600,  # don't actually probe in background
            failure_threshold=2,
            cooldown_seconds=300,
        )

        # Simulate probe failures
        hc._record_failure("timeout")
        assert hc.probe_status == "degraded"
        assert hc.is_available() is True  # not yet at threshold

        hc._record_failure("timeout")
        assert hc.probe_status == "unhealthy"
        # Breaker should be OPEN now
        if hc._breaker:
            assert hc._breaker.state == "OPEN"
        assert hc.is_available() is False

    def test_capability_health_checker_recovers(self):
        """Capability: health checker recovers after successful probe."""
        from aria_service.llm.resilience import LLMHealthChecker

        hc = LLMHealthChecker(
            endpoint="http://localhost:1",
            check_interval=3600,
            failure_threshold=2,
            cooldown_seconds=300,
        )

        # Fail twice to open breaker
        hc._record_failure("timeout")
        hc._record_failure("timeout")
        assert hc.is_available() is False

        # Record success — breaker should close
        hc._record_healthy()
        assert hc.probe_status == "healthy"
        assert hc.is_available() is True
        assert hc.consecutive_failures == 0
