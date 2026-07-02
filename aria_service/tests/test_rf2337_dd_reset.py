"""R-F2337 — reset_dd_memory() wipes ALL DD state for a clean start (test data purge)
without touching unrelated keys, and DDVault.clear_all() empties the vault tables."""
import fnmatch

import pytest

from aria_service.intel import dd_orchestrator as ddo
from aria_service.intel import dd_vault


def test_vault_clear_all(tmp_path):
    v = dd_vault.DDVault(db_path=str(tmp_path / "v.db"))
    v.record_case("company:US:X", "X Corp")
    v.set_financial_profile("company:US:X", {"health_verdict": "STABLE"}, entity_name="X Corp")
    v.add_cross_reference("company:US:X", "company:US:Y", "shares_director")
    counts = v.clear_all()
    assert counts["dd_cases"] >= 1
    assert counts["financial_profiles"] >= 1
    assert v.get_case("company:US:X") is None
    assert v.get_financial_profile("company:US:X") is None


@pytest.mark.asyncio
async def test_reset_requires_confirm():
    r = await ddo.reset_dd_memory()          # no confirm
    assert r["ok"] is False
    r2 = await ddo.reset_dd_memory(confirm=False)
    assert r2["ok"] is False


@pytest.mark.asyncio
async def test_reset_wipes_all_dd_keys_only(monkeypatch, tmp_path):
    store = {
        "crucix:dd:report:dd_a": {"x": 1},
        "crucix:dd:report:dd_b": {"y": 2},
        "crucix:dd:report_index": [{"run_id": "dd_a"}],
        "crucix:dd:vls:dd_a": {"hash": "h"},
        "crucix:dd:vls:chain:company:US:X": ["dd_a"],
        "crucix:dd:watchlist": [{"name": "X"}],
        "crucix:aria:dd:watchlist:alerts": ["a1"],
        "unrelated:key": {"keep": True},          # must survive
    }
    import aria_service.intel.redis_store as rs

    async def fake_scan_keys(pattern, count=200):
        return [k for k in list(store) if fnmatch.fnmatch(k, pattern)]

    async def fake_delete(key):
        return store.pop(key, None) is not None

    monkeypatch.setattr(rs, "scan_keys", fake_scan_keys)
    monkeypatch.setattr(rs, "delete", fake_delete)

    v = dd_vault.DDVault(db_path=str(tmp_path / "v.db"))
    v.record_case("company:US:X", "X Corp")
    monkeypatch.setattr(dd_vault, "get_vault", lambda: v)

    r = await ddo.reset_dd_memory(confirm=True)
    assert r["ok"] is True
    for k in ("crucix:dd:report:dd_a", "crucix:dd:report:dd_b", "crucix:dd:report_index",
              "crucix:dd:vls:dd_a", "crucix:dd:vls:chain:company:US:X",
              "crucix:dd:watchlist", "crucix:aria:dd:watchlist:alerts"):
        assert k not in store, f"{k} was not wiped"
    assert store.get("unrelated:key") == {"keep": True}   # non-DD key preserved
    assert v.get_case("company:US:X") is None              # vault cleared
    assert r["cleared"]["reports"] == 2
