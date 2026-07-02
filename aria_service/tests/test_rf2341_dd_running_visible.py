"""R-F2341 — an async ("New DD" button) running DD must appear in the reports list
immediately (owner-scoped), show 'failed' when it fails/orphans, and never duplicate.
Before this, mark_dd_running wrote only a raw placeholder — list_reports (index-based)
never saw it, so a running DD was invisible and the 'appears automatically' message lied.
"""
import fnmatch

import pytest

from aria_service.intel import dd_orchestrator as ddo


@pytest.fixture
def fake_rs(monkeypatch):
    store: dict = {}
    import aria_service.intel.redis_store as rs

    async def set_json(k, v, ex=None, keepttl=False):
        store[k] = v
        return True

    async def get_json(k):
        return store.get(k)

    async def delete(k):
        return store.pop(k, None) is not None

    async def scan_json(pat, count=200):
        return [(k, v) for k, v in list(store.items()) if fnmatch.fnmatch(k, pat)]

    async def scan_keys(pat, count=200):
        return [k for k in list(store) if fnmatch.fnmatch(k, pat)]

    for name, fn in [("set_json", set_json), ("get_json", get_json), ("delete", delete),
                     ("scan_json", scan_json), ("scan_keys", scan_keys)]:
        monkeypatch.setattr(rs, name, fn)
    return store


@pytest.mark.asyncio
async def test_running_dd_visible_in_owner_list(fake_rs):
    await ddo.mark_dd_running("dd_r1", "Modirum Gespi", "standard", None,
                              user_id="u1", user_email_lower="op@x.com",
                              user_email_domain="x.com")
    rows = await ddo.list_reports(user_id="u1", user_email_domain="x.com")
    run = next((r for r in rows if r.get("run_id") == "dd_r1"), None)
    assert run is not None                        # visible immediately (the operator's fix)
    assert run["status"] == "running"
    assert run["entity_name"] == "Modirum Gespi"


@pytest.mark.asyncio
async def test_mark_running_dedups_same_run_id(fake_rs):
    await ddo.mark_dd_running("dd_r2", "A Co", "standard", None, user_id="u1")
    await ddo.mark_dd_running("dd_r2", "A Co", "standard", None, user_id="u1")
    idx = fake_rs["crucix:dd:report_index"]
    assert sum(1 for e in idx if e["run_id"] == "dd_r2") == 1   # no duplicate row


@pytest.mark.asyncio
async def test_failed_dd_shows_failed_in_index(fake_rs):
    await ddo.mark_dd_running("dd_r3", "B Co", "standard", None, user_id="u1")
    await ddo.mark_dd_failed("dd_r3", "boom")
    row = next(e for e in fake_rs["crucix:dd:report_index"] if e["run_id"] == "dd_r3")
    assert row["status"] == "failed"


@pytest.mark.asyncio
async def test_orphaned_running_reconciled_to_failed(fake_rs):
    await ddo.mark_dd_running("dd_r4", "C Co", "standard", None, user_id="u1")
    # simulate a restart-orphaned placeholder (old started_at -> stale)
    fake_rs["crucix:dd:report:dd_r4"]["started_at"] = "2000-01-01T00:00:00+00:00"
    res = await ddo.reconcile_stale_running_dds(max_age_s=60)
    assert res["reconciled"] >= 1
    row = next(e for e in fake_rs["crucix:dd:report_index"] if e["run_id"] == "dd_r4")
    assert row["status"] == "failed"                # orphan shows Failed, not stuck Running
