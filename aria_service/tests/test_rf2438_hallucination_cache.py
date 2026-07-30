"""R-F2438 — hallucination/stats served from a real, staleness-flagged cache so
state_store contention (R-F2277) can't push it past the 8s fly-proxy.

Invokes the REAL hallucination_stats_ep with _compute monkeypatched, proving:
  1. cold  → computes once, returns REAL data
  2. warm  → served from cache (flagged), NO recompute
  3. stale → served stale-flagged + a single-flight bg refresh scheduled
  4. cold compute is BOUNDED — a 30s compute still returns <8s (placeholder),
     never hanging the proxy
The data is always the REAL computed stats (age-flagged), never fabricated.

Run: python aria_service/tests/test_rf2438_hallucination_cache.py
"""
import asyncio
import time


async def _cancel_bg():
    for t in asyncio.all_tasks():
        if t is not asyncio.current_task():
            t.cancel()
    await asyncio.sleep(0)


async def _run():
    import aria_service.routes.aria as a
    fails = []
    ok = lambda c, m: (print(f"  {'✓' if c else '✗'} {m}"), fails.append(m) if not c else None)

    calls = {"n": 0}
    async def fake_compute():
        calls["n"] += 1
        return {"summary": {"total_violations_24h": 3, "turns_observed_24h": 100},
                "self_claim_guard": {"ok": True}, "_schema_version": "rf407.v1"}

    # R-F3449 — this was a bare assignment with no restore, leaving
    # routes.aria._compute_hallucination_stats stubbed (and _hall_cache primed) for the REST
    # OF THE SESSION. Latent rather than active in the R-F3448 baseline because nothing
    # after this file re-uses them, which is luck, not safety.
    #
    # try/finally rather than the monkeypatch fixture: `_run` is a plain async helper called
    # both by test_hallucination_cache and by a __main__ block, so it has no fixture access.
    _orig_compute = a._compute_hallucination_stats
    _orig_cache = getattr(a, "_hall_cache", None)
    a._compute_hallucination_stats = fake_compute
    try:
        return await _body(a, ok, fails, calls)
    finally:
        a._compute_hallucination_stats = _orig_compute
        a._hall_cache = _orig_cache


async def _body(a, ok, fails, calls):
    import time

    # 1. cold → real compute
    a._hall_cache = None
    r1 = await a.hallucination_stats_ep()
    ok(r1["summary"]["total_violations_24h"] == 3 and calls["n"] == 1, "cold: computes once, returns REAL data")
    ok("_from_cache" not in r1, "cold: fresh compute not marked from-cache")

    # 2. warm → from cache, NO recompute
    r2 = await a.hallucination_stats_ep()
    ok(r2.get("_from_cache") is True and r2.get("_cache_age_s") is not None, "warm: served from cache with age flag")
    ok(calls["n"] == 1, "warm: did NOT recompute (real data reused)")
    ok(r2["summary"]["total_violations_24h"] == 3, "warm: still the real stats")

    # 3. stale (age>60s) → serve stale-flagged + single-flight bg refresh
    a._hall_cache = (time.time() - 120, {"summary": {"total_violations_24h": 3}, "_schema_version": "rf407.v1"})
    n_before = calls["n"]
    r3 = await a.hallucination_stats_ep()
    ok(r3.get("_stale") is True and r3.get("_cache_age_s", 0) >= 120, "stale: served stale-flagged (real, age>=120s)")
    others = [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]
    ok(len(others) >= 1, "stale: single-flight bg refresh scheduled")
    await asyncio.sleep(0.05)  # let the bg refresh run
    ok(calls["n"] == n_before + 1, "stale: bg refresh recomputed real stats for next caller")
    await _cancel_bg()

    # 4. cold compute BOUNDED — a 30s compute must still return <8s (placeholder)
    a._hall_cache = None
    async def slow_compute():
        await asyncio.sleep(30)
        return {}
    a._compute_hallucination_stats = slow_compute
    t0 = time.time()
    r4 = await a.hallucination_stats_ep()
    dt = time.time() - t0
    ok(dt < 7.6, f"cold+slow: bounded to {dt*1000:.0f}ms (wait_for 7s) — never hangs the 8s proxy")
    ok(r4.get("_regenerating") is True, "cold+slow: honest _regenerating placeholder (not fabricated stats)")
    await _cancel_bg()

    print("\nPASS" if not fails else f"\nFAIL ({len(fails)})")
    if fails:
        raise SystemExit(1)


def test_hallucination_cache():
    asyncio.run(_run())


if __name__ == "__main__":
    asyncio.run(_run())
