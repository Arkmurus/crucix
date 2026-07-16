"""R-F2652 / R-F2653 / R-F2654 — DD reports vault↔index durability + multi-tenant safety.

dd_cases (dd_vault.py) is keyed by canonical_entity_id and SHARED across every
tenant that has DD'd the entity: latest_report_id + each id in previous_report_ids
can belong to a DIFFERENT user. Three interlocking defects made a user's report
vanish or destroyed another tenant's data under the R-F2277 write storm:

  R-F2653 — delete_report called dd_vault.delete_case(cid), which DELETED the whole
            shared dd_cases row + ALL cross-references. One tenant deleting THEIR
            report wiped the case-file / version-chain / cross-refs for EVERY tenant.
  R-F2652 — the list_reports reconcile restored only latest_report_id per entity, so
            a user's OWN run demoted to a "previous" id (a colleague re-ran the same
            entity) was never restored when its volatile index row was dropped.
  R-F2654 — get_report_owner read ONLY the volatile index, so a same-company
            colleague opening a SHARED report whose index row was evicted got 404
            even though the durable dd_report_owners table still had the owner.

These tests drive the REAL fixed paths (remove_report_from_case, list_reports,
get_report_owner) with an ISOLATED vault (tmp DB — never /data) and an in-memory
fake state_store, and assert the multi-tenant outcomes.
"""

from __future__ import annotations

from typing import Any

import pytest

from aria_service.intel import dd_vault


def _fresh_vault(tmp_path) -> dd_vault.DDVault:
    return dd_vault.DDVault(db_path=str(tmp_path / "dd_vault_test.db"))


def _seed_two_tenant_case(v: dd_vault.DDVault) -> None:
    """Entity X: tenant-A ran run_A first, tenant-B later → latest=run_B, prev=[run_A]."""
    v.record_case("ent_X", "Acme Ltd", entity_type="company",
                         latest_report_id="run_A")
    v.record_case("ent_X", "Acme Ltd", entity_type="company",
                         latest_report_id="run_B")
    v.record_report_owner("run_A", canonical_entity_id="ent_X",
                          user_id="userA", user_email_domain="a.com")
    v.record_report_owner("run_B", canonical_entity_id="ent_X",
                          user_id="userB", user_email_domain="b.com")


# ── R-F2653 — remove ONE report, never wipe the shared case ──────────────────

def test_remove_report_keeps_shared_case_for_other_tenant(tmp_path) -> None:
    """THE CROSS-TENANT FIX: tenant-A deleting run_A must NOT destroy tenant-B's case."""
    v = _fresh_vault(tmp_path)
    _seed_two_tenant_case(v)

    res = v.remove_report_from_case("ent_X", "run_A")

    assert res == {"found": True, "case_deleted": False, "remaining_reports": 1}
    case = v.get_case("ent_X")
    assert case is not None, "shared case must survive — tenant-B still has run_B"
    assert case["latest_report_id"] == "run_B"
    import json
    assert "run_A" not in json.loads(case.get("previous_report_ids") or "[]")


def test_remove_last_report_deletes_the_case(tmp_path) -> None:
    """Only when the ENTITY's last report is removed does the case (and cross-refs) go."""
    v = _fresh_vault(tmp_path)
    v.record_case("ent_Y", "Solo Ltd", latest_report_id="run_solo")
    v.add_cross_reference("ent_Y", "ent_Z", "linked")

    res = v.remove_report_from_case("ent_Y", "run_solo")

    assert res["case_deleted"] is True and res["remaining_reports"] == 0
    assert v.get_case("ent_Y") is None
    assert v.get_cross_references("ent_Y") == []  # cross-refs cleared only now


def test_remove_latest_promotes_previous(tmp_path) -> None:
    """Removing the latest promotes the newest previous run to latest."""
    v = _fresh_vault(tmp_path)
    _seed_two_tenant_case(v)  # latest=run_B, prev=[run_A]

    res = v.remove_report_from_case("ent_X", "run_B")

    assert res["case_deleted"] is False and res["remaining_reports"] == 1
    assert v.get_case("ent_X")["latest_report_id"] == "run_A"


