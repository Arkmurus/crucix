"""R-F1864 (DD-01) — circuit-breaker failure counter must actually record.

Bug: `_record_failure()` referenced an undefined constant `CB_KEY_FREFIX`
(typo of `CB_KEY_PREFIX`) at test_runner.py:364. The reference raised
NameError, which the broad `except Exception` swallowed to logger.debug — so
the failure count was NEVER incremented and the circuit breaker's
"3 failures in 1h -> 24h cooldown" auto-disable could NEVER trip. This is the
safety net the live autonomous self-deploy loop depends on (CLAUDE.md §21c).

Capability test: drive `_record_failure` three times against an in-memory redis
and assert `_check_circuit_breaker` then refuses further runs. This FAILS before
the one-character fix (count stays 0) and PASSES after.
"""
from __future__ import annotations

import asyncio

import pytest


class _FakeRedis:
    """Minimal async redis stand-in covering the methods the breaker uses."""

    def __init__(self) -> None:
        self.store: dict[str, str] = {}

    async def incr(self, key: str) -> int:
        self.store[key] = str(int(self.store.get(key, "0")) + 1)
        return int(self.store[key])

    async def expire(self, key: str, ttl: int) -> bool:
        return key in self.store

    async def get(self, key: str):
        return self.store.get(key)

    async def setex(self, key: str, ttl: int, val: str) -> bool:
        self.store[key] = str(val)
        return True


def _run(coro):
    return asyncio.run(coro)


def test_record_failure_increments_the_breaker_count():
    """_record_failure must write to {CB_KEY_PREFIX}:count, not raise NameError."""
    from aria_service.autonomous.test_runner import TestRunner, CB_KEY_PREFIX

    redis = _FakeRedis()
    runner = TestRunner(redis_client=redis)

    _run(runner._record_failure("boom"))

    assert redis.store.get(f"{CB_KEY_PREFIX}:count") == "1", (
        "failure was not recorded — the counter key was never written "
        "(NameError on CB_KEY_FREFIX would be swallowed and leave count unset)"
    )


def test_capability_breaker_trips_after_three_failures():
    """Capability: after CB_MAX_FAILURES failures the breaker refuses runs."""
    from aria_service.autonomous.test_runner import TestRunner, CB_MAX_FAILURES

    redis = _FakeRedis()
    runner = TestRunner(redis_client=redis)

    # Before any failures, the breaker allows runs.
    allowed, _ = _run(runner._check_circuit_breaker())
    assert allowed is True

    # Record exactly CB_MAX_FAILURES failures.
    for _ in range(CB_MAX_FAILURES):
        _run(runner._record_failure("test failed"))

    # The breaker must now be open (auto-disabled).
    allowed, reason = _run(runner._check_circuit_breaker())
    assert allowed is False, (
        f"breaker should be open after {CB_MAX_FAILURES} failures, "
        f"but it allowed the run (reason={reason!r}) — counter never incremented"
    )
