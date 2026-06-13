"""
R-F1548 capability test: prove the collab bridge drain runs independently
of the autonomous engine, so Claude's notes are never stuck.
"""
import pytest


@pytest.mark.asyncio
async def test_collab_drain_task_in_scheduler():
    """The autonomous scheduler must include a collab bridge drain task."""
    from aria_service.intel.autonomous_scheduler import AutonomousScheduler
    
    import inspect
    source = inspect.getsource(AutonomousScheduler.start)
    assert "collab_drain" in source, (
        "AutonomousScheduler.start must include a collab_drain task"
    )
    assert "_drain_collab_bridge" in source, (
        "The collab_drain task must call _drain_collab_bridge"
    )


@pytest.mark.asyncio
async def test_collab_drain_method_exists():
    """AutonomousScheduler must have a _drain_collab_bridge method."""
    from aria_service.intel.autonomous_scheduler import AutonomousScheduler
    
    assert hasattr(AutonomousScheduler, "_drain_collab_bridge"), (
        "AutonomousScheduler must have _drain_collab_bridge method"
    )


@pytest.mark.asyncio
async def test_collab_drain_interval_is_short():
    """The collab drain interval must be 2 minutes or less."""
    from aria_service.intel.autonomous_scheduler import AutonomousScheduler
    
    import inspect
    source = inspect.getsource(AutonomousScheduler.start)
    # Find the collab_drain interval
    import re
    m = re.search(r'collab_drain.*?(\d+)', source)
    assert m, "Could not find collab_drain interval"
    interval = int(m.group(1))
    assert interval <= 300, (
        f"Collab drain interval should be <= 300s (5 min), got {interval}s"
    )