def test_remove_unrelated_run_is_noop(tmp_path) -> None:
    """A run not in the case must never touch the shared row."""
    v = _fresh_vault(tmp_path)
    v.record_case("ent_X", "Acme Ltd", latest_report_id="run_A")

    res = v.remove_report_from_case("ent_X", "run_NOT_HERE")

    assert res["found"] is False and res["case_deleted"] is False
    assert v.get_case("ent_X")["latest_report_id"] == "run_A"


# ── in-memory fake state_store for the async list_reports / get_report_owner path ──

class _FakeRS:
    def __init__(self) -> None:
        self.store: dict[str, Any] = {}

    async def get_json(self, key: str):
        return self.store.get(key)

    async def set_json(self, key: str, value, *a: Any, **k: Any):
        self.store[key] = value


@pytest.fixture
def fake_rs(monkeypatch: pytest.MonkeyPatch) -> _FakeRS:
    from aria_service.intel import redis_store
    rs = _FakeRS()
    monkeypatch.setattr(redis_store, "get_json", rs.get_json)
    monkeypatch.setattr(redis_store, "set_json", rs.set_json)
    return rs


# ── R-F2654 — get_report_owner falls back to the durable table ───────────────

@pytest.mark.asyncio
async def test_get_report_owner_falls_back_to_durable(tmp_path, fake_rs, monkeypatch) -> None:
    """Index evicted the row → owner must still come from dd_report_owners (not 404)."""
    from aria_service.intel import dd_orchestrator
    v = _fresh_vault(tmp_path)
    v.record_report_owner("run_evicted", canonical_entity_id="ent_X",
                          user_id="userA", user_email_domain="a.com")
    monkeypatch.setattr(dd_vault, "get_vault", lambda: v)
    fake_rs.store[dd_orchestrator.REPORT_INDEX_KEY] = []  # storm-dropped index

    owner = await dd_orchestrator.get_report_owner("run_evicted")

    assert owner is not None, "owner must be recovered from the durable table"
    assert owner["user_id"] == "userA"
    assert owner["user_email_domain"] == "a.com"


@pytest.mark.asyncio
async def test_get_report_owner_none_when_durable_also_empty(tmp_path, fake_rs, monkeypatch) -> None:
    """Fail-closed: unknown everywhere → None (an owner-less run stays owner-less)."""
    from aria_service.intel import dd_orchestrator
    v = _fresh_vault(tmp_path)
    monkeypatch.setattr(dd_vault, "get_vault", lambda: v)
    fake_rs.store[dd_orchestrator.REPORT_INDEX_KEY] = []

    assert await dd_orchestrator.get_report_owner("run_unknown") is None


# ── R-F2652 — reconcile restores PREVIOUS runs, not only the latest ──────────

@pytest.mark.asyncio
async def test_reconcile_restores_previous_run_to_index(tmp_path, fake_rs, monkeypatch) -> None:
    """A tenant-A run demoted to previous is restored from the vault under storm."""
    from aria_service.intel import dd_orchestrator
    v = _fresh_vault(tmp_path)
    _seed_two_tenant_case(v)  # latest=run_B (tenant B), prev=[run_A] (tenant A)
    monkeypatch.setattr(dd_vault, "get_vault", lambda: v)
    fake_rs.store[dd_orchestrator.REPORT_INDEX_KEY] = []  # both rows dropped by the storm

    # tenant A lists their reports — the reconcile must rebuild the index from the vault
    await dd_orchestrator.list_reports(user_id="userA", user_email_domain="a.com")

    restored = {(e.get("run_id") or "") for e in fake_rs.store[dd_orchestrator.REPORT_INDEX_KEY]
                if isinstance(e, dict)}
    assert "run_A" in restored, "the PREVIOUS run (tenant A's) must be restored, not only latest"
    assert "run_B" in restored, "the latest run must also be present"
    # and run_A carries its REAL durable owner, not a fabricated one
    row_a = next(e for e in fake_rs.store[dd_orchestrator.REPORT_INDEX_KEY]
                 if isinstance(e, dict) and e.get("run_id") == "run_A")
    assert row_a.get("user_id") == "userA"
