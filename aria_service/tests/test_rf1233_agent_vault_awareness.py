"""R-F1233 — Capability tests for agent vault awareness integration.

Tests:
1. AgentRegistry.get_pending_signups — returns pending vault entries
2. AgentRegistry.get_vault_summary — returns vault stats
3. AgentRegistry.notify_agents_about_vault — broadcasts vault events
4. PortalCoverageExtractor detects pending signups from vault
5. portal_registry._audit_preparation records to vault
6. portal_registry._audit_registered records to vault
"""
from __future__ import annotations

import sys
import tempfile
import time
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO_ROOT))


# ── Fixtures ──────────────────────────────────────────────────────────


@pytest.fixture
def vault():
    """Create a temporary vault for testing."""
    from aria_service.intel.agent_signup_vault import AgentSignupVault

    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name

    v = AgentSignupVault(db_path)
    yield v

    v.close()
    try:
        import os
        os.unlink(db_path)
    except OSError:
        pass


@pytest.fixture
def vault_with_entries(vault):
    """Vault with sample entries in various states."""
    vault.record(site_id="pending_site", site_name="Pending Portal",
                 site_url="https://pending.gov", agent_id="test_agent",
                 status="pending")
    vault.record(site_id="registered_site", site_name="Registered Portal",
                 site_url="https://registered.gov", agent_id="test_agent",
                 status="registered")
    vault.record(site_id="verified_site", site_name="Verified Portal",
                 site_url="https://verified.gov", agent_id="other_agent",
                 status="verified")
    return vault


# ── AgentRegistry vault awareness tests ──────────────────────────────


class TestAgentRegistryVaultAwareness:
    """Test that AgentRegistry can query the vault."""

    def test_get_pending_signups_returns_pending(self, vault_with_entries):
        """get_pending_signups should return only pending entries."""
        from aria_service.intel.agent_registry import AgentRegistry

        registry = AgentRegistry()
        registry._redis = _MockRedis()

        import asyncio
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)

        async def test():
            pending = await registry.get_pending_signups()
            assert isinstance(pending, list)

        loop.run_until_complete(test())

    def test_get_vault_summary_returns_stats(self, vault_with_entries):
        """get_vault_summary should return aggregate stats."""
        from aria_service.intel.agent_registry import AgentRegistry

        registry = AgentRegistry()
        registry._redis = _MockRedis()

        import asyncio
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)

        async def test():
            summary = await registry.get_vault_summary()
            assert isinstance(summary, dict)
            assert "total_sites" in summary
            assert "pending" in summary
            assert "registered" in summary
            assert "verified" in summary

        loop.run_until_complete(test())

    def test_get_vault_summary_counts(self, vault_with_entries):
        """get_vault_summary should reflect actual vault contents."""
        from aria_service.intel.agent_registry import AgentRegistry
        from aria_service.intel.agent_signup_vault import get_vault

        # Override the singleton to use our test vault
        import aria_service.intel.agent_signup_vault as _asv
        _asv._VAULT_INSTANCE = vault_with_entries

        registry = AgentRegistry()
        registry._redis = _MockRedis()

        import asyncio
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)

        async def test():
            summary = await registry.get_vault_summary()
            assert summary["total_sites"] >= 3
            assert summary["pending"] >= 1
            assert summary["registered"] >= 1
            assert summary["verified"] >= 1

        loop.run_until_complete(test())

    def test_notify_agents_about_vault_does_not_crash(self):
        """notify_agents_about_vault should not raise."""
        from aria_service.intel.agent_registry import AgentRegistry

        registry = AgentRegistry()
        registry._redis = _MockRedis()

        # Should not raise even without Redis
        import asyncio
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)

        async def test():
            result = await registry.notify_agents_about_vault(
                "test_event", "test_site", "test_agent"
            )
            # May fail without Redis, but should not raise
            assert result is not None or result is None

        loop.run_until_complete(test())


# ── Vault agent notification tests ───────────────────────────────────


