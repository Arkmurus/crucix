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

    # R-F3532 — the fake must cover get_json_strict too. delete_report now uses the
    # STRICT read (so an unreadable store is not reported as "report not found"),
    # and an unpatched strict read means the code under test reads the REAL store
    # while the test writes to this dict — a fixture that silently tests nothing.
    async def get_json_strict(k):
        await asyncio.sleep(0)
        return store.get(k)

    for name, fn in [("set_json", set_json), ("get_json", get_json), ("delete", delete),
                     ("get_json_strict", get_json_strict),
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

    # R-F3532 — this fake was long-red (recorded in docs/suite_baseline_2026_07_30.md)
    # because it still modelled delete_case(cid), which R-F2653 REPLACED with
    # remove_report_from_case(cid, run_id) precisely so one tenant's delete stops
    # wiping a shared entity's case for everyone. The guard was asserting an API
    # the code no longer calls, i.e. protecting nothing. Modelled correctly now.
    class _V:
        def list_all(self, limit=500):
            return []

        def remove_report_from_case(self, cid, run_id):
            deleted.append(cid)
            return {"found": cid == "company:acme:GB", "case_deleted": True,
                    "remaining_reports": 0}

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
async def test_delete_report_absent_is_success_but_claims_no_removal(fake_rs, monkeypatch):
    """R-F2388 + R-F3532 — never CLAIM a removal that did not happen, but do not
    call an already-absent report a failure either.

    R-F2388's protection is intact: every backing-store flag stays False, so no
    consumer can read this as "a report was removed". What changed is the verdict
    on a run that is genuinely gone. `ok=False` made an orphan row (an index entry
    whose blob aged out, or simply a second click) PERMANENTLY undeletable in the
    UI — the surface reported "Delete did not remove a report" and left the row on
    screen. The user's goal is that the report is gone; it is.
    """
    import aria_service.intel.dd_vault as dv

    class _V:
        def list_all(self, limit=500):
            return []

        def remove_report_from_case(self, cid, run_id):
            return {"found": False, "case_deleted": False, "remaining_reports": 0}

    monkeypatch.setattr(dv, "get_vault", lambda: _V())

    out = await ddo.delete_report("dd_missing")

    assert out["ok"] is True
    assert out["already_absent"] is True
    # R-F2388's actual invariant — no fabricated removal.
    assert out["blob_deleted"] is False
    assert out["index_entries_removed"] == 0
    assert out["vault_deleted"] is False
    assert out["error"] == ""
    assert out["store_error"] == ""


@pytest.mark.asyncio
async def test_delete_report_unreadable_store_is_a_failure_not_a_missing_report(fake_rs, monkeypatch):
    """R-F3532 — "I could not look" must never be reported as "it is not there".

    The old non-strict read returned None for BOTH, so a wedged state_store told
    the user their report did not exist. That is the R-F2664 clobber class applied
    to a delete receipt.
    """
    import aria_service.intel.redis_store as rs
    import aria_service.intel.dd_vault as dv

    class _V:
        def list_all(self, limit=500):
            return []

        def remove_report_from_case(self, cid, run_id):
            return {"found": False, "case_deleted": False, "remaining_reports": 0}

    monkeypatch.setattr(dv, "get_vault", lambda: _V())

    async def _boom(_k):
        raise RuntimeError("StoreReadError")

    monkeypatch.setattr(rs, "get_json_strict", _boom)

    out = await ddo.delete_report("dd_unreadable")

    assert out["ok"] is False, "an unreadable store must not report success"
    assert out["already_absent"] is False, "absence was never established"
    assert out["store_error"] == "RuntimeError"
    assert "could not be read" in out["error"]
