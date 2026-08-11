"""R-F1031 — Unit tests for the autonomous safety guardrails module.

Covers all 5 guardrails in `aria_service/autonomous/safety.py`:

  1. Rate limit       — token bucket on task firings per hour
  2. Daily cost cap   — circuit breaker on total daily LLM cost
  3. Deduplication    — skip if same task+entity ran in last 24h
  4. Per-task timeout — (tested via can_task_run ordering)
  5. Engine pause     — global kill switch

Also tests the composite `can_task_run()` function and the in-memory
fallback paths (H8 pattern — Redis outage must not open floodgates).

These are unit tests — no live Redis, no real LLM calls. Uses a stub
redis_store module patched at import time.
"""
from __future__ import annotations

import asyncio
import time
from unittest.mock import AsyncMock, patch

import pytest


def _run(coro):
    """Run an async coroutine — pytest-asyncio is not available."""
    return asyncio.run(coro)


def _reset_safety_memory():
    """Reset the module-level in-memory counters so tests don't leak
    state across each other. The safety module uses module-level globals
    (_memory_cost_spent, _memory_cost_day, _memory_rate_hour,
    _memory_rate_count) that persist across test functions."""
    import aria_service.autonomous.safety as _s
    _s._memory_cost_day = ""
    _s._memory_cost_spent = 0.0
    _s._memory_rate_hour = 0
    _s._memory_rate_count = 0


@pytest.fixture(autouse=True)
def _no_safety_leak_past_this_file():
    """R-F3894 — reset AFTER every test, not only before.

    `_reset_safety_memory()` was called at the START of seven tests, so state was
    clean WITHIN this file and the LAST test to run left `safety._memory_cost_spent`
    dirty for every file that follows. That is invisible under a full-suite run —
    some later test in this file happens to reset it — and appears the moment a `-k`
    selection deselects the tests that would have.

    THE VICTIM, and it is not a test-only problem in shape. Traced end to end:

        safety._memory_cost_spent (left over-budget by TestCostCap)
          -> load_governor.cost_pressure()      reads it (load_governor.py:193,
                                                "ONE source of truth for the day's spend")
          -> load_governor.should_shed_paid()
          -> student.py:1406 gates Brave escalation on `not _paid_shed`
          -> test_rf2392_brave_region_sourcing_gate2 sees NO escalation and fails

    So a stale global in a unit test silently switched off the paid-search
    escalation in an unrelated module — the R-F2961 cost-shed doing exactly what it
    should, on a number that was fiction. Reproduced pairwise in 1.6s
    (rf1031 + rf2392) after bisecting the collection order, per §16.

    Fixture rather than another call at the top of each test: a guarantee that
    depends on every future author remembering it is not a guarantee.
    """
    _reset_safety_memory()
    yield
    _reset_safety_memory()


# ════════════════════════════════════════════════════════════════════════════
# Stub redis_store — matches the public surface safety.py uses
# ════════════════════════════════════════════════════════════════════════════

class _StubRedisStore:
    """In-memory stub matching the redis_store module-level functions
    that safety.py calls (rs.get, rs.set, rs.incr, rs.expire, rs.delete,
    rs.incrbyfloat)."""

    def __init__(self) -> None:
        self._kv: dict[str, str] = {}
        self._expiry: dict[str, float] = {}

    async def get(self, key: str) -> str | None:
        # Check expiry
        if key in self._expiry and time.time() > self._expiry[key]:
            self._kv.pop(key, None)
            self._expiry.pop(key, None)
            return None
        return self._kv.get(key)

    async def set(self, key: str, value: str, ex: int | None = None) -> None:
        self._kv[key] = value
        if ex is not None:
            self._expiry[key] = time.time() + ex

    async def incr(self, key: str, amount: int = 1) -> int:
        current = int(self._kv.get(key, "0"))
        current += amount
        self._kv[key] = str(current)
        return current

    async def incrbyfloat(self, key: str, amount: float) -> float:
        current = float(self._kv.get(key, "0"))
        current += amount
        self._kv[key] = f"{current:.6f}"
        return current

    async def expire(self, key: str, seconds: int) -> bool:
        if key in self._kv:
            self._expiry[key] = time.time() + seconds
            return True
        return False

    async def delete(self, key: str) -> bool:
        existed = key in self._kv
        self._kv.pop(key, None)
        self._expiry.pop(key, None)
        return existed


# ════════════════════════════════════════════════════════════════════════════
# Rate limit
# ════════════════════════════════════════════════════════════════════════════

