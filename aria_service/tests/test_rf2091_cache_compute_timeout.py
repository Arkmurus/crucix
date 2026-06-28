"""R-F2091 — cached_endpoint compute timeout: a hung component can never
PERMANENTLY wedge the endpoint.

Live incident 2026-06-28: /api/aria/health hung >90s. The R-F2072 precompute
loop's first refresh_now() called a component that hung, holding the entry lock
while the value was still _MISS — so every subsequent reader blocked on that lock
forever. The fix bounds every `await fn()` (cold path, refresh_now, and the
background SWR refresh) so a hung compute times out, releases the lock, and the
next tick retries. These tests drive the real decorator.
"""
import asyncio

import pytest

from aria_service.intel.endpoint_cache import cached_endpoint


def test_rf2091_cold_compute_timeout_does_not_hang_forever():
    """A reader whose cold compute hangs gets a TimeoutError (bounded), NOT an
    infinite hang, and the lock is released afterwards."""
    @cached_endpoint(ttl_s=10.0, name="t2091_cold", compute_timeout_s=0.1)
    async def handler():
        await asyncio.sleep(30)        # hang
        return {"v": 1}

    async def run():
        with pytest.raises(asyncio.TimeoutError):
            await handler()
        # lock must be free afterwards — a second call also bounded, not deadlocked
        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(handler(), timeout=2.0)
    asyncio.run(run())


def test_rf2091_refresh_now_timeout_releases_lock_and_recovers():
    """refresh_now() on a hung compute must time out (release the lock), and once
    the component recovers a later call must warm normally — proving no permanent
    wedge (the live /api/aria/health failure mode)."""
    state = {"hang": True, "calls": 0}

    @cached_endpoint(ttl_s=10.0, name="t2091_recover", compute_timeout_s=0.1)
    async def handler():
        state["calls"] += 1
        if state["hang"]:
            await asyncio.sleep(30)
        return {"v": state["calls"]}

    async def run():
        # precompute-style refresh hangs → must raise, not block forever
        with pytest.raises(asyncio.TimeoutError):
            await handler.refresh_now()
        # component recovers
        state["hang"] = False
        # a reader can now acquire the lock (not held by the timed-out refresh) and warm
        res = await asyncio.wait_for(handler(), timeout=2.0)
        assert res["v"] >= 1
        assert handler.cache_stats()["warm"] is True
    asyncio.run(run())


def test_rf2091_fast_compute_unaffected():
    """A normal fast compute still works (timeout is a safety net, not a throttle)."""
    @cached_endpoint(ttl_s=10.0, name="t2091_fast", compute_timeout_s=5.0)
    async def handler():
        return {"ok": True}

    async def run():
        assert (await handler()) == {"ok": True}
        assert (await handler()) == {"ok": True}   # warm hit
        assert handler.cache_stats()["hits"] >= 1
    asyncio.run(run())
