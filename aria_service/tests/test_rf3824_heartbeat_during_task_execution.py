"""R-F3824 — a long task must not read as a dead engine, and a hung one still must.

THE DEFECT, from the live blackout dumps on 2026-08-10.

    === [R-F1146] Blackout detected for autonomous_engine: heartbeat stale 301.1s ===
    --- Task: Task-143 (_engine_loop) done=False cancelled=False ---
      /app/aria_service/autonomous/engine.py:1019 _engine_loop

Twice today (301.1s and 310.4s). The engine task is ALIVE — `done=False,
cancelled=False` — and parked at `engine.py:1019`, which is
`await tasks_mod.execute_task(...)`. The heartbeat is ticked at `engine.py:832`,
once per POLLING-LOOP iteration, i.e. only between tasks. So any task that runs
longer than `_BLACKOUT_THRESHOLD_S` (300s) produces no tick and the detector
declares a blackout on a perfectly healthy engine. Both observations landing at
~300s is the signature of a task hitting a five-minute internal bound.

That is one signal standing for two very different states — "busy" and "wedged" —
which is the defect class §1 keeps recording.

WHY THIS IS NOT SIMPLY "TICK WHILE THE TASK RUNS". Ticking unconditionally for as
long as `execute_task` is on the stack would remove the FALSE blackout and the TRUE
one together: a task hung forever would keep the heartbeat fresh forever, and the
detector could never fire again. The wedge detector would be silently disarmed —
trading a noisy alarm for no alarm, which is worse.

So the ticker is BOUNDED. It keeps the engine honest for a normal long task and then
STOPS, deliberately letting the blackout fire for anything beyond the busy window.
The bound is what preserves the signal.
"""
from __future__ import annotations

import asyncio

import pytest

from aria_service.autonomous import engine


@pytest.mark.asyncio
async def test_a_long_task_keeps_the_engine_heartbeat_fresh():
    """THE FALSE BLACKOUT. A task running past the 300s threshold must still tick."""
    ticks = []
    await engine._heartbeat_during_task(
        "SLOW-TASK", ticks.append, interval=0.01, max_busy_s=0.05)
    assert len(ticks) >= 3, (
        f"the heartbeat must keep ticking while a task runs, got {len(ticks)}")
    assert all(t == "autonomous_engine" for t in ticks)


@pytest.mark.asyncio
async def test_ticking_STOPS_after_the_busy_window_so_a_wedge_is_still_detectable():
    """THE HALF THAT KEEPS THE DETECTOR ARMED.

    If this ever becomes unbounded, a task hung forever holds the heartbeat fresh
    forever and R-F1146 can never fire again — the alarm would be disarmed by the
    fix meant to make it accurate.
    """
    ticks = []
    await engine._heartbeat_during_task(
        "HUNG-TASK", ticks.append, interval=0.01, max_busy_s=0.05)
    n_at_window = len(ticks)

    # Well past the window: the count must NOT keep climbing.
    await asyncio.sleep(0.08)
    assert len(ticks) == n_at_window, (
        "the ticker must stop at the busy window — otherwise a hung task is "
        "indistinguishable from a healthy one, forever")


@pytest.mark.asyncio
async def test_the_ticker_is_cancellable_and_leaves_nothing_running():
    """It runs as a sibling task of the execution; completing the task must end it."""
    ticks = []
    t = asyncio.create_task(
        engine._heartbeat_during_task("T", ticks.append, interval=0.01, max_busy_s=10))
    await asyncio.sleep(0.03)
    t.cancel()
    with pytest.raises(asyncio.CancelledError):
        await t
    stopped_at = len(ticks)
    await asyncio.sleep(0.03)
    assert len(ticks) == stopped_at, "a cancelled ticker must stop ticking"


@pytest.mark.asyncio
async def test_a_failing_tick_never_breaks_the_engine():
    """Observability must not be able to kill the loop it observes (§21a)."""
    calls = []

    def _boom(_agent):
        calls.append(1)
        raise RuntimeError("heartbeat store unavailable")

    # Must not raise.
    await engine._heartbeat_during_task("T", _boom, interval=0.01, max_busy_s=0.04)
    assert calls, "it should have attempted to tick"


def test_execute_task_is_wrapped_by_the_ticker():
    """Pinned by source: the fix is worthless if the call site does not use it.

    engine.py:1019 is the exact line the live blackout dumps name.
    """
    from aria_service.tests._source_probe import function_source

    src = function_source(engine, "_engine_loop")
    assert "_heartbeat_during_task" in src, (
        "the execute_task await must be wrapped by the heartbeat ticker — that call "
        "site IS the defect the blackout dumps point at")
    # the ticker must be torn down whatever happens to the task
    assert "finally" in src