class TestRateLimit:
    def setup_method(self) -> None:
        _reset_safety_memory()

    def test_first_firing_allowed(self) -> None:
        async def body() -> None:
            stub = _StubRedisStore()
            with patch("aria_service.autonomous.safety.rs", stub):
                from aria_service.autonomous import safety
                allowed, count = await safety.check_and_increment_rate()
                assert allowed
                assert count == 1
        _run(body())

    def test_within_cap_allowed(self) -> None:
        async def body() -> None:
            stub = _StubRedisStore()
            with patch("aria_service.autonomous.safety.rs", stub):
                from aria_service.autonomous import safety
                # Fill bucket to cap-1
                for _ in range(safety.MAX_FIRINGS_PER_HOUR - 1):
                    await safety.check_and_increment_rate()
                allowed, count = await safety.check_and_increment_rate()
                assert allowed
                assert count == safety.MAX_FIRINGS_PER_HOUR
        _run(body())

    def test_over_cap_blocked_and_rolled_back(self) -> None:
        """R-F897: a BLOCKED attempt must NOT inflate the bucket."""
        async def body() -> None:
            stub = _StubRedisStore()
            with patch("aria_service.autonomous.safety.rs", stub):
                from aria_service.autonomous import safety
                # Fill bucket to cap
                for _ in range(safety.MAX_FIRINGS_PER_HOUR):
                    await safety.check_and_increment_rate()
                # Next firing should be blocked AND rolled back
                allowed, count = await safety.check_and_increment_rate()
                assert not allowed
                # Count should be exactly cap (not cap+1) — the speculative
                # incr was rolled back
                assert count == safety.MAX_FIRINGS_PER_HOUR
        _run(body())

    def test_coder_uses_separate_bucket(self) -> None:
        """R-F901: coder has its own hourly bucket."""
        async def body() -> None:
            stub = _StubRedisStore()
            with patch("aria_service.autonomous.safety.rs", stub):
                from aria_service.autonomous import safety
                # Exhaust the shared bucket
                for _ in range(safety.MAX_FIRINGS_PER_HOUR):
                    await safety.check_and_increment_rate()
                # Shared bucket should be blocked
                allowed, _ = await safety.check_and_increment_rate()
                assert not allowed
                # Coder bucket should still be fresh
                from aria_service.autonomous.safety import (
                    _CODER_RATE_KEY_FMT, CODER_MAX_FIXES_PER_HOUR,
                )
                allowed_coder, count = await safety.check_and_increment_rate(
                    key_fmt=_CODER_RATE_KEY_FMT, cap=CODER_MAX_FIXES_PER_HOUR,
                )
                assert allowed_coder
                assert count == 1
        _run(body())

    def test_redis_failure_falls_back_to_in_memory(self) -> None:
        """R-F457: Redis outage must not enable unbounded over-fire."""
        async def body() -> None:
            broken = AsyncMock()
            broken.incr = AsyncMock(side_effect=RuntimeError("Redis down"))
            with patch("aria_service.autonomous.safety.rs", broken):
                from aria_service.autonomous import safety
                # First call should fall back to in-memory and succeed
                allowed, count = await safety.check_and_increment_rate()
                assert allowed
                assert count == 1
                # Exhaust the in-memory cap
                for _ in range(safety.MAX_FIRINGS_PER_HOUR):
                    await safety.check_and_increment_rate()
                # Should now be blocked even with Redis down
                allowed, count = await safety.check_and_increment_rate()
                assert not allowed
        _run(body())


# ════════════════════════════════════════════════════════════════════════════
# Cost cap
# ════════════════════════════════════════════════════════════════════════════

