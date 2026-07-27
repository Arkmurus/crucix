"""R-F3272 — `max_age_s=0` must mean "everything is stale", as documented.

    dd_orchestrator.py:14982
        if not (age is None or age > max_age_s):
            continue  # within the live-DD window — leave alone

Strictly greater. So `max_age_s=0` actually means "everything EXCEPT what started
this instant", and a run whose `started_at` lands on the same clock tick as `now`
has `age == 0.0` and is silently skipped.

HOW THIS SURFACED. `test_rf3009_reconcile_resumes_orphan_with_target` calls
`mark_dd_running(...)` and then `reconcile_stale_running_dds(max_age_s=0)` back to
back. On Windows the system clock granularity is ~15.6ms, so the two timestamps
routinely land on the same tick. Measured by running that single test 12 times in
isolation: 2/12 failures on one build, 1/12 on another — i.e. it fails on BOTH,
independent of any code change. A test that fails one run in ten makes every
"green suite" claim afterwards worthless, which is the real cost.

Production is not affected: the only production caller uses the 1800s default, and
`age > 1800` vs `age >= 1800` differ only for a run that is exactly 1800.000s old.
The defect is the CONTRACT — a caller who passes 0 does not get what the docstring
promises. Fixed with `>=`, which is also the correct reading of "older than the
window" at the boundary.

These tests pin the boundary directly instead of racing the clock, so they cannot
themselves become flaky.
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
        },
    )


def _run_reconcile(rows, max_age_s, now_ts):
    """Drive the real reconcile with a pinned clock, so age is EXACT.

    NOTE the patch targets: `reconcile_stale_running_dds` imports both
    `redis_store` and `time` INSIDE the function body, so there is no
    `dd_orchestrator.rs` attribute to patch — the real modules must be patched.
    """
    import time as _time_mod

    from aria_service.intel import redis_store as _rs

    async def go():
        with patch.object(_rs, "scan_json", new=AsyncMock(return_value=rows)), \
             patch.object(_rs, "set_json", new=AsyncMock(return_value=None)), \
             patch.object(ddo, "_resume_orphaned_dd", new=AsyncMock()) as resume, \
             patch.object(_time_mod, "time", return_value=now_ts):
            out = await ddo.reconcile_stale_running_dds(max_age_s=max_age_s)
        return out, resume
    return asyncio.run(go())


def _iso(ts: float) -> str:
    from datetime import datetime, timezone
    return datetime.fromtimestamp(ts, timezone.utc).isoformat()


def test_age_exactly_zero_is_stale_when_max_age_is_zero() -> None:
    """THE RACE, pinned: started_at == now, so age is exactly 0.0.

    This is the state the Windows clock produces for two back-to-back calls, and
    the `>` comparison skipped it.
    """
    now = 1_800_000_000.0
    rows = [_row("dd_boundary_zero", _iso(now))]
    out, resume = _run_reconcile(rows, max_age_s=0, now_ts=now)

    assert out["scanned"] == 1
    assert resume.await_count == 1, (
        "a run at age 0.0 was skipped with max_age_s=0 — the parameter is "
        "documented as 'everything is stale' but `>` excludes the current tick"
    )


def test_age_exactly_at_the_window_is_stale() -> None:
    """The same boundary at a real window: exactly max_age_s old counts as stale."""
    now = 1_800_000_000.0
    rows = [_row("dd_boundary_exact", _iso(now - 1800.0))]
    out, resume = _run_reconcile(rows, max_age_s=1800.0, now_ts=now)

    assert resume.await_count == 1, "a run exactly at the window edge must reconcile"


def test_a_genuinely_live_run_is_still_left_alone() -> None:
    """Regression: the guard must not become a blanket 'reconcile everything'.

    This is the property the `>` was protecting — a DD that is still running must
    not be torn down mid-flight.
    """
    now = 1_800_000_000.0
    rows = [_row("dd_still_live", _iso(now - 5.0))]
    out, resume = _run_reconcile(rows, max_age_s=1800.0, now_ts=now)

    assert resume.await_count == 0, "a 5s-old DD is live and must be left alone"


def test_unparseable_started_at_still_counts_as_stale() -> None:
    """Documented behaviour ('no parseable started_at counts as stale') preserved."""
    now = 1_800_000_000.0
    rows = [_row("dd_no_ts", "not-a-timestamp")]
    out, resume = _run_reconcile(rows, max_age_s=1800.0, now_ts=now)

    assert resume.await_count == 1
