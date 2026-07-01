"""R-F2250 — async DD (fire-and-poll): /dd/orchestrate returns a run_id immediately
+ runs the DD in the background, so a ~10-min DD can't die at the proxy timeout
('DD failed HTTP 500'). Placeholder helpers let the poller see running/failed/done.
"""
from __future__ import annotations
import asyncio
import inspect
from pathlib import Path

from aria_service.intel import dd_orchestrator as ddo


def test_run_id_threads_through_orchestrator():
    assert "run_id" in inspect.signature(ddo.orchestrate_dd).parameters
    assert "run_id" in inspect.signature(ddo._orchestrate_dd_impl).parameters


def test_mark_running_then_failed_round_trip():
    async def go():
        rid = "dd_testrf2250xx"
        await ddo.mark_dd_running(rid, "Acme Test Entity", "quick", "company:GB:x")
        rep = await ddo.get_report(rid)
        assert rep is not None, "placeholder not persisted"
        assert rep.get("status") == "running"
        assert rep.get("entity_name") == "Acme Test Entity"
        assert rep.get("async_mode") is True
        # a background failure must flip it to a terminal 'failed' (not stuck running)
        await ddo.mark_dd_failed(rid, "kaboom upstream error")
        rep2 = await ddo.get_report(rid)
        assert rep2.get("status") == "failed"
        assert "kaboom" in (rep2.get("error") or "")
    asyncio.run(go())


def test_endpoint_async_branch_source_contract():
    src = (Path(__file__).resolve().parent.parent / "routes" / "aria.py").read_text(encoding="utf-8")
    # the endpoint returns immediately with a poll key and backgrounds the DD
    assert "async_mode" in src and "poll_url" in src
    assert "mark_dd_running" in src
    assert "asyncio.create_task(_bg_dd())" in src
    assert "run_id=_run_id" in src  # the background DD uses the pre-assigned id


def test_frontend_polls_instead_of_sync_await():
    html = (Path(__file__).resolve().parents[2] / "public" / "dd-reports.html").read_text(encoding="utf-8")
    assert "body.async_mode = true" in html
    assert "/api/aria/dd/report/" in html  # polls the report