class TestCostCap:
    def setup_method(self) -> None:
        _reset_safety_memory()

    def test_within_budget_allowed(self) -> None:
        async def body() -> None:
            stub = _StubRedisStore()
            with patch("aria_service.autonomous.safety.rs", stub):
                from aria_service.autonomous import safety
                within, spent = await safety.check_cost_cap()
                assert within
                assert spent == 0.0
        _run(body())

    def test_over_budget_blocked(self) -> None:
        async def body() -> None:
            stub = _StubRedisStore()
            with patch("aria_service.autonomous.safety.rs", stub):
                from aria_service.autonomous import safety
                # Record cost above the cap
                await safety.record_task_cost(safety.DAILY_COST_CAP_USD + 1.0)
                within, spent = await safety.check_cost_cap()
                assert not within
                assert spent >= safety.DAILY_COST_CAP_USD
        _run(body())

    def test_record_task_cost_updates_both_counters(self) -> None:
        """H8: cost writes to both Redis and in-memory."""
        async def body() -> None:
            stub = _StubRedisStore()
            with patch("aria_service.autonomous.safety.rs", stub):
                from aria_service.autonomous import safety
                await safety.record_task_cost(5.0)
                within, spent = await safety.check_cost_cap()
                assert within  # 5 < cap
                assert spent >= 5.0
        _run(body())

    def test_redis_failure_falls_back_to_in_memory(self) -> None:
        """H8: Redis outage must not open the floodgates."""
        async def body() -> None:
            broken = AsyncMock()
            broken.get = AsyncMock(side_effect=RuntimeError("Redis down"))
            with patch("aria_service.autonomous.safety.rs", broken):
                from aria_service.autonomous import safety
                # Record cost in memory
                await safety.record_task_cost(safety.DAILY_COST_CAP_USD + 1.0)
                # Check should use in-memory counter
                within, spent = await safety.check_cost_cap()
                assert not within
                assert spent >= safety.DAILY_COST_CAP_USD
        _run(body())

    def test_zero_cost_not_recorded(self) -> None:
        async def body() -> None:
            stub = _StubRedisStore()
            with patch("aria_service.autonomous.safety.rs", stub):
                from aria_service.autonomous import safety
                await safety.record_task_cost(0.0)
                within, spent = await safety.check_cost_cap()
                assert within
                assert spent == 0.0
        _run(body())


# ════════════════════════════════════════════════════════════════════════════
# Deduplication
# ════════════════════════════════════════════════════════════════════════════

class TestDeduplication:
    def setup_method(self) -> None:
        _reset_safety_memory()

    def test_first_run_allowed(self) -> None:
        async def body() -> None:
            stub = _StubRedisStore()
            with patch("aria_service.autonomous.safety.rs", stub):
                from aria_service.autonomous import safety
                allowed = await safety.check_and_mark_dedupe("task_x", "entity_a")
                assert allowed
        _run(body())

    def test_duplicate_blocked(self) -> None:
        async def body() -> None:
            stub = _StubRedisStore()
            with patch("aria_service.autonomous.safety.rs", stub):
                from aria_service.autonomous import safety
                await safety.check_and_mark_dedupe("task_x", "entity_a")
                allowed = await safety.check_and_mark_dedupe("task_x", "entity_a")
                assert not allowed
        _run(body())

    def test_different_entity_allowed(self) -> None:
        async def body() -> None:
            stub = _StubRedisStore()
            with patch("aria_service.autonomous.safety.rs", stub):
                from aria_service.autonomous import safety
                await safety.check_and_mark_dedupe("task_x", "entity_a")
                allowed = await safety.check_and_mark_dedupe("task_x", "entity_b")
                assert allowed
        _run(body())

    def test_empty_task_id_always_allowed(self) -> None:
        async def body() -> None:
            stub = _StubRedisStore()
            with patch("aria_service.autonomous.safety.rs", stub):
                from aria_service.autonomous import safety
                allowed = await safety.check_and_mark_dedupe("", "entity_a")
                assert allowed
        _run(body())

    def test_clear_dedupe_allows_retry(self) -> None:
        async def body() -> None:
            stub = _StubRedisStore()
            with patch("aria_service.autonomous.safety.rs", stub):
                from aria_service.autonomous import safety
                await safety.check_and_mark_dedupe("task_x", "entity_a")
                await safety.clear_dedupe("task_x", "entity_a")
                allowed = await safety.check_and_mark_dedupe("task_x", "entity_a")
                assert allowed
        _run(body())

    def test_redis_failure_fails_open(self) -> None:
        """A Redis outage on dedup check must allow the task to run."""
        async def body() -> None:
            broken = AsyncMock()
            broken.get = AsyncMock(side_effect=RuntimeError("Redis down"))
            with patch("aria_service.autonomous.safety.rs", broken):
                from aria_service.autonomous import safety
                allowed = await safety.check_and_mark_dedupe("task_x", "entity_a")
                assert allowed  # fail-open
        _run(body())


# ════════════════════════════════════════════════════════════════════════════
# Engine pause / resume
# ════════════════════════════════════════════════════════════════════════════

