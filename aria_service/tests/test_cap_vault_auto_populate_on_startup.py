"""R-F1253 — Capability test: agent signup vault auto-populates on startup.

Proves that:
1. The vault auto-population code exists in main.py lifespan
2. Calling import_from_portal_registry populates the vault with data
3. The vault stats show entries after import
"""

import pytest
import os
import tempfile
import shutil


def _read(path: str) -> str:
    with open(path, encoding="utf-8") as f:
        return f.read()


class TestVaultAutoPopulateInLifespan:
    """Verify vault auto-population is wired into main.py lifespan."""

    def test_vault_auto_populate_code_in_main(self):
        """main.py must contain the R-F1253 vault auto-population block."""
        source = _read("aria_service/main.py")
        assert "R-F1253" in source, (
            "R-F1253 vault auto-population block must exist in main.py"
        )
        # R-F1482: method was renamed from import_from_portal_registry to import_open_portals
        assert "import_open_portals" in source, (
            "main.py must call import_open_portals for vault auto-population"
        )
        assert "agent_signup_vault" in source, (
            "main.py must import agent_signup_vault for auto-population"
        )

    def test_vault_auto_populate_checks_empty_first(self):
        """The auto-population must check if vault is empty before importing."""
        source = _read("aria_service/main.py")
        assert 'stats.get("total", 0) == 0' in source, (
            "Auto-population must check vault is empty before importing"
        )

    def test_vault_auto_populate_is_non_fatal(self):
        """Vault auto-population failure must not crash lifespan."""
        source = _read("aria_service/main.py")
        assert "non-fatal" in source, (
            "Vault auto-population must be wrapped in try/except with non-fatal logging"
        )


class TestVaultImportFromPortalRegistry:
    """Prove the vault import actually populates data."""

    def test_import_populates_vault(self):
        """Calling import_open_portals must populate the vault."""
        from aria_service.intel.agent_signup_vault import AgentSignupVault, get_vault
        from aria_service.intel.portal_registry import PORTALS

        # Use a fresh vault with a temp directory
        tmpdir = tempfile.mkdtemp()
        try:
            vault = AgentSignupVault(db_path=os.path.join(tmpdir, "vault.db"))
            stats_before = vault.stats()
            assert stats_before["total"] == 0, "Fresh vault must be empty"

            # R-F1482: method is import_open_portals, not import_from_portal_registry
            count = vault.import_open_portals(PORTALS, agent_id="test_agent")
            assert count > 0, f"Must import portals, got {count}"

            stats_after = vault.stats()
            assert stats_after["total"] == count, (
                f"Stats total ({stats_after['total']}) must match imported count ({count})"
            )
            # Some are 'registered' (open APIs), some are 'pending' (registrable)
            assert stats_after["by_status"].get("pending", 0) > 0 or stats_after["by_status"].get("registered", 0) > 0, (
                f"Imported entries should be pending or registered, got {stats_after['by_status']}"
            )

            # Verify entries are queryable
            entries = vault.list(limit=5)
            assert len(entries) > 0, "Must have entries after import"
            assert entries[0]["site_id"] is not None, "Entry must have site_id"
            assert entries[0]["site_name"] is not None, "Entry must have site_name"
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)

    def test_import_skips_existing(self):
        """Importing again must not create duplicates."""
        from aria_service.intel.agent_signup_vault import AgentSignupVault
        from aria_service.intel.portal_registry import PORTALS

        tmpdir = tempfile.mkdtemp()
        try:
            vault = AgentSignupVault(db_path=os.path.join(tmpdir, "vault.db"))
            # R-F1482: method is import_open_portals, not import_from_portal_registry
            count1 = vault.import_open_portals(PORTALS, agent_id="test_agent")
            count2 = vault.import_open_portals(PORTALS, agent_id="test_agent")
            assert count2 == 0, (
                f"Second import must return 0 (already imported), got {count2}"
            )
            stats = vault.stats()
            assert stats["total"] == count1, (
                f"Total ({stats['total']}) must equal first import count ({count1})"
            )
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)

    def test_vault_entries_have_required_fields(self):
        """Every vault entry must have site_id, site_name, site_url, status, agent_id."""
        from aria_service.intel.agent_signup_vault import AgentSignupVault
        from aria_service.intel.portal_registry import PORTALS

        tmpdir = tempfile.mkdtemp()
        try:
            vault = AgentSignupVault(db_path=os.path.join(tmpdir, "vault.db"))
            # R-F1482: method is import_open_portals, not import_from_portal_registry
            vault.import_open_portals(PORTALS, agent_id="test_agent")
            entries = vault.list(limit=100)
            assert len(entries) > 0, "Must have entries"

            required = {"site_id", "site_name", "site_url", "status", "agent_id"}
            for entry in entries:
                missing = required - set(entry.keys())
                assert not missing, (
                    f"Entry {entry.get('site_id', '?')} missing fields: {missing}"
                )
                assert entry["status"] in ("pending", "registered", "verified", "failed", "expired", "cancelled"), (
                    f"Entry {entry['site_id']} has invalid status: {entry['status']}"
                )
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)
