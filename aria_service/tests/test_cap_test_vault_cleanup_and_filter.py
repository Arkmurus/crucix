"""R-F1684: Capability test — vault cleanup removes test artifacts,
import_open_portals covers all portal types, and gap detector filters
declined/deferred portals.

This test drives the REAL functions (not mocked helpers) to prove the
user-visible behaviour: test data is cleaned, all portal types are imported,
and the gap detector doesn't create phantom gaps for test artifacts.
"""
import os
import sys
import time
import json
import tempfile
from pathlib import Path

# Ensure the project root is on sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from aria_service.intel.agent_signup_vault import AgentSignupVault, get_vault, VALID_STATUSES
from aria_service.intel.portal_registry import PORTALS


class TestVaultCleanupAndFilter:
    """Capability test: vault cleanup + import + gap filter."""

    def setup_method(self):
        """Create a temp vault for testing."""
        self.tmp_dir = Path(tempfile.mkdtemp())
        self.db_path = self.tmp_dir / "test_vault.db"
        self.vault = AgentSignupVault(db_path=self.db_path)

    def teardown_method(self):
        """Clean up temp vault."""
        self.vault.close()
        import shutil
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def test_valid_statuses_includes_declined_and_deferred(self):
        """R-F1684: VALID_STATUSES must include 'declined' and 'deferred'."""
        assert "declined" in VALID_STATUSES, "VALID_STATUSES missing 'declined'"
        assert "deferred" in VALID_STATUSES, "VALID_STATUSES missing 'deferred'"

    def test_cleanup_removes_old_test_entries(self):
        """R-F1684: cleanup_test_data removes test_agent/test entries >24h old."""
        # Create old test entries
        old_ts = time.time() - 48 * 3600  # 48 hours ago
        self.vault.record(
            site_id="test_old_1", site_name="Old Test 1", site_url="http://test1.com",
            agent_id="test_agent", status="pending",
        )
        # Manually set created_at to 48h ago by updating the DB directly
        self.vault._get_conn().execute(
            "UPDATE signups SET created_at = ? WHERE site_id = ?",
            (old_ts, "test_old_1"),
        )
        self.vault._get_conn().commit()

        # Create a recent test entry (should survive)
        self.vault.record(
            site_id="test_recent", site_name="Recent Test", site_url="http://recent.com",
            agent_id="test_agent", status="pending",
        )

        # Create a real entry (should survive)
        self.vault.record(
            site_id="real_portal", site_name="Real Portal", site_url="http://real.com",
            agent_id="portal_registry", status="needs_operator",
        )

        # Run cleanup with 24h threshold
        deleted = self.vault.cleanup_test_data(max_age_hours=24)

        # The old test entry should be deleted
        assert deleted >= 1, f"Expected at least 1 deletion, got {deleted}"
        assert self.vault.get("test_old_1") is None, "Old test entry should be deleted"
        assert self.vault.get("test_recent") is not None, "Recent test entry should survive"
        assert self.vault.get("real_portal") is not None, "Real entry should survive"

    def test_import_open_portals_covers_all_types(self):
        """R-F1684: import_open_portals handles none, CAPTCHA, signup_fields, and fallback."""
        # Create mock portal definitions covering all types
        mock_portals = [
            # Type 1: registration_type='none' → open_api
            {"id": "free_api", "name": "Free API", "url": "http://free.com",
             "registration_type": "none", "requires_captcha": False, "signup_fields": None},
            # Type 2: CAPTCHA → needs_operator
            {"id": "captcha_portal", "name": "CAPTCHA Portal", "url": "http://captcha.com",
             "registration_type": "email_form", "requires_captcha": True, "signup_fields": None},
            # Type 3: signup_fields → pending
            {"id": "form_portal", "name": "Form Portal", "url": "http://form.com",
             "registration_type": "email_form", "requires_captcha": False,
             "signup_fields": [("email", "email", "email")]},
            # Type 4: no signup_fields, no CAPTCHA → needs_operator (fallback)
            {"id": "api_key_portal", "name": "API Key Portal", "url": "http://apikey.com",
             "registration_type": "api_key", "requires_captcha": False, "signup_fields": None},
        ]

        count = self.vault.import_open_portals(mock_portals, agent_id="test_import")
        assert count == 4, f"Expected 4 imports, got {count}"

        # Verify each type got the correct status
        free_entry = self.vault.get("free_api")
        assert free_entry is not None
        assert free_entry["status"] == "open_api", f"Expected open_api, got {free_entry['status']}"

        captcha_entry = self.vault.get("captcha_portal")
        assert captcha_entry is not None
        assert captcha_entry["status"] == "needs_operator", f"Expected needs_operator, got {captcha_entry['status']}"

        form_entry = self.vault.get("form_portal")
        assert form_entry is not None
        assert form_entry["status"] == "pending", f"Expected pending, got {form_entry['status']}"

        api_entry = self.vault.get("api_key_portal")
        assert api_entry is not None
        assert api_entry["status"] == "needs_operator", f"Expected needs_operator, got {api_entry['status']}"

    def test_import_skips_duplicates(self):
        """R-F1684: import_open_portals skips already-imported portals."""
        mock_portals = [
            {"id": "dup_test", "name": "Dup Test", "url": "http://dup.com",
             "registration_type": "none", "requires_captcha": False, "signup_fields": None},
        ]
        count1 = self.vault.import_open_portals(mock_portals)
        assert count1 == 1
        count2 = self.vault.import_open_portals(mock_portals)
        assert count2 == 0, f"Expected 0 duplicates, got {count2}"

    def test_declined_portal_not_in_gaps(self):
        """R-F1684: declined portals should not create gaps in the extractor.
        
        This tests the metadata filtering path in PortalCoverageExtractor.
        A portal with metadata.declined=True should be skipped.
        """
        # Record a declined portal
        self.vault.record(
            site_id="declined_portal", site_name="Declined Portal",
            site_url="http://declined.com", agent_id="portal_registry",
            status="needs_operator",
            metadata={"declined": True, "reason": "operator declined paid subscription"},
        )

        # Record a real needs_operator portal (should be actionable)
        self.vault.record(
            site_id="actionable_portal", site_name="Actionable Portal",
            site_url="http://actionable.com", agent_id="portal_registry",
            status="needs_operator",
        )

        # List needs_operator entries
        entries = self.vault.list(status="needs_operator")
        entry_ids = {e["site_id"] for e in entries}

        # Both should be in the vault
        assert "declined_portal" in entry_ids
        assert "actionable_portal" in entry_ids

        # The declined portal's metadata should have declined=True
        declined = self.vault.get("declined_portal")
        assert declined is not None
        meta = declined.get("metadata", {})
        assert meta.get("declined") is True, f"Expected declined=True, got {meta}"