class TestEnginePause:
    def setup_method(self) -> None:
        _reset_safety_memory()

    def test_engine_not_paused_by_default(self) -> None:
        async def body() -> None:
            stub = _StubRedisStore()
            with patch("aria_service.autonomous.safety.rs", stub):
                from aria_service.autonomous import safety
                paused = await safety.is_engine_paused()
                assert not paused
        _run(body())

    def test_pause_engine_sets_flag(self) -> None:
        async def body() -> None:
            stub = _StubRedisStore()
            with patch("aria_service.autonomous.safety.rs", stub):
                from aria_service.autonomous import safety
                await safety.pause_engine("test")
                paused = await safety.is_engine_paused()
                assert paused
        _run(body())

    def test_resume_engine_clears_flag(self) -> None:
        async def body() -> None:
            stub = _StubRedisStore()
            with patch("aria_service.autonomous.safety.rs", stub):
                from aria_service.autonomous import safety
                await safety.pause_engine("test")
                await safety.resume_engine()
                paused = await safety.is_engine_paused()
                assert not paused
        _run(body())

    def test_redis_failure_returns_false(self) -> None:
        """Pause must be deliberate — Redis failure = not paused."""
        async def body() -> None:
            broken = AsyncMock()
            broken.get = AsyncMock(side_effect=RuntimeError("Redis down"))
            with patch("aria_service.autonomous.safety.rs", broken):
                from aria_service.autonomous import safety
                paused = await safety.is_engine_paused()
                assert not paused  # fail-open
        _run(body())


# ════════════════════════════════════════════════════════════════════════════
# Per-task pause / resume
# ════════════════════════════════════════════════════════════════════════════

class TestTaskPause:
    def setup_method(self) -> None:
        _reset_safety_memory()

    def test_task_not_paused_by_default(self) -> None:
        async def body() -> None:
            stub = _StubRedisStore()
            with patch("aria_service.autonomous.safety.rs", stub):
                from aria_service.autonomous import safety
                paused = await safety.is_task_paused("task_x")
                assert not paused
        _run(body())

    def test_pause_task_sets_flag(self) -> None:
        async def body() -> None:
            stub = _StubRedisStore()
            with patch("aria_service.autonomous.safety.rs", stub):
                from aria_service.autonomous import safety
                await safety.pause_task("task_x")
                paused = await safety.is_task_paused("task_x")
                assert paused
        _run(body())

    def test_resume_task_clears_flag(self) -> None:
        async def body() -> None:
            stub = _StubRedisStore()
            with patch("aria_service.autonomous.safety.rs", stub):
                from aria_service.autonomous import safety
                await safety.pause_task("task_x")
                await safety.resume_task("task_x")
                paused = await safety.is_task_paused("task_x")
                assert not paused
        _run(body())

    def test_different_tasks_independent(self) -> None:
        async def body() -> None:
            stub = _StubRedisStore()
            with patch("aria_service.autonomous.safety.rs", stub):
                from aria_service.autonomous import safety
                await safety.pause_task("task_x")
                assert await safety.is_task_paused("task_x")
                assert not await safety.is_task_paused("task_y")
        _run(body())


# ════════════════════════════════════════════════════════════════════════════
# Composite can_task_run
# ════════════════════════════════════════════════════════════════════════════

