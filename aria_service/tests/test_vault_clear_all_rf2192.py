"""Capability test for R-F2192 — vault "Clear all".

Drives the real bulk-clear path AgentSignupVault.delete_all that backs the admin
DELETE /api/aria/vault endpoint + the vault.html "Clear all" button.

Run: python -m pytest aria_service/tests/test_vault_clear_all_rf2192.py -q
"""
import tempfile
import asyncio
from types import SimpleNamespace

from aria_service.intel.agent_signup_vault import AgentSignupVault
from aria_service.intel import portal_registry


def _vault():
    return AgentSignupVault(db_path=tempfile.mktemp(suffix=".db"))


def test_delete_all_clears_everything():
    v = _vault()
    v.record("s1", "Site1", "https://a.example.com", "admin_manual", site_type="website", status="verified")
    v.record("p1", "Portal1", "https://p.example.com", "admin_manual", site_type="portal", status="registered")
    assert len(v.list(limit=100)) == 2
    n = v.delete_all()
    assert n == 2
    assert v.list(limit=100) == []
    assert v.is_auto_seed_enabled() is False


def test_delete_all_keep_portals():
    v = _vault()
    v.record("s1", "Site1", "https://a.example.com", "admin_manual", site_type="website", status="verified")
    v.record("p1", "Portal1", "https://p.example.com", "admin_manual", site_type="portal", status="registered")
    n = v.delete_all(keep_portals=True)
    assert n == 1
    rows = v.list(limit=100)
    assert len(rows) == 1 and rows[0]["site_type"] == "portal"
    assert v.is_auto_seed_enabled() is True


def test_delete_all_empty_vault_is_zero():
    v = _vault()
    assert v.delete_all() == 0
    assert v.is_auto_seed_enabled() is False


def test_inflight_portal_sweep_stops_after_operator_clear(monkeypatch):
    """The real auto-register loop must not write after a live full clear."""
    vault = _vault()
    vault.delete_all()
    attempted: list[str] = []

    async def _register(portal_id):
        attempted.append(portal_id)
        return {"success": True}

    monkeypatch.setattr(portal_registry, "_ENABLED", True)
    monkeypatch.setattr(
        portal_registry,
        "PORTALS",
        [SimpleNamespace(id="late-write", registration_type="form")],
    )
    monkeypatch.setattr(
        "aria_service.intel.agent_signup_vault.get_vault",
        lambda: vault,
    )
    monkeypatch.setattr(portal_registry, "register_for_portal", _register)

    result = asyncio.run(portal_registry.auto_register_all())

    assert result["stopped_by_operator_clear"] is True
    assert attempted == []


if __name__ == "__main__":
    test_delete_all_clears_everything()
    test_delete_all_keep_portals()
    test_delete_all_empty_vault_is_zero()
    print("ALL PASS")