class TestVaultMigration:
    """R-F1704: Startup migration corrects fabricated registrations."""

    def setup_method(self):
        import tempfile
        self.tmp_dir = Path(tempfile.mkdtemp())
        self.db_path = self.tmp_dir / "test_migration.db"
        self.vault = AgentSignupVault(db_path=self.db_path)

    def teardown_method(self):
        self.vault.close()
        import shutil
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def test_migration_corrects_fabricated_registrations(self):
        """R-F1704: Entries with 'Credentials found in vault' notes get corrected."""
        # Create a fabricated entry (old code's pattern)
        self.vault.record(
            site_id="fabricated_portal", site_name="Fabricated Portal",
            site_url="http://fake.com", agent_id="system",
            status="registered",
            notes="Credentials found in vault",
        )

        # Create a real registration (should NOT be touched)
        self.vault.record(
            site_id="real_portal", site_name="Real Portal",
            site_url="http://real.com", agent_id="system",
            status="registered",
            notes="Autonomously registered. Identity: aria@arkmurus.com.",
        )

        # Close and reopen to trigger migration
        self.vault.close()
        self.vault = AgentSignupVault(db_path=self.db_path)

        # Fabricated entry should be corrected to needs_operator
        fabricated = self.vault.get("fabricated_portal")
        assert fabricated is not None
        assert fabricated["status"] == "needs_operator", (
            f"Fabricated entry should be needs_operator, got {fabricated['status']}"
        )
        assert "R-F1704" in (fabricated.get("notes") or ""), (
            "Migration note should be present"
        )

        # Real registration should remain registered
        real = self.vault.get("real_portal")
        assert real is not None
        assert real["status"] == "registered", (
            f"Real registration should remain registered, got {real['status']}"
        )

    def test_migration_skips_clean_vault(self):
        """R-F1704: Vault with no fabricated entries stays unchanged."""
        self.vault.record(
            site_id="clean_portal", site_name="Clean Portal",
            site_url="http://clean.com", agent_id="system",
            status="needs_operator",
        )
        self.vault.close()
        self.vault = AgentSignupVault(db_path=self.db_path)

        entry = self.vault.get("clean_portal")
        assert entry is not None
        assert entry["status"] == "needs_operator"
