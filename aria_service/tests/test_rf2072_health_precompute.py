"""R-F2072 (Tier 0-finish) — proactive health precompute capability test.

The user-visible property: the heavy /health aggregation must NEVER run on the
request path. R-F2063 cached it (stale-while-revalidate), but a refresh still only
fired when a request arrived, and the FIRST poll after a cold boot still paid the
full compute. R-F2072 adds `refresh_now()` — a method a background warmer loop
calls on a fixed tick — so the cache is always warm and request-path callers only
ever READ a precomputed value.

These tests drive the real `cached_endpoint.refresh_now` and assert that after a
proactive refresh, request-path calls never invoke the (expensive) handler.
"""
import asyncio
import time

from aria_service.intel.endpoint_cache import cached_endpoint


def test_rf2072_refresh_now_warms_cache_so_requests_never_compute():
    """The broken path: a request triggers the 21s compute. Fixed: a background
    refresh_now() warms the cache first, so requests only read."""
    calls = {"compute": 0}

    @cached_endpoint(ttl_s=25.0, name="t_precompute")
    async def health():
        calls["compute"] += 1
        await asyncio.sleep(0.02)        # stand-in for the heavy aggregation
        return {"status": "healthy", "n": calls["compute"]}

    async def run():
        # The precompute loop warms the cache OUT of band, before any request.
        await health.refresh_now()
        assert calls["compute"] == 1, "precompute should compute exactly once"

        # Now 50 request-path calls must all be served from the warm cache and
        # must NOT trigger another compute on the request path.
        t0 = time.monotonic()
        results = await asyncio.gather(*[health() for _ in range(50)])
        elapsed = time.monotonic() - t0
        assert all(r == {"status": "healthy", "n": 1} for r in results)
        assert calls["compute"] == 1, (
            f"request path must never compute; got {calls['compute']} computes"
        )
        assert elapsed < 0.05, "warm reads must be effectively instant (no recompute)"

        st = health.cache_stats()
        assert st["warm"] is True and st["hits"] >= 50
    asyncio.run(run())


def test_rf2072_refresh_now_keeps_value_fresh_across_ticks():
    """A repeated proactive tick keeps replacing the cached value, so the endpoint
    never serves a stale/expired entry even with zero request traffic."""
    calls = {"compute": 0}

    @cached_endpoint(ttl_s=0.05, name="t_precompute_ticks")
    async def health():
        calls["compute"] += 1
        return {"n": calls["compute"]}

    async def run():
        await health.refresh_now()       # tick 1 → n=1
        assert (await health())["n"] == 1
        await asyncio.sleep(0.06)        # entry would now be expired
        await health.refresh_now()       # tick 2 → n=2 (warmer ran before any request)
        # A request after the tick reads the FRESH value via the fast path,
        # so it never recomputes on the request path.
        before = calls["compute"]
        assert (await health())["n"] == 2
        assert calls["compute"] == before, "request after a tick must not recompute"
    asyncio.run(run())


def test_rf2072_refresh_now_single_flight_with_request_path():
    """refresh_now and a concurrent cold request share the lock → one compute."""
    calls = {"compute": 0}

    @cached_endpoint(ttl_s=25.0, name="t_precompute_sf")
    async def health():
        calls["compute"] += 1
        await asyncio.sleep(0.05)
        return {"n": calls["compute"]}

    async def run():
        # Kick a proactive refresh and a request at the same time.
        a, b = await asyncio.gather(health.refresh_now(), health())
        assert a == b == {"n": 1}
        assert calls["compute"] == 1, f"must single-flight, got {calls['compute']}"
    asyncio.run(run())
