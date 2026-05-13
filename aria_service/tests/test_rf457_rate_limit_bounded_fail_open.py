"""R-F457 — autonomous rate limit fails OPEN but BOUNDED on Redis error.

Pre-R-F457 `check_and_increment_rate()` returned `(True, 0)` on every
Redis exception, allowing unbounded autonomous task fan-out for the
duration of any Redis outage. The audit framed this as "cost cap
fails open" but the actual cost-cap path is already protected by a
dual Redis + in-memory counter — the risk was on the RATE LIMIT.

R-F457 adds an in-memory hourly counter that tracks firings during a
Redis outage. Total over-fire is bounded by `MAX_FIRINGS_PER_HOUR`
globally rather than infinity. Once Redis recovers, the real counter
takes over again.
"""
from __future__ import annotations

import asyncio


def test_rf457_in_memory_counter_increments_per_call():
    """Each call to _memory_rate_incr returns a monotonically
    increasing count within the same hour bucket. Resets across
    hour boundaries via the global hour bucket."""
    import time
    from aria_service.autonomous import safety

    # Reset the in-memory state to current hour, count=0
    safety._memory_rate_hour = int(time.time() // 3600)
    safety._memory_rate_count = 0

    a = safety._memory_rate_incr()
    b = safety._memory_rate_incr()
    c = safety._memory_rate_incr()
    assert (a, b, c) == (1, 2, 3), (
        f"R-F457: in-memory rate counter not monotonic. Got {(a, b, c)}"
    )


def test_rf457_redis_failure_falls_back_to_in_memory(monkeypatch):
    """When the Redis INCR raises, check_and_increment_rate must NOT
    return (True, 0) blindly. It must increment the in-memory counter
    and return (allowed_based_on_cap, in_memory_count)."""
    from aria_service.autonomous import safety

    # Reset in-memory state
    import time
    safety._memory_rate_hour = int(time.time() // 3600)
    safety._memory_rate_count = 0

    async def _broken_incr(*a, **kw):
        raise RuntimeError("simulated Redis blip")

    async def _broken_expire(*a, **kw):
        raise RuntimeError("simulated Redis blip")

    # Monkeypatch the redis-store shim
    from aria_service.autonomous.safety import rs as _rs_module
    monkeypatch.setattr(_rs_module, "incr", _broken_incr)
    monkeypatch.setattr(_rs_module, "expire", _broken_expire)

    # First call — count becomes 1, well under cap → allowed
    allowed_a, count_a = asyncio.run(safety.check_and_increment_rate())
    assert allowed_a is True, "R-F457: first fail-open call must be allowed"
    assert count_a == 1, f"R-F457: expected count=1, got {count_a}"

    # Second call — count becomes 2
    allowed_b, count_b = asyncio.run(safety.check_and_increment_rate())
    assert allowed_b is True
    assert count_b == 2


def test_rf457_in_memory_counter_caps_at_max(monkeypatch):
    """If the in-memory counter exceeds MAX_FIRINGS_PER_HOUR during a
    Redis outage, the rate limit MUST start denying. Pin the bound."""
    from aria_service.autonomous import safety

    # Force a Redis failure
    async def _broken(*a, **kw):
        raise RuntimeError("redis down")
    monkeypatch.setattr(safety.rs, "incr", _broken)
    monkeypatch.setattr(safety.rs, "expire", _broken)

    # Set in-memory counter at the cap
    import time
    safety._memory_rate_hour = int(time.time() // 3600)
    safety._memory_rate_count = safety.MAX_FIRINGS_PER_HOUR  # at the cap

    # Next call pushes count over → should be denied
    allowed, count = asyncio.run(safety.check_and_increment_rate())
    assert count == safety.MAX_FIRINGS_PER_HOUR + 1
    assert allowed is False, (
        f"R-F457 REGRESSION: in-memory counter exceeded cap "
        f"({count} > {safety.MAX_FIRINGS_PER_HOUR}) but check_and_increment_rate "
        f"returned allowed=True. Pre-R-F457 over-fire bug returned."
    )


def test_rf457_in_memory_counter_resets_across_hour_boundary(monkeypatch):
    """When the hour bucket changes, the in-memory counter resets to 0.
    Without this a process running for >1h would see ever-increasing
    counts even during Redis health."""
    from aria_service.autonomous import safety

    # Set state to a stale hour
    safety._memory_rate_hour = 0  # stale
    safety._memory_rate_count = 9999

    a = safety._memory_rate_incr()
    # Hour bucket gets updated to NOW, counter resets to 0, then +1
    assert a == 1, (
        f"R-F457: counter should reset across hour boundary. "
        f"Got {a} (expected 1)"
    )
    import time
    assert safety._memory_rate_hour == int(time.time() // 3600)
