"""R-F2063 — endpoint cache (stale-while-revalidate) capability test.

Drives the real cached_endpoint decorator: a slow handler is computed ONCE on the
cold call, served instantly from cache within the TTL, and after the TTL a stale
value is returned IMMEDIATELY while a single background refresh runs — so a polled
heavy endpoint never re-pays its full cost on the request path.
"""
import asyncio
import time

from aria_service.intel.endpoint_cache import cached_endpoint


def test_rf2063_cold_compute_then_fresh_hits_dont_recompute():
    calls = {"n": 0}

    @cached_endpoint(ttl_s=10.0, name="t_fresh")
    async def handler():
        calls["n"] += 1
        await asyncio.sleep(0)
        return {"v": calls["n"]}

    async def run():
        a = await handler()          # cold → compute #1
        b = await handler()          # fresh hit → no recompute
        c = await handler()          # fresh hit → no recompute
        assert a == {"v": 1} and b == {"v": 1} and c == {"v": 1}
        assert calls["n"] == 1, "fresh hits must not recompute"
        st = handler.cache_stats()
        assert st["warm"] is True and st["hits"] >= 2 and st["misses"] == 1
    asyncio.run(run())


def test_rf2063_stale_serves_old_value_immediately_and_refreshes_in_background():
    calls = {"n": 0}

    @cached_endpoint(ttl_s=0.05, name="t_stale")
    async def handler():
        calls["n"] += 1
        await asyncio.sleep(0)
        return {"v": calls["n"]}

    async def run():
        first = await handler()                 # cold → v=1
        assert first == {"v": 1}
        await asyncio.sleep(0.06)               # let it go stale
        t0 = time.monotonic()
        stale = await handler()                 # must return OLD value instantly
        elapsed = time.monotonic() - t0
        assert stale == {"v": 1}, "stale read must serve the previous value, not block"
        assert elapsed < 0.03, "stale read must be immediate (no recompute on the request path)"
        # the background refresh should land shortly
        await asyncio.sleep(0.02)
        fresh = await handler()
        assert fresh == {"v": 2}, "background refresh must have updated the cache"
        assert handler.cache_stats()["stale_serves"] >= 1
    asyncio.run(run())


def test_rf2063_cold_single_flight_one_compute_for_concurrent_callers():
    calls = {"n": 0}

    @cached_endpoint(ttl_s=10.0, name="t_singleflight")
    async def handler():
        calls["n"] += 1
        await asyncio.sleep(0.05)               # slow compute window
        return {"v": calls["n"]}

    async def run():
        # 5 concurrent COLD callers must trigger exactly ONE compute (single-flight)
        results = await asyncio.gather(*[handler() for _ in range(5)])
        assert all(r == {"v": 1} for r in results)
        assert calls["n"] == 1, f"cold stampede must single-flight, got {calls['n']} computes"
    asyncio.run(run())
