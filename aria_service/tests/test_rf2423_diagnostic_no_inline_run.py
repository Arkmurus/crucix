"""R-F2423 — /diagnostic/details must NEVER run the ~30s self_diagnostic inline.

Before: when the 120s cache was stale the endpoint did `await run_diagnostic()`
(per-module _check_smoke wait_for(30s) + self-referential _check_endpoint
httpx(15s), gathered → ~30s) → past fly's 8s proxy timeout → the command-centre
"DATA UNAVAILABLE" banner.

After: the endpoint always serves the cache (with staleness flags) and refreshes
it single-flight in the background; a missing cache returns a placeholder. It
must return fast even if run_diagnostic() would block for 30s.

Invokes the ACTUAL endpoint function with the heavy deps monkeypatched.
Runs under pytest OR standalone:  python aria_service/tests/test_rf2423_diagnostic_no_inline_run.py
"""
import asyncio
import sys
import time
import types


def _install_fakes(cache_value, run_diag_coro):
    import aria_service.intel as intel_pkg

    async def _get_json(_k):
        return cache_value

    fake_rs = types.SimpleNamespace(get_json=_get_json)
    fake_sd = types.SimpleNamespace(run_diagnostic=run_diag_coro)
    sys.modules["aria_service.intel.redis_store"] = fake_rs
    sys.modules["aria_service.intel.self_diagnostic"] = fake_sd
    intel_pkg.redis_store = fake_rs
    intel_pkg.self_diagnostic = fake_sd


async def _cancel_bg():
    for t in asyncio.all_tasks():
        if t is not asyncio.current_task():
            t.cancel()
    await asyncio.sleep(0)


async def _run_all():
    import aria_service.routes.aria as a

    # ── 1. NO cache + a run_diagnostic that would block 30s → must return <2s ──
    async def slow_run():
        await asyncio.sleep(30)
        return {"modules": [], "modules_checked": 99}
    _install_fakes(None, slow_run)
    t0 = time.time()
    res = await a.diagnostic_details_ep()
    dt = time.time() - t0
    assert dt < 2.0, f"blocked {dt:.1f}s — endpoint ran diagnostic inline (must not)"
    assert res.get("_regenerating") is True and res.get("modules") == [], res
    await _cancel_bg()
    print(f"  ✓ no-cache: returned placeholder in {dt*1000:.0f}ms (not 30s)")

    # ── 2. Fresh cache → served as-is, marked from-cache, no inline run ──
    ran = {"n": 0}
    async def counting_run():
        ran["n"] += 1
        return {}
    from datetime import datetime, timezone
    fresh = {"modules": [{"name": "x", "status": "ok"}], "modules_checked": 1,
             "generated_at": datetime.now(timezone.utc).isoformat()}
    _install_fakes(fresh, counting_run)
    res = await a.diagnostic_details_ep()
    assert res.get("_from_cache") is True and res.get("modules_checked") == 1, res
    assert res.get("_stale") is False, res
    assert ran["n"] == 0, "fresh cache must NOT trigger a regen"
    await _cancel_bg()
    print("  ✓ fresh-cache: served from cache, _stale=False, 0 regens")

    # ── 3. Stale cache (age>1200s = 15-min task missed a cycle) → served + bg regen ──
    from datetime import timedelta
    old = {"modules": [{"name": "x", "status": "ok"}], "modules_checked": 1,
           "generated_at": (datetime.now(timezone.utc) - timedelta(seconds=1500)).isoformat()}
    _install_fakes(old, slow_run)
    t0 = time.time()
    res = await a.diagnostic_details_ep()
    dt = time.time() - t0
    assert dt < 2.0, f"stale path blocked {dt:.1f}s"
    assert res.get("_from_cache") is True and res.get("_stale") is True, res
    # a background regen task must have been scheduled (fire-and-forget)
    others = [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]
    assert len(others) >= 1, "stale cache must schedule a background regen"
    await _cancel_bg()
    print(f"  ✓ stale-cache: served in {dt*1000:.0f}ms + bg regen scheduled")

    # ── 4. Cache read itself hangs (state_store wedged) → bounded, not 30s ──
    async def hang_get(_k):
        await asyncio.sleep(30)
    import aria_service.intel as intel_pkg
    intel_pkg.redis_store = types.SimpleNamespace(get_json=hang_get)
    sys.modules["aria_service.intel.redis_store"] = intel_pkg.redis_store
    intel_pkg.self_diagnostic = types.SimpleNamespace(run_diagnostic=slow_run)
    sys.modules["aria_service.intel.self_diagnostic"] = intel_pkg.self_diagnostic
    t0 = time.time()
    res = await a.diagnostic_details_ep()
    dt = time.time() - t0
    assert dt < 6.5, f"hung cache read blocked {dt:.1f}s — wait_for(5s) cap failed"
    assert res.get("_regenerating") is True, res
    await _cancel_bg()
    print(f"  ✓ wedged-read: bounded to {dt*1000:.0f}ms (wait_for 5s cap) → placeholder")


def test_diagnostic_details_never_runs_inline():
    asyncio.run(_run_all())


if __name__ == "__main__":
    asyncio.run(_run_all())
    print("\nPASS")
