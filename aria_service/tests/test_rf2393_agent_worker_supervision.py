"""R-F2393 — agent/worker DD: singleton loops stay supervised.

The MVP multi-user posture depends on background workers not escaping the
lifespan supervisor. If a singleton monitor/scheduler starts outside _bg_task it
can miss strong references, exception logging, respawn, and shutdown
cancellation.
"""
from __future__ import annotations

from pathlib import Path


def _main_src() -> str:
    return Path("aria_service/main.py").read_text(encoding="utf-8")


def test_wiring_monitor_is_registered_with_bg_supervisor():
    src = _main_src()
    assert "_wiring_monitor_task = _bg_task(" in src
    assert "factory=_wm.monitor_loop" in src
    assert "_wiring_monitor_task = _wm.start_monitor()" not in src


def test_autonomous_scheduler_lifecycle_is_supervised_and_stops_children():
    src = _main_src()
    assert "async def _autonomous_scheduler_loop():" in src
    assert "_scheduler_task = _bg_task(" in src
    assert "name=\"autonomous_scheduler\"" in src
    assert "factory=_autonomous_scheduler_loop" in src
    assert "await _scheduler.stop()" in src
    assert "_scheduler_task = asyncio.create_task(_scheduler.start()" not in src
