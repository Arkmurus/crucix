"""R-F1700 — the dead+dark `gap_fixer` scheduler tick is removed.

`AutonomousScheduler._fix_gaps` (the 15-min "gap_fixer" tick) was a divergent
DUPLICATE of the live coder path (coder_entrypoint.start_aria_coder ->
coder.run_forever). It was broken 4 ways (wrong `from .gap_detector` import,
wrong GapDetector()/ARIACoder() ctors, `.get()` on a FixResult dataclass) and
every failure was swallowed at debug with NO brain signal — self-healing
theatre that ran-and-failed every 15 minutes invisibly.

This drives the REAL start() and asserts the dead tick is gone while the other
ticks still register (start() not broken).
"""
from __future__ import annotations

import asyncio
from unittest.mock import patch

import pytest

from aria_service.intel.autonomous_scheduler import AutonomousScheduler


def test_fix_gaps_method_deleted():
    assert not hasattr(AutonomousScheduler, "_fix_gaps"), (
        "the dead+dark _fix_gaps duplicate must be deleted"
    )


@pytest.mark.asyncio
async def test_start_does_not_register_gap_fixer_but_keeps_the_rest():
    sched = AutonomousScheduler()

    def _fake_create_task(coro, *a, **k):
        coro.close()  # don't schedule it; avoid 'never awaited' warning

        class _T:
            def cancel(self):
                pass

        return _T()

    with patch(
        "aria_service.intel.autonomous_scheduler.asyncio.create_task",
        side_effect=_fake_create_task,
    ):
        await sched.start()

    assert "gap_fixer" not in sched._tasks, "the dead gap_fixer tick must be gone"
    # start() is otherwise intact — the real ticks still register.
    assert "dd_monitor" in sched._tasks
    assert "self_diagnostic" in sched._tasks
    assert "collab_drain" in sched._tasks
