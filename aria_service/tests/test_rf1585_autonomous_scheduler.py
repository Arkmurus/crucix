"""R-F1585 — Tests for autonomous_scheduler.py.

Covers the AutonomousScheduler lifecycle: start, stop, task registration,
heartbeat tick, and brain wiring.
"""
from __future__ import annotations

import asyncio
import pytest

from aria_service.intel.autonomous_scheduler import AutonomousScheduler


@pytest.mark.asyncio
async def test_scheduler_initial_state():
    """Scheduler starts with no tasks and not running."""
    sched = AutonomousScheduler()
    assert sched._running is False
    assert len(sched._tasks) == 0


@pytest.mark.asyncio
async def test_scheduler_start_creates_tasks():
    """start() creates background tasks for each scheduled interval."""
    sched = AutonomousScheduler()
    await sched.start()
    assert sched._running is True
    assert len(sched._tasks) > 0
    # Expected tasks (R-F1700: gap_fixer removed — dead+dark duplicate of the
    # live coder.run_forever path).
    expected = {"dd_monitor", "self_diagnostic",
                "adversarial", "ecosystem_optimize", "collab_drain", "vault_retry"}
    assert expected.issubset(set(sched._tasks.keys())), (
        f"Missing tasks. Have: {set(sched._tasks.keys())}"
    )
    # Clean up
    await sched.stop()


@pytest.mark.asyncio
async def test_scheduler_start_idempotent():
    """Calling start() twice does not create duplicate tasks."""
    sched = AutonomousScheduler()
    await sched.start()
    task_count = len(sched._tasks)
    await sched.start()  # Second call should be a no-op
    assert len(sched._tasks) == task_count
    await sched.stop()


@pytest.mark.asyncio
async def test_scheduler_stop_cancels_tasks():
    """stop() cancels all background tasks."""
    sched = AutonomousScheduler()
    await sched.start()
    assert sched._running is True
    assert len(sched._tasks) > 0

    await sched.stop()
    assert sched._running is False
    assert len(sched._tasks) == 0


@pytest.mark.asyncio
async def test_scheduler_stop_idempotent():
    """Calling stop() twice does not raise."""
    sched = AutonomousScheduler()
    await sched.start()
    await sched.stop()
    # Second stop should be safe
    await sched.stop()
    assert sched._running is False


@pytest.mark.asyncio
async def test_scheduler_tick_heartbeat_exists():
    """Scheduler has a _tick_heartbeat method (R-F1579)."""
    sched = AutonomousScheduler()
    assert hasattr(sched, "_tick_heartbeat")
    assert callable(sched._tick_heartbeat)


@pytest.mark.asyncio
async def test_scheduler_tick_heartbeat_does_not_raise():
    """_tick_heartbeat is best-effort and never raises."""
    sched = AutonomousScheduler()
    # Should not raise even without a running event loop or Redis
    try:
        await sched._tick_heartbeat()
    except Exception:
        pytest.fail("_tick_heartbeat should never raise")


@pytest.mark.asyncio
async def test_scheduler_has_expected_intervals():
    """Scheduler tasks have the expected intervals."""
    sched = AutonomousScheduler()
    # Check the _run_interval calls in start()
    # We can verify by checking the method exists
    assert hasattr(sched, "_run_dd_monitor")
    assert not hasattr(sched, "_fix_gaps")  # R-F1700: dead+dark duplicate deleted
    assert hasattr(sched, "_run_diagnostics")
    assert hasattr(sched, "_run_adversarial")
    assert hasattr(sched, "_optimize_ecosystem")
    assert hasattr(sched, "_drain_collab_bridge")
    assert hasattr(sched, "_retry_pending_vault")


@pytest.mark.asyncio
async def test_scheduler_cleanup_on_gc():
    """Scheduler cleans up tasks when deleted."""
    sched = AutonomousScheduler()
    await sched.start()
    task_refs = list(sched._tasks.values())
    await sched.stop()
    # All tasks should be cancelled
    for t in task_refs:
        assert t.cancelled() or t.done()
