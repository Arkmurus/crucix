"""R-F2343 — report-index atomicity.

All report-index read-modify-writes go through _mutate_report_index (one lock + read the
CURRENT index inside the lock), so concurrent DD starts/completions/failures can't clobber
each other's rows. mark_dd_failed UPSERTs so a fast-fail DD ALWAYS shows 'failed', never
vanishes. The fake store below YIELDS on every op, so without the lock the concurrent
read-modify-writes would interleave and lose updates — the tests prove they don't.
"""
import asyncio
import fnmatch

import pytest

from aria_service.intel import dd_orchestrator as ddo


@pytest.fixture
def fake_rs(monkeypatch):
    store: dict = {}
    import aria_service.intel.redis_store as rs

    async def set_json(k, v, ex=None, keepttl=False):
        await asyncio.sleep(0)                 # yield → enable interleaving
        store[k] = v
        return True

    async def get_json(k):
        await asyncio.sleep(0)
        return store.get(k)

    async def delete(k):
        await asyncio.sleep(0)
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
async def test_concurrent_starts_no_clobber(fake_rs):
    # 8 async DDs started at the same instant — every row must survive.
    await asyncio.gather(*[
        ddo.mark_dd_running(f"dd_c{i}", f"Co{i}", "standard", None, user_id="u1")
        for i in range(8)
    ])
    idx = fake_rs["crucix:dd:report_index"]
    ids = {e["run_id"] for e in idx}
    assert len(idx) == 8
    assert all(f"dd_c{i}" in ids for i in range(8))     # none lost to a race


@pytest.mark.asyncio
async def test_completion_does_not_clobber_concurrent_running(fake_rs):
    await ddo.mark_dd_running("dd_A", "A Co", "standard", None, user_id="u1")
    await ddo.mark_dd_running("dd_B", "B Co", "standard", None, user_id="u1")  # started after A
    # A completes via the SAME atomic path (fresh read + replace A) — B must remain.
    new_entry = {"run_id": "dd_A", "entity_name": "A Co", "risk_classification": "GREEN", "user_id": "u1"}

    def _complete(index):
        index = [e for e in index if e.get("run_id") != "dd_A"]
        index.insert(0, new_entry)
        return index
    await ddo._mutate_report_index(_complete)
    ids = {e["run_id"] for e in fake_rs["crucix:dd:report_index"]}
    assert "dd_A" in ids and "dd_B" in ids             # B not clobbered by A's completion


@pytest.mark.asyncio
async def test_fast_fail_upserts_failed_row_with_owner(fake_rs):
    # simulate the fast-fail race: the raw placeholder exists (with owner) but no index row.
    fake_rs["crucix:dd:report:dd_F"] = {
        "run_id": "dd_F", "entity_name": "F Co", "user_id": "u1", "user_email_domain": "x.com",
    }
    await ddo.mark_dd_failed("dd_F", "boom before the row landed")
    idx = fake_rs.get("crucix:dd:report_index", [])
    row = next((e for e in idx if e["run_id"] == "dd_F"), None)
    assert row is not None                              # UPSERTed — never vanishes
    assert row["status"] == "failed"
    assert row["user_id"] == "u1"                       # owner recovered → visible to the user


@pytest.mark.asyncio
async def test_mark_failed_updates_existing_running_no_duplicate(fake_rs):
    await ddo.mark_dd_running("dd_G", "G Co", "standard", None, user_id="u1")
    await ddo.mark_dd_failed("dd_G", "boom")
    idx = fake_rs["crucix:dd:report_index"]
    rows = [e for e in idx if e["run_id"] == "dd_G"]
    assert len(rows) == 1 and rows[0]["status"] == "failed"   # updated in place, not duplicated


@pytest.mark.asyncio
async def test_delete_report_atomic_removal(fake_rs, monkeypatch):
    # keep dd_vault.delete_case a no-op (its own DB is irrelevant here)
    import aria_service.intel.dd_vault as dv
    class _V:
        def delete_case(self, *a, **k): return True
    monkeypatch.setattr(dv, "get_vault", lambda: _V())
    await ddo.mark_dd_running("dd_D", "D Co", "standard", None, user_id="u1")
    r = await ddo.delete_report("dd_D")
    assert r["index_entries_removed"] == 1
    ids = {e["run_id"] for e in fake_rs.get("crucix:dd:report_index", [])}
    assert "dd_D" not in ids


@pytest.mark.asyncio
async def test_delete_report_uses_canonical_vault_key(fake_rs, monkeypatch):
    """R-F2387 — deleting a DD row must remove the persistent vault case too."""
    import aria_service.intel.dd_vault as dv

    deleted = []

    class _V:
        def list_all(self, limit=500):
            return []

        def delete_case(self, cid):
            deleted.append(cid)
            return cid == "company:acme:GB"

    monkeypatch.setattr(dv, "get_vault", lambda: _V())
    fake_rs[ddo.REPORT_INDEX_KEY] = [
        {"run_id": "dd_Z", "canonical_entity_id": "company:acme:GB", "entity_name": "Acme"},
    ]
    fake_rs[ddo.REPORT_REDIS_KEY.format(run_id="dd_Z")] = {
        "run_id": "dd_Z",
        "canonical_entity_id": "company:acme:GB",
    }

    out = await ddo.delete_report("dd_Z")

    assert out["vault_deleted"] is True
    assert out["canonical_entity_ids_deleted"] == ["company:acme:GB"]
    assert deleted == ["company:acme:GB"]


@pytest.mark.asyncio
async def test_delete_report_false_when_nothing_removed(fake_rs, monkeypatch):
    """R-F2388 — success True must mean at least one backing store changed."""
    import aria_service.intel.dd_vault as dv

    class _V:
        def list_all(self, limit=500):
            return []

        def delete_case(self, cid):
            return False

    monkeypatch.setattr(dv, "get_vault", lambda: _V())

    out = await ddo.delete_report("dd_missing")

    assert out["ok"] is False
    assert out["blob_deleted"] is False
    assert out["index_entries_removed"] == 0
    assert out["vault_deleted"] is False
    assert "not found" in out["error"]
