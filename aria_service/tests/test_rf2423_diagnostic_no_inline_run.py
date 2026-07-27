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


# ── R-F3314: these fakes MUST be uninstalled. ────────────────────────────────
# This file replaces two CORE modules in sys.modules and, before this fix, never
# put them back. The last fake installed below is a get_json that sleeps 30
# SECONDS (the section-4 "wedged read" case), so every later test in the session
# that read the store slept 30s per call until pytest-timeout killed the whole
# process. That is why the full Python suite could never reach a summary: it is
# not slowness, it is a poisoned sys.modules entry.
#
# Proven by bisection over the 1461-file suite: this file plus
# test_rf2469_durable_dd_ownership.py wedges; either alone passes (rf2469 in
# 0.83s). The pairing is deterministic, not flaky.
#
# Same class as R-F2801, documented in test_rf1498's own header: a process-global
# mutation no monkeypatch undoes, which leaks into every later test in the run.
# The other seven tests that touch sys.modules only INJECT standalone scripts
# (eval_aria_llm, sast_scan, cre_eval, reachability_sweep); they add entries
# rather than replacing production modules, so they are not this defect.
_MISSING = object()

_PATCHED = (
    ("aria_service.intel.redis_store", "redis_store"),
    ("aria_service.intel.self_diagnostic", "self_diagnostic"),
)


def _snapshot_real_modules():
    """Capture the REAL modules before any fake is installed."""
    import aria_service.intel as intel_pkg
    return {
        mod: (sys.modules.get(mod, _MISSING), getattr(intel_pkg, attr, _MISSING))
        for mod, attr in _PATCHED
    }


def _restore_real_modules(snap) -> None:
    """Put them back, including the case where they were never imported."""
    import aria_service.intel as intel_pkg
    for mod, attr in _PATCHED:
        in_sys, on_pkg = snap[mod]
        if in_sys is _MISSING:
            sys.modules.pop(mod, None)
        else:
            sys.modules[mod] = in_sys
        if on_pkg is _MISSING:
            if hasattr(intel_pkg, attr):
                delattr(intel_pkg, attr)
        else:
            setattr(intel_pkg, attr, on_pkg)


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
    """R-F3314 wrapper: guarantee the real modules go back, on ANY exit path.

    Wrapping rather than re-indenting the body keeps the assertions byte-identical,
    and covers BOTH entry points (pytest and `python <this file>`), so the fix
    cannot be bypassed by running it standalone.
    """
    # Import the real modules first, so the snapshot captures them rather than an
    # absence. Without this, a session where nothing had imported the store yet
    # would snapshot _MISSING and the restore would leave it unimported.
    import aria_service.intel.redis_store      # noqa: F401
    import aria_service.intel.self_diagnostic  # noqa: F401
    _snap = _snapshot_real_modules()
    try:
        await _run_all_inner()
    finally:
        _restore_real_modules(_snap)


async def _run_all_inner():
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


def test_rf3314_the_real_modules_do_not_stay_faked():
    """R-F3314 CAPABILITY TEST: the fakes must not outlive this file.

    This asserts the state every LATER test inherits, which is the thing that was
    actually broken. Runs after the test above (file order), so it observes what
    that test left behind.

    Pre-fix this fails: sys.modules still holds the section-4 SimpleNamespace whose
    get_json sleeps 30 seconds, so the next test in the session to read the store
    stalls until pytest-timeout kills the process. Bisection over the 1461-file
    suite pinned this file as the poisoner and rf2469 as the first victim.
    """
    import aria_service.intel as intel_pkg

    rs = sys.modules.get("aria_service.intel.redis_store")
    assert rs is not None, "redis_store was removed from sys.modules and not restored"
    assert not isinstance(rs, types.SimpleNamespace), (
        "a FAKE redis_store escaped this file. Every later test that reads the "
        "store inherits it, and the section-4 fake sleeps 30s per get_json."
    )
    # The fake carried only get_json; the real module has the full surface.
    for attr in ("get_json", "set_json", "scan_keys"):
        assert hasattr(rs, attr), f"restored redis_store has no {attr!r} - not the real module"

    sd = sys.modules.get("aria_service.intel.self_diagnostic")
    assert sd is None or not isinstance(sd, types.SimpleNamespace), (
        "a fake self_diagnostic escaped this file"
    )

    # The package attribute is a second, independent handle: code doing
    # `from aria_service.intel import redis_store` reads THIS, not sys.modules,
    # so restoring only one of the two would leave the poison half-installed.
    assert getattr(intel_pkg, "redis_store", rs) is rs, (
        "sys.modules was restored but aria_service.intel.redis_store still points "
        "at the fake"
    )


if __name__ == "__main__":
    asyncio.run(_run_all())
    print("\nPASS")