class TestCanTaskRun:
    def setup_method(self) -> None:
        _reset_safety_memory()

    def test_all_checks_pass(self) -> None:
        async def body() -> None:
            stub = _StubRedisStore()
            with patch("aria_service.autonomous.safety.rs", stub):
                from aria_service.autonomous import safety
                allowed, reason = await safety.can_task_run("task_x", "entity_a")
                assert allowed
                assert reason == "ok"
        _run(body())

    def test_engine_paused_blocks(self) -> None:
        async def body() -> None:
            stub = _StubRedisStore()
            with patch("aria_service.autonomous.safety.rs", stub):
                from aria_service.autonomous import safety
                await safety.pause_engine("test")
                allowed, reason = await safety.can_task_run("task_x", "entity_a")
                assert not allowed
                assert "engine_paused" in reason
        _run(body())

    def test_task_paused_blocks(self) -> None:
        async def body() -> None:
            stub = _StubRedisStore()
            with patch("aria_service.autonomous.safety.rs", stub):
                from aria_service.autonomous import safety
                await safety.pause_task("task_x")
                allowed, reason = await safety.can_task_run("task_x", "entity_a")
                assert not allowed
                assert "task_paused" in reason
        _run(body())

    def test_cost_cap_exceeded_blocks(self) -> None:
        async def body() -> None:
            stub = _StubRedisStore()
            with patch("aria_service.autonomous.safety.rs", stub):
                from aria_service.autonomous import safety
                await safety.record_task_cost(safety.DAILY_COST_CAP_USD + 1.0)
                allowed, reason = await safety.can_task_run("task_x", "entity_a")
                assert not allowed
                assert "daily_cost_cap_exceeded" in reason
        _run(body())

    def test_rate_limit_exceeded_blocks(self) -> None:
        async def body() -> None:
            stub = _StubRedisStore()
            with patch("aria_service.autonomous.safety.rs", stub):
                from aria_service.autonomous import safety
                # Exhaust the rate bucket
                for _ in range(safety.MAX_FIRINGS_PER_HOUR):
                    await safety.check_and_increment_rate()
                allowed, reason = await safety.can_task_run("task_x", "entity_a")
                assert not allowed
                assert "rate_limit_exceeded" in reason
        _run(body())

    def test_duplicate_recent_run_blocks(self) -> None:
        async def body() -> None:
            stub = _StubRedisStore()
            with patch("aria_service.autonomous.safety.rs", stub):
                from aria_service.autonomous import safety
                # First run succeeds
                allowed, _ = await safety.can_task_run("task_x", "entity_a")
                assert allowed
                # Second run should be deduped
                allowed, reason = await safety.can_task_run("task_x", "entity_a")
                assert not allowed
                assert "duplicate_recent_run" in reason
        _run(body())

    def test_coder_uses_separate_rate_bucket(self) -> None:
        """R-F901: coder=True uses the coder's own hourly bucket."""
        async def body() -> None:
            stub = _StubRedisStore()
            with patch("aria_service.autonomous.safety.rs", stub):
                from aria_service.autonomous import safety
                # Exhaust the shared bucket
                for _ in range(safety.MAX_FIRINGS_PER_HOUR):
                    await safety.check_and_increment_rate()
                # Shared task should be blocked
                allowed_shared, _ = await safety.can_task_run("task_x", "entity_a")
                assert not allowed_shared
                # Coder should still be allowed (separate bucket)
                allowed_coder, reason = await safety.can_task_run(
                    "coder_fix", "gap_1", coder=True,
                )
                assert allowed_coder, f"coder blocked: {reason}"
        _run(body())

    def test_paused_engine_does_not_consume_rate_slot(self) -> None:
        """A task blocked by pause must not consume a rate bucket slot."""
        async def body() -> None:
            stub = _StubRedisStore()
            with patch("aria_service.autonomous.safety.rs", stub):
                from aria_service.autonomous import safety
                await safety.pause_engine("test")
                # This should be blocked by pause, not rate limit
                await safety.can_task_run("task_x", "entity_a")
                # Rate bucket should still be at 0
                from aria_service.autonomous.safety import _RATE_KEY_FMT
                import time
                key = _RATE_KEY_FMT.format(hour=int(time.time() // 3600))
                count = int(await stub.get(key) or "0")
                assert count == 0, "pause should not consume rate slot"
        _run(body())


# ════════════════════════════════════════════════════════════════════════════
# Capability test — the user-visible symptom
# ════════════════════════════════════════════════════════════════════════════

def test_capability_safety_blocks_runaway_autonomy() -> None:
    """Capability test: the safety module MUST prevent unbounded task
    execution. With N > MAX_FIRINGS_PER_HOUR tasks queued, only the first
    MAX_FIRINGS_PER_HOUR should execute; the rest must be blocked."""
    _reset_safety_memory()
    async def body() -> None:
        stub = _StubRedisStore()
        with patch("aria_service.autonomous.safety.rs", stub):
            from aria_service.autonomous import safety
            results: list[tuple[bool, str]] = []
            # Fire more tasks than the cap allows
            for i in range(safety.MAX_FIRINGS_PER_HOUR + 5):
                allowed, reason = await safety.can_task_run(
                    f"task_{i}", f"entity_{i}",
                )
                results.append((allowed, reason))
            allowed_count = sum(1 for a, _ in results if a)
            blocked_count = sum(1 for a, _ in results if not a)
            assert allowed_count == safety.MAX_FIRINGS_PER_HOUR, (
                f"Expected {safety.MAX_FIRINGS_PER_HOUR} allowed, "
                f"got {allowed_count}"
            )
            assert blocked_count == 5, (
                f"Expected 5 blocked, got {blocked_count}"
            )
    _run(body())
