"""R-F1820 — H3: DD-report by-id ownership (GET + DELETE).

Authorization review H3 (HIGH): /api/aria/dd/report/:run_id (GET and DELETE) took no
user param and returned/deleted any report by id — while the sibling LIST endpoint
scoped per user (R-F607). Any user could read or delete another user's confidential
DD report by run_id.

Fix: both endpoints enforce ownership against the report's stored user_id (the
ARKDDReport.user_id field); Node pins user_id from the JWT and strips client values.
Legacy pre-R-F607 reports (user_id='') and the admin/no-filter path (user_id='') are
not blocked (matches list_reports semantics).

Capability test drives the REAL endpoints: cross-user → 404 (and DELETE not executed),
owner → ok, admin/no-filter → ok.
"""
import pytest
from fastapi import HTTPException

from aria_service.routes import aria as A


@pytest.fixture
def ddstore(monkeypatch):
    from aria_service.intel import dd_orchestrator as ddo
    report = {"run_id": "r1", "user_id": "alice", "identity": {"entity_name": "Acme"}}
    state = {"deleted": False}

    async def _get(run_id):
        return dict(report) if run_id == "r1" else None

    async def _del(run_id):
        state["deleted"] = True
        return {"deleted": True, "run_id": run_id}

    monkeypatch.setattr(ddo, "get_report", _get)
    monkeypatch.setattr(ddo, "delete_report", _del)
    return state


@pytest.mark.asyncio
async def test_dd_report_blocks_cross_user(ddstore):
    with pytest.raises(HTTPException) as e:
        await A.dd_report_ep(run_id="r1", user_id="bob")
    assert e.value.status_code == 404


@pytest.mark.asyncio
async def test_dd_report_owner_ok(ddstore):
    out = await A.dd_report_ep(run_id="r1", user_id="alice")
    assert out["user_id"] == "alice"


@pytest.mark.asyncio
async def test_dd_report_admin_no_filter_ok(ddstore):
    out = await A.dd_report_ep(run_id="r1", user_id="")  # admin / autonomous path
    assert out["run_id"] == "r1"


@pytest.mark.asyncio
async def test_dd_report_delete_blocks_cross_user_and_does_not_delete(ddstore):
    with pytest.raises(HTTPException) as e:
        await A.dd_report_delete_ep(run_id="r1", user_id="bob")
    assert e.value.status_code == 404
    assert ddstore["deleted"] is False, "cross-user DELETE must NOT execute"


@pytest.mark.asyncio
async def test_dd_report_delete_owner_executes(ddstore):
    await A.dd_report_delete_ep(run_id="r1", user_id="alice")
    assert ddstore["deleted"] is True
