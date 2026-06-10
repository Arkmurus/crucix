"""
R-F1142 — Capability test: DD trigger monitor wired into autonomous scheduler.

Verifies that:
1. AutonomousScheduler.start() registers a "dd_monitor" task
2. The _run_dd_monitor method imports and calls monitor_and_trigger
3. The scheduler starts with 5 tasks (dd_monitor + gap_fixer + self_diagnostic + adversarial + ecosystem_optimize)
"""
from __future__ import annotations

import asyncio
import logging
from unittest.mock import AsyncMock, patch

import pytest

from aria_service.intel.autonomous_scheduler import AutonomousScheduler


@pytest.mark.asyncio
async def test_rf1142_scheduler_registers_dd_monitor_task():
    """Scheduler registers dd_monitor task on start()."""
    scheduler = AutonomousScheduler()
    assert "dd_monitor" not in scheduler._tasks

    # Start scheduler — it creates tasks immediately
    await scheduler.start()
    try:
        assert "dd_monitor" in scheduler._tasks
        # R-F1490: added vault_retry task
        assert len(scheduler._tasks) == 6  # dd_monitor + gap_fixer + self_diagnostic + adversarial + ecosystem_optimize + vault_retry
    finally:
        await scheduler.stop()


@pytest.mark.asyncio
async def test_rf1142_dd_monitor_calls_monitor_and_trigger():
    """_run_dd_monitor imports and calls monitor_and_trigger from dd_trigger_pipeline."""
    scheduler = AutonomousScheduler()

    with patch(
        "aria_service.intel.dd_trigger_pipeline.monitor_and_trigger",
        new_callable=AsyncMock,
    ) as mock_monitor:
        mock_monitor.return_value = {
            "signals_found": 3,
            "matches_found": 2,
            "triggers_fired": 1,
            "triggers_skipped": 1,
            "duration_ms": 150,
        }

        await scheduler._run_dd_monitor()

        mock_monitor.assert_awaited_once()


@pytest.mark.asyncio
async def test_rf1142_dd_monitor_handles_exception_gracefully():
    """_run_dd_monitor logs debug and does not raise when monitor_and_trigger fails."""
    scheduler = AutonomousScheduler()

    with patch(
        "aria_service.intel.dd_trigger_pipeline.monitor_and_trigger",
        new_callable=AsyncMock,
    ) as mock_monitor:
        mock_monitor.side_effect = ValueError("test error")

        # Should not raise
        await scheduler._run_dd_monitor()

        mock_monitor.assert_awaited_once()
