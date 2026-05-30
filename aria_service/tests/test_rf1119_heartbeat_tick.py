"""R-F1119 — continuous_profiler heartbeat tick + internal-monitor auth bypass.

Fix 1: _heartbeat_tick() async task bumps main_loop_heartbeat every 1s so
the sampler thread's stall detector doesn't fire false positives (nobody
called bump_heartbeat() before).

Fix 2: /api/aria/health/perf and /api/aria/brain/stats added to
_PUBLIC_AUTH_BYPASS_PATHS so the internal self_healing monitor doesn't
get 401 when calling localhost without a bearer token.
"""
from __future__ import annotations

import asyncio
import time


def test_rf1119_heartbeat_tick_bumps_main_loop_heartbeat():
    """The _heartbeat_tick() task bumps main_loop_heartbeat every 1s.
    Start it, wait 1.5s, and confirm the heartbeat advanced."""
    from aria_service.intel.continuous_profiler import _state, _heartbeat_tick

    # Reset state
    _state["running"] = True
    _state["main_loop_heartbeat"] = 0.0

    async def _run():
        task = asyncio.create_task(_heartbeat_tick())
        await asyncio.sleep(1.5)
        _state["running"] = False
        task.cancel()
        try:
            await task
        except (asyncio.CancelledError, StopIteration):
            pass
        return _state["main_loop_heartbeat"]

    hb = asyncio.run(_run())
    assert hb > 0, f"Expected heartbeat > 0, got {hb}"
    # Should be within the last 2s (1.5s sleep + margin)
    assert time.time() - hb < 2.0, (
        f"Heartbeat too old: {time.time() - hb:.1f}s since last tick"
    )


def test_rf1119_heartbeat_tick_stops_when_not_running():
    """When _state['running'] is False, the tick loop exits."""
    from aria_service.intel.continuous_profiler import _state, _heartbeat_tick

    _state["running"] = False
    _state["main_loop_heartbeat"] = 0.0

    async def _run():
        task = asyncio.create_task(_heartbeat_tick())
        await asyncio.sleep(0.1)
        # Should have exited immediately since running=False
        done = task.done()
        if not done:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        return done

    done = asyncio.run(_run())
    assert done, "_heartbeat_tick should exit immediately when running=False"


def test_rf1119_health_perf_in_bypass():
    """/api/aria/health/perf must be in _PUBLIC_AUTH_BYPASS_PATHS."""
    from aria_service.routes import aria as aria_routes
    assert "/api/aria/health/perf" in aria_routes._PUBLIC_AUTH_BYPASS_PATHS


def test_rf1119_brain_stats_in_bypass():
    """/api/aria/brain/stats must be in _PUBLIC_AUTH_BYPASS_PATHS."""
    from aria_service.routes import aria as aria_routes
    assert "/api/aria/brain/stats" in aria_routes._PUBLIC_AUTH_BYPASS_PATHS
