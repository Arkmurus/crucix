"""R-F3273 — the resumed DD task was unreferenced and could vanish before running.

    dd_orchestrator.py:15008
        asyncio.create_task(_resume_orphaned_dd(...))     # nothing holds the result
        resumed += 1
        logger.warning("[R-F3009] resuming restart-killed DD %s ...")

`asyncio.create_task` returns a task the event loop holds only WEAKLY. CPython's
own docs say to save a reference or the task may "disappear mid-execution". This
file already knows that — line 10596 keeps `_AM_FOLLOWUP_TASKS` for exactly this
reason and cites R-F1363: "a task with no live reference never executes". The
resume path never got the same treatment.

THE COST. R-F3009 exists so "a DD can no longer silently vanish on a deploy". But
the resume it launches could itself be collected before it ran, while the reconcile
had ALREADY incremented `resume_count`, written status back, and logged
"resuming restart-killed DD ... (attempt 1/2)". So the ledger says resumed, the
user sees 'running', and nothing is running — the exact failure R-F3009 prevents,
reintroduced by the mechanism meant to prevent it. Worse, `resume_count` is
consumed, so the retry budget burns down on resumes that never happened.

HOW IT SURFACED. `test_rf3009_reconcile_resumes_orphan_with_target` asserts
`_resume_orphaned_dd` was awaited and failed intermittently — 1-2 runs in 12-15,
on every build tested. That was not clock flakiness (my first diagnosis, and it
was wrong: the `>=` boundary fix in R-F3272 did not change the rate). The captured
failure shows the reconcile logging "1 resumed + 0 failed" while the mock's
await_count was 0 — the task was created and never ran.
"""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

import pytest

from aria_service.intel import dd_orchestrator as ddo


def _row(run_id: str, started_iso: str) -> tuple[str, dict]:
    return (
        f"crucix:dd:report:{run_id}",
        {
            "run_id": run_id,
            "status": "running",
            "started_at": started_iso,
            "resume_target": {"name": "Acme Ltd", "type": "company"},
            "resume_count": 0,
            "orchestrator_mode": "standard",
        },
    )


def _iso_now_minus(seconds: float) -> str:
    from datetime import datetime, timezone
    import time as _t
    return datetime.fromtimestamp(_t.time() - seconds, timezone.utc).isoformat()


def test_the_resume_task_is_retained_so_it_cannot_be_collected() -> None:
    """A strong reference must exist the moment the task is created."""
    from aria_service.intel import redis_store as _rs

    async def go():
        rows = [_row("dd_rf3273_retained", _iso_now_minus(3600))]
        started = asyncio.Event()

        async def _slow_resume(*a, **k):
            started.set()
            await asyncio.sleep(0.05)

        with patch.object(_rs, "scan_json", new=AsyncMock(return_value=rows)), \
             patch.object(_rs, "set_json", new=AsyncMock(return_value=None)), \
             patch.object(ddo, "_resume_orphaned_dd", new=_slow_resume):
            out = await ddo.reconcile_stale_running_dds(max_age_s=1800)
            # THE ASSERTION: a live reference exists while the task is in flight.
            assert getattr(ddo, "_DD_RESUME_TASKS", None) is not None, (
                "no task registry — the resume task is unreferenced and the loop "
                "holds it only weakly (R-F1363)"
            )
            assert len(ddo._DD_RESUME_TASKS) >= 1, (
                "the resume task was created without retaining a strong reference; "
                "it can be garbage-collected before it runs, while resume_count has "
                "already been spent"
            )
            # and it genuinely runs to completion
            await asyncio.wait_for(started.wait(), timeout=2.0)
            await asyncio.gather(*list(ddo._DD_RESUME_TASKS), return_exceptions=True)
        # NOTE: reconcile returns {scanned, reconciled} only — `resumed` is logged
        # but not returned, and test_rf2300 pins that shape. Assert on the work
        # actually done rather than on a key the contract does not carry.
        assert out["scanned"] == 1
    asyncio.run(go())


def test_the_registry_is_drained_so_it_cannot_leak() -> None:
    """A retention set that only grows is a memory leak — it must self-clean."""
    from aria_service.intel import redis_store as _rs

    async def go():
        rows = [_row("dd_rf3273_drain", _iso_now_minus(3600))]
        with patch.object(_rs, "scan_json", new=AsyncMock(return_value=rows)), \
             patch.object(_rs, "set_json", new=AsyncMock(return_value=None)), \
             patch.object(ddo, "_resume_orphaned_dd", new=AsyncMock()):
            await ddo.reconcile_stale_running_dds(max_age_s=1800)
            await asyncio.gather(*list(ddo._DD_RESUME_TASKS), return_exceptions=True)
            await asyncio.sleep(0)      # let done-callbacks fire
        assert len(ddo._DD_RESUME_TASKS) == 0, (
            "finished resume tasks are never discarded — the set grows forever"
        )
    asyncio.run(go())


def test_the_resume_actually_runs_deterministically() -> None:
    """The R-F3009 guarantee, asserted without racing the scheduler.

    The original test asserted await_count immediately after reconcile returned,
    which depends on whether the loop had scheduled the detached task yet. Awaiting
    the retained tasks makes the same guarantee deterministic.
    """
    from aria_service.intel import redis_store as _rs

    async def go():
        rows = [_row("dd_rf3273_runs", _iso_now_minus(3600))]
        resume = AsyncMock()
        with patch.object(_rs, "scan_json", new=AsyncMock(return_value=rows)), \
             patch.object(_rs, "set_json", new=AsyncMock(return_value=None)), \
             patch.object(ddo, "_resume_orphaned_dd", new=resume):
            await ddo.reconcile_stale_running_dds(max_age_s=1800)
            await asyncio.gather(*list(ddo._DD_RESUME_TASKS), return_exceptions=True)
        assert resume.await_count == 1, "the DD must actually be re-launched"
    asyncio.run(go())
