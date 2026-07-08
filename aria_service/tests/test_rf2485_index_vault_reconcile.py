"""R-F2485 — list_reports must reconcile the VOLATILE state_store index against the
DURABLE vault on EVERY read, so a user's own completed DD is visible even when the
index is a stale, non-empty, owner-less snapshot (index writes drop under the
R-F2277 storm; the old empty-only rebuild left the user seeing 0).

Two guarantees:
  (1) a completed vault case MISSING from the index is merged in, with its owner
      restored from dd_report_owners → the owner sees it, a phantom user does not.
  (2) an owner-less-but-PRESENT index row is healed from dd_report_owners.
Owners are never fabricated (legacy owner-less with no record stays hidden).
"""
import asyncio
import os
import tempfile

import aria_service.intel.dd_vault as _ddv
import aria_service.intel.redis_store as _rs
from aria_service.intel import dd_orchestrator as dor
from aria_service.intel.dd_vault import DDVault

OWNER = "user_owner_A"
PHANTOM = "user_phantom_Z"


def _vault_with_case(run_id, canonical, owner):
    fd, path = tempfile.mkstemp(prefix="rf2485_", suffix=".db")
    os.close(fd)
    v = DDVault(db_path=path)
    v.record_case(canonical_entity_id=canonical, entity_name="Acme Ltd",
                  latest_report_id=run_id)
    if owner:
        v.record_report_owner(run_id, canonical_entity_id=canonical, user_id=owner,
                              user_email_domain="a.com")
    return v


async def _list(vault, stale_index, *, user_id):
    dor._R2469_OWNER_BACKFILLED.clear()
    _store = list(stale_index)

    async def fake_get_json(key, *a, **k):
        if key == dor.REPORT_INDEX_KEY:
            return list(_store)
        if key == dor.WATCHLIST_KEY:
            return []
        return None

    async def fake_set_json(key, val, *a, **k):
        if key == dor.REPORT_INDEX_KEY:
            _store[:] = val
        return True

    async def fake_mutate(mutator, **k):
        new = mutator(list(_store))
        if new is not None:
            _store[:] = new
        return new

    orig = (_rs.get_json, _rs.set_json, _ddv.get_vault, dor._mutate_report_index)
    _rs.get_json, _rs.set_json = fake_get_json, fake_set_json
    _ddv.get_vault = lambda: vault
    dor._mutate_report_index = fake_mutate
    os.environ.pop("ARIA_DD_LEGACY_OWNER_FALLBACK", None)
    try:
        return await dor.list_reports(limit=50, user_id=user_id, user_email_domain=None)
    finally:
        _rs.get_json, _rs.set_json, _ddv.get_vault, dor._mutate_report_index = orig


def _names(rs):
    return {(r.get("entity_name") or "") for r in rs}


def test_missing_completed_case_reconciled_and_owner_restored():
    v = _vault_with_case("dd_new1", "company:GB:NEW1", OWNER)
    # A STALE, non-empty, owner-less index (a different run) — old code would NOT rebuild.
    stale = [{"run_id": "dd_stale_other", "entity_name": "Stale Co",
              "canonical_entity_id": "company:GB:STALE", "user_id": None,
              "created_at": "2026-07-08T00:00:00Z"}]
    owner_view = asyncio.run(_list(v, stale, user_id=OWNER))
    assert "Acme Ltd" in _names(owner_view), f"owner must see their reconciled completed DD, got {_names(owner_view)}"
    phantom_view = asyncio.run(_list(v, stale, user_id=PHANTOM))
    assert "Acme Ltd" not in _names(phantom_view), f"LEAK: phantom saw another user's report: {_names(phantom_view)}"


def test_present_ownerless_row_healed_from_durable_table():
    v = _vault_with_case("dd_new2", "company:GB:NEW2", OWNER)
    # The row is PRESENT in the index but owner-less (its owner IS recorded durably).
    stale = [{"run_id": "dd_new2", "entity_name": "Acme Ltd",
              "canonical_entity_id": "company:GB:NEW2", "user_id": None,
              "created_at": "2026-07-08T00:00:00Z"}]
    owner_view = asyncio.run(_list(v, stale, user_id=OWNER))
    assert "Acme Ltd" in _names(owner_view), f"owner-less-but-recorded row must heal to the owner, got {_names(owner_view)}"


def test_legacy_ownerless_with_no_record_stays_hidden():
    v = _vault_with_case("dd_legacy", "company:GB:LEG", owner=None)  # NO owner record
    owner_view = asyncio.run(_list(v, [], user_id=OWNER))
    assert "Acme Ltd" not in _names(owner_view), "owner-unknown case must NOT be fabricated onto a scoped user"


if __name__ == "__main__":
    test_missing_completed_case_reconciled_and_owner_restored()
    print("PASS test_missing_completed_case_reconciled_and_owner_restored")
    test_present_ownerless_row_healed_from_durable_table()
    print("PASS test_present_ownerless_row_healed_from_durable_table")
    test_legacy_ownerless_with_no_record_stays_hidden()
    print("PASS test_legacy_ownerless_with_no_record_stays_hidden")
    print("ALL PASS")