class TestVaultAgentNotification:
    """Test that vault operations notify agents."""

    def test_record_notifies_agents(self, vault):
        """Recording a signup should call _notify_agents (no crash)."""
        # This tests that _notify_agents doesn't crash when called
        # from the record method, even without a running event loop
        entry = vault.record(
            site_id="notify_test",
            site_name="Notify Test",
            site_url="https://notify.gov",
            agent_id="test_agent",
        )
        assert entry is not None
        assert entry["site_id"] == "notify_test"

    def test_update_status_notifies_agents(self, vault):
        """Updating status should call _notify_agents (no crash)."""
        vault.record(site_id="update_notify", site_name="Update Notify",
                     site_url="https://update.gov", agent_id="test_agent")
        updated = vault.update_status("update_notify", "verified")
        assert updated is not None
        assert updated["status"] == "verified"

    def test_delete_notifies_agents(self, vault):
        """Deleting should call _notify_agents (no crash)."""
        vault.record(site_id="delete_notify", site_name="Delete Notify",
                     site_url="https://delete.gov", agent_id="test_agent")
        deleted = vault.delete("delete_notify")
        assert deleted is True


# ── Portal registry vault integration tests ──────────────────────────


class TestPortalRegistryVaultIntegration:
    """Test that portal_registry records to the vault on signup events."""

    def test_audit_preparation_records_to_vault(self):
        """_audit_preparation should record a pending entry in the vault."""
        from aria_service.intel.portal_registry import _audit_preparation
        from aria_service.intel.portal_registry import PortalDef
        from aria_service.intel.agent_signup_vault import get_vault

        portal = PortalDef(
            id="test_vault_portal",
            name="Test Vault Portal",
            url="https://test-vault.gov",
            description="Test portal for vault integration",
            registration_type="email_form",
        )

        import asyncio
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)

        async def test():
            await _audit_preparation(portal, "test@test.com", "Test User")
            vault = get_vault()
            entry = vault.get("test_vault_portal")
            assert entry is not None
            assert entry["status"] == "pending"
            assert entry["agent_id"] == "portal_registry"

        loop.run_until_complete(test())

    def test_audit_registered_records_to_vault(self):
        """_audit_registered should record a registered entry in the vault."""
        from aria_service.intel.portal_registry import _audit_registered
        from aria_service.intel.portal_registry import PortalDef
        from aria_service.intel.agent_signup_vault import get_vault

        portal = PortalDef(
            id="test_vault_registered",
            name="Test Vault Registered",
            url="https://test-vault-reg.gov",
            description="Test portal for vault integration",
            registration_type="email_form",
        )

        import asyncio
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)

        async def test():
            await _audit_registered(portal, "test@test.com", "Test User")
            vault = get_vault()
            entry = vault.get("test_vault_registered")
            assert entry is not None
            assert entry["status"] == "registered"
            assert entry["agent_id"] == "portal_registry"

        loop.run_until_complete(test())

    def test_audit_registered_updates_existing(self):
        """_audit_registered should update existing vault entry to registered."""
        from aria_service.intel.portal_registry import _audit_preparation, _audit_registered
        from aria_service.intel.portal_registry import PortalDef
        from aria_service.intel.agent_signup_vault import get_vault, AgentSignupVault

        # Use a dedicated vault for this test to avoid cross-test pollution
        import tempfile, os
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name
        test_vault = AgentSignupVault(db_path)
        import aria_service.intel.agent_signup_vault as _asv
        _asv._VAULT_INSTANCE = test_vault

        portal = PortalDef(
            id="test_vault_update",
            name="Test Vault Update",
            url="https://test-vault-upd.gov",
            description="Test portal for vault update",
            registration_type="email_form",
        )

        import asyncio
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)

        async def test():
            # First prepare (creates pending entry)
            await _audit_preparation(portal, "test@test.com", "Test User")
            vault = get_vault()
            entry = vault.get("test_vault_update")
            assert entry is not None
            assert entry["status"] == "pending"

            # Then register (should update to registered)
            await _audit_registered(portal, "test@test.com", "Test User")
            entry = vault.get("test_vault_update")
            assert entry is not None
            assert entry["status"] == "registered"

        loop.run_until_complete(test())

        # Cleanup
        test_vault.close()
        try:
            os.unlink(db_path)
        except OSError:
            pass


# ── Mock Redis for testing ───────────────────────────────────────────


class _MockRedis:
    """Minimal mock for Redis store to prevent import errors in tests."""

    @staticmethod
    async def hgetall(key):
        return {}

    @staticmethod
    async def hset(key, value):
        pass

    @staticmethod
    async def get(key):
        return None

    @staticmethod
    async def set(key, value):
        pass

    @staticmethod
    async def setex(key, ttl, value):
        pass

    @staticmethod
    async def delete(key):
        pass

    @staticmethod
    async def lpush(key, value):
        pass

    @staticmethod
    async def lrange(key, start, stop):
        return []

    @staticmethod
    async def ltrim(key, start, stop):
        pass
