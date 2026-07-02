"""R-F2300 — orphaned async-DD 'running' placeholders get a terminal state.

An async DD (R-F2250) runs its layers in an in-process background task. A service
restart (deploy / R-F2277 os._exit / crash) kills the task but leaves the
status='running' placeholder forever, so the chat/report poll spins with a frozen
"running · ETA …" (2026-07-02: a Modirum Gespi deep DD sat 'running' 12.5h after a
deploy). reconcile_stale_running_dds() marks placeholders older than the threshold
(or with no parseable started_at) FAILED so the UI stops spinning.
"""
from __future__ import annotations

import time
from datetime import datetime, timezone

import pytest

from aria_service.intel import dd_orchestrator as ddo
from aria_service.intel import redis_store as rs


@pytest.mark.asyncio
async def test_reconcile_marks_stale_running_failed(monkeypatch):
    now = time.time()
    old = datetime.fromtimestamp(now - 4000, timezone.utc).isoformat()   # >30 min → stale
    recent = datetime.fromtimestamp(now - 120, timezone.utc).isoformat()  # 2 min → live
    rows = [
        ("crucix:dd:report:dd_old", {"run_id": "dd_old", "status": "running", "started_at": old}),
        ("crucix:dd:report:dd_new", {"run_id": "dd_new", "status": "running", "started_at": recent}),
        ("crucix:dd:report:dd_nostart", {"run_id": "dd_nostart", "status": "running"}),  # no ts → stale
        ("crucix:dd:report:dd_done", {"run_id": "dd_done", "status": "done"}),           # terminal → skip
    ]

    async def _scan(pattern, count=200):
        assert pattern == "crucix:dd:report:*"
        return list(rows)
    monkeypatch.setattr(rs, "scan_json", _scan)

    failed = []
    async def _fail(run_id, error):
        failed.append(run_id)
        assert "re-run" in error.lower()
    monkeypatch.setattr(ddo, "mark_dd_failed", _fail)

    res = await ddo.reconcile_stale_running_dds()

    assert set(failed) == {"dd_old", "dd_nostart"}, failed   # stale ones only
    assert "dd_new" not in failed        # a genuinely-running (2-min) DD is untouched
    assert "dd_done" not in failed       # terminal reports untouched
    assert res == {"scanned": 3, "reconciled": 2}, res


@pytest.mark.asyncio
async def test_reconcile_respects_custom_threshold(monkeypatch):
    now = time.time()
    ts = datetime.fromtimestamp(now - 300, timezone.utc).isoformat()  # 5 min old
    rows = [("crucix:dd:report:dd_x", {"run_id": "dd_x", "status": "running", "started_at": ts})]
    async def _scan(pattern, count=200):
        return list(rows)
    monkeypatch.setattr(rs, "scan_json", _scan)
    failed = []
    async def _fail(run_id, error):
        failed.append(run_id)
    monkeypatch.setattr(ddo, "mark_dd_failed", _fail)

    # 5 min < 30 min default → not reconciled
    assert (await ddo.reconcile_stale_running_dds())["reconciled"] == 0
    # 5 min > 60 s threshold → reconciled
    assert (await ddo.reconcile_stale_running_dds(max_age_s=60))["reconciled"] == 1
    assert failed == ["dd_x"]


@pytest.mark.asyncio
async def test_reconcile_never_raises_on_scan_error(monkeypatch):
    async def _boom(pattern, count=200):
        raise RuntimeError("state store down")
    monkeypatch.setattr(rs, "scan_json", _boom)
    assert await ddo.reconcile_stale_running_dds() == {"scanned": 0, "reconciled": 0}
