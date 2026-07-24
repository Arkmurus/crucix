"""R-F3009 — a DD killed by an app restart is RE-LAUNCHED (retry-capped) by the
reconcile, not just marked 'failed'. Combined with R-F3008 (foreground priority)
and R-F3010 (deploy-coordination), a DD can no longer silently vanish on a deploy.

mark_dd_running now stores the full target + a resume_count; reconcile resumes an
orphan when it has the target and is under the cap, else marks it failed (R-F2300).
After a restart the in-process gauge is 0, so any 'running' placeholder is
immediately treated as orphaned.
"""
import asyncio
from unittest.mock import patch, AsyncMock

from aria_service.intel import dd_orchestrator as ddo


def _reset_gauge():
    while ddo.dd_inflight_count() > 0:
        ddo._dd_inflight_dec()


def test_rf3009_mark_running_stores_resume_target():
    _reset_gauge()
    async def go():
        rid = "dd_rf3009_store_x1"
        await ddo.mark_dd_running(rid, "Acme Ltd", "standard",
                                  target={"name": "Acme Ltd", "type": "company"})
        rep = await ddo.get_report(rid)
        assert rep["resume_target"] == {"name": "Acme Ltd", "type": "company"}
        assert rep["resume_count"] == 0
        await ddo.mark_dd_failed(rid, "test cleanup")
    asyncio.run(go())


def test_rf3009_reconcile_resumes_orphan_with_target():
    _reset_gauge()
    async def go():
        rid = "dd_rf3009_resume_x2"
        await ddo.mark_dd_running(rid, "Acme Ltd", "standard",
                                  target={"name": "Acme Ltd", "type": "company"})
        with patch.object(ddo, "_resume_orphaned_dd", new=AsyncMock()) as m:
            res = await ddo.reconcile_stale_running_dds(max_age_s=0)
        rep = await ddo.get_report(rid)
        assert rep["resume_count"] == 1, "resume must be counted"
        assert rep["status"] == "running", "a resumed run stays 'running' (not failed)"
        assert m.await_count >= 1, "the DD must actually be re-launched"
        await ddo.mark_dd_failed(rid, "test cleanup")
    asyncio.run(go())


def test_rf3009_reconcile_fails_orphan_without_target():
    _reset_gauge()
    async def go():
        rid = "dd_rf3009_notarget_x3"
        await ddo.mark_dd_running(rid, "NoTarget Ltd", "standard")  # no target stored
        with patch.object(ddo, "_resume_orphaned_dd", new=AsyncMock()):
            await ddo.reconcile_stale_running_dds(max_age_s=0)
        rep = await ddo.get_report(rid)
        assert rep["status"] == "failed", "no stored target → terminal failed (R-F2300 behaviour)"
    asyncio.run(go())


def test_rf3009_reconcile_caps_resumes():
    _reset_gauge()
    async def go():
        from aria_service.intel import redis_store as rs
        rid = "dd_rf3009_cap_x4"
        await ddo.mark_dd_running(rid, "Cap Ltd", "standard",
                                  target={"name": "Cap Ltd", "type": "company"})
        rep = await ddo.get_report(rid)
        rep["resume_count"] = ddo._MAX_DD_RESUMES        # already at the cap
        await rs.set_json(ddo.REPORT_REDIS_KEY.format(run_id=rid), rep, ex=ddo.REPORT_TTL_SECONDS)
        with patch.object(ddo, "_resume_orphaned_dd", new=AsyncMock()) as m:
            await ddo.reconcile_stale_running_dds(max_age_s=0)
        rep2 = await ddo.get_report(rid)
        assert rep2["status"] == "failed", "at the resume cap → failed, not resumed again"
    asyncio.run(go())
