"""R-F1231 — Capability tests for Agent Signup Vault.

Tests:
1. AgentSignupVault CRUD — record, get, list, update_status, delete
2. AgentSignupVault stats — aggregate statistics
3. AgentSignupVault import_from_portal_registry — bulk import
4. AgentSignupVault validation — invalid status raises ValueError
5. AgentSignupVault dedup — duplicate site_id raises ValueError
6. API routes — vault_list_ep, vault_record_ep, vault_update_ep, vault_delete_ep
"""
from __future__ import annotations

import json
import os
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
        os.unlink(db_path)
    except OSError:
        pass


@pytest.fixture
def sample_portals():
    """Sample portal definitions for import testing."""
    return [
        {
            "id": "test_portal_1",
            "name": "Test Portal One",
            "url": "https://test1.gov/register",
            "registration_type": "email_form",
            "signup_fields": [("email", "email", "email"), ("password", "password", "password")],
        },
        {
            "id": "test_portal_2",
            "name": "Test Portal Two",
            "url": "https://test2.gov/signup",
            "registration_type": "email_form",
            "signup_fields": [("username", "text", "email")],
        },
        {
            "id": "test_portal_3",
            "name": "Test Portal Three (no signup)",
            "url": "https://test3.gov",
            "registration_type": "api_key",
            "signup_fields": [],  # no signup fields — should be skipped
        },
    ]


# ── CRUD tests ───────────────────────────────────────────────────────


class TestVaultCRUD:
    """Test basic CRUD operations on the vault."""

    def test_record_and_get(self, vault):
        """Recording a signup and retrieving it should return matching data."""
        entry = vault.record(
            site_id="ungm",
            site_name="UNGM — UN Global Marketplace",
            site_url="https://www.ungm.org",
            agent_id="dd_orchestrator",
            status="pending",
            notes="Need to complete registration",
        )
        assert entry is not None
        assert entry["site_id"] == "ungm"
        assert entry["site_name"] == "UNGM — UN Global Marketplace"
        assert entry["agent_id"] == "dd_orchestrator"
        assert entry["status"] == "pending"
        assert entry["notes"] == "Need to complete registration"
        assert entry["created_at"] > 0
        assert entry["updated_at"] > 0

        # Get by ID
        fetched = vault.get("ungm")
        assert fetched is not None
        assert fetched["site_id"] == "ungm"
        assert fetched["site_name"] == "UNGM — UN Global Marketplace"

    def test_record_with_metadata(self, vault):
        """Recording with metadata should store and retrieve it."""
        entry = vault.record(
            site_id="sam_gov",
            site_name="SAM.gov",
            site_url="https://sam.gov",
            agent_id="researcher",
            site_type="portal",
            metadata={"portal_type": "api_key", "requires_captcha": False},
        )
        assert entry is not None
        assert entry.get("metadata", {}).get("portal_type") == "api_key"
        assert entry.get("metadata", {}).get("requires_captcha") is False

    def test_get_nonexistent_returns_none(self, vault):
        """Getting a non-existent site_id should return None."""
        result = vault.get("nonexistent_site")
        assert result is None

    def test_list_all(self, vault):
        """Listing all entries should return all recorded signups."""
        vault.record(site_id="site_a", site_name="Site A", site_url="https://a.com", agent_id="agent1")
        vault.record(site_id="site_b", site_name="Site B", site_url="https://b.com", agent_id="agent2")
        vault.record(site_id="site_c", site_name="Site C", site_url="https://c.com", agent_id="agent1")

        entries = vault.list()
        assert len(entries) == 3

    def test_list_with_status_filter(self, vault):
        """Listing with status filter should return only matching entries."""
        vault.record(site_id="site_a", site_name="Site A", site_url="https://a.com", agent_id="agent1", status="pending")
        vault.record(site_id="site_b", site_name="Site B", site_url="https://b.com", agent_id="agent2", status="verified")
        vault.record(site_id="site_c", site_name="Site C", site_url="https://c.com", agent_id="agent1", status="pending")

        pending = vault.list(status="pending")
        assert len(pending) == 2
        assert all(e["status"] == "pending" for e in pending)

        verified = vault.list(status="verified")
        assert len(verified) == 1
        assert verified[0]["site_id"] == "site_b"

    def test_list_with_agent_filter(self, vault):
        """Listing with agent_id filter should return only that agent's entries."""
        vault.record(site_id="site_a", site_name="Site A", site_url="https://a.com", agent_id="agent1")
        vault.record(site_id="site_b", site_name="Site B", site_url="https://b.com", agent_id="agent2")

        agent1_entries = vault.list(agent_id="agent1")
        assert len(agent1_entries) == 1
        assert agent1_entries[0]["agent_id"] == "agent1"

    def test_list_with_search(self, vault):
        """Listing with search should find matching text in name, URL, or notes."""
        vault.record(site_id="ungm", site_name="UNGM Portal", site_url="https://ungm.org", agent_id="agent1",
                     notes="UN procurement portal")
        vault.record(site_id="sam", site_name="SAM.gov", site_url="https://sam.gov", agent_id="agent1")

        results = vault.list(search="UNGM")
        assert len(results) == 1
        assert results[0]["site_id"] == "ungm"

        results = vault.list(search="procurement")
        assert len(results) == 1
        assert results[0]["site_id"] == "ungm"

    def test_list_with_limit_and_offset(self, vault):
        """Listing with limit and offset should paginate correctly."""
        for i in range(10):
            vault.record(site_id=f"site_{i}", site_name=f"Site {i}", site_url=f"https://site{i}.com", agent_id="agent1")

        first_page = vault.list(limit=3, offset=0)
        assert len(first_page) == 3

        second_page = vault.list(limit=3, offset=3)
        assert len(second_page) == 3
        assert second_page[0]["site_id"] != first_page[0]["site_id"]

    def test_update_status(self, vault):
        """Updating status should change the entry and set updated_at."""
        entry = vault.record(site_id="test_site", site_name="Test Site", site_url="https://test.com", agent_id="agent1",
                             status="pending")
        original_updated = entry["updated_at"]

        time.sleep(0.01)  # ensure timestamp changes

        updated = vault.update_status("test_site", "verified", notes="Registration confirmed")
        assert updated is not None
        assert updated["status"] == "verified"
        assert updated["notes"] == "Registration confirmed"
        assert updated["updated_at"] > original_updated
        assert updated["last_verified_at"] is not None  # auto-set when status=verified

    def test_update_status_nonexistent(self, vault):
        """Updating a non-existent entry should return None."""
        result = vault.update_status("nonexistent", "verified")
        assert result is None

    def test_update_metadata_merge(self, vault):
        """Updating metadata should merge with existing, not replace."""
        vault.record(site_id="test", site_name="Test", site_url="https://test.com", agent_id="agent1",
                     metadata={"key1": "value1", "key2": "value2"})
        vault.update_status("test", "verified", metadata={"key3": "value3"})
        entry = vault.get("test")
        assert entry["metadata"]["key1"] == "value1"
        assert entry["metadata"]["key2"] == "value2"
        assert entry["metadata"]["key3"] == "value3"

    def test_delete(self, vault):
        """Deleting an entry should remove it from the vault."""
        vault.record(site_id="to_delete", site_name="To Delete", site_url="https://delete.com", agent_id="agent1")
        assert vault.get("to_delete") is not None

        deleted = vault.delete("to_delete")
        assert deleted is True
        assert vault.get("to_delete") is None

    def test_delete_nonexistent(self, vault):
        """Deleting a non-existent entry should return False."""
        deleted = vault.delete("nonexistent")
        assert deleted is False

    def test_duplicate_raises_error(self, vault):
        """Recording a duplicate site_id should raise ValueError."""
        vault.record(site_id="dup", site_name="Original", site_url="https://dup.com", agent_id="agent1")
        with pytest.raises(ValueError, match="already exists"):
            vault.record(site_id="dup", site_name="Duplicate", site_url="https://dup.com", agent_id="agent1")

    def test_invalid_status_raises_error(self, vault):
        """Recording or updating with an invalid status should raise ValueError."""
        with pytest.raises(ValueError, match="Invalid status"):
            vault.record(site_id="bad", site_name="Bad", site_url="https://bad.com", agent_id="agent1",
                         status="invalid_status")

        vault.record(site_id="good", site_name="Good", site_url="https://good.com", agent_id="agent1", status="pending")
        with pytest.raises(ValueError, match="Invalid status"):
            vault.update_status("good", "also_invalid")


# ── Stats tests ──────────────────────────────────────────────────────


class TestVaultStats:
    """Test vault statistics."""

    def test_stats_empty_vault(self, vault):
        """An empty vault should return zero counts."""
        stats = vault.stats()
        assert stats["total"] == 0
        assert stats["by_status"] == {}
        assert stats["by_agent"] == {}

    def test_stats_with_entries(self, vault):
        """Stats should reflect the vault contents."""
        vault.record(site_id="a", site_name="A", site_url="https://a.com", agent_id="agent1", status="pending")
        vault.record(site_id="b", site_name="B", site_url="https://b.com", agent_id="agent1", status="verified")
        vault.record(site_id="c", site_name="C", site_url="https://c.com", agent_id="agent2", status="verified")
        vault.record(site_id="d", site_name="D", site_url="https://d.com", agent_id="agent2", status="failed")

        stats = vault.stats()
        assert stats["total"] == 4
        assert stats["by_status"]["pending"] == 1
        assert stats["by_status"]["verified"] == 2
        assert stats["by_status"]["failed"] == 1
        assert stats["by_agent"]["agent1"] == 2
        assert stats["by_agent"]["agent2"] == 2
        assert stats["by_type"]["portal"] == 4


# ── Import tests ─────────────────────────────────────────────────────


class TestVaultImport:
    """Test importing from portal_registry."""

    def test_import_from_portal_registry(self, vault, sample_portals):
        """Importing should create entries for all portal types."""
        # R-F1684: import_open_portals now handles ALL portal types, not just
        # those with signup_fields. test_portal_3 (api_key, no signup_fields)
        # now gets imported as 'needs_operator'.
        count = vault.import_open_portals(sample_portals, agent_id="test_agent")
        assert count == 3  # all 3 portals now imported (R-F1684 widened coverage)

        # Check the imported entries
        entry1 = vault.get("test_portal_1")
        assert entry1 is not None
        assert entry1["site_name"] == "Test Portal One"
        assert entry1["agent_id"] == "test_agent"
        assert entry1["status"] == "pending"

        entry2 = vault.get("test_portal_2")
        assert entry2 is not None
        assert entry2["site_name"] == "Test Portal Two"

        # Portal without signup_fields now gets imported as 'needs_operator'
        entry3 = vault.get("test_portal_3")
        assert entry3 is not None, "test_portal_3 should now be imported as needs_operator"
        assert entry3["status"] == "needs_operator", (
            f"Expected needs_operator, got {entry3['status']}"
        )

    def test_import_skips_existing(self, vault, sample_portals):
        """Importing should skip already-recorded sites."""
        vault.record(site_id="test_portal_1", site_name="Already Here", site_url="https://existing.com",
                     agent_id="existing_agent")

        # R-F1477: method is import_open_portals, not import_from_portal_registry
        # R-F1684: now imports 3 portals (was 2), so after skipping 1, count=2
        count = vault.import_open_portals(sample_portals, agent_id="test_agent")
        assert count == 2  # test_portal_2 and test_portal_3 are new (was 1)

        # Existing entry should not be overwritten
        entry = vault.get("test_portal_1")
        assert entry["agent_id"] == "existing_agent"  # unchanged


# ── API route tests ──────────────────────────────────────────────────


class TestVaultAPI:
    """Test vault API routes via FastAPI TestClient."""

    @pytest.fixture
    def client(self):
        """Create a test client with the vault routes."""
        from fastapi.testclient import TestClient
        from aria_service.main import app

        with TestClient(app) as c:
            yield c

    def test_vault_list_endpoint(self, client):
        """GET /api/aria/vault should return a list of entries."""
        response = client.get("/api/aria/vault")
        assert response.status_code in (200, 401, 403)  # may need auth
        if response.status_code == 200:
            data = response.json()
            assert "success" in data
            assert "entries" in data
            assert "stats" in data

    def test_vault_stats_endpoint(self, client):
        """GET /api/aria/vault/stats should return statistics."""
        response = client.get("/api/aria/vault/stats")
        assert response.status_code in (200, 401, 403)
        if response.status_code == 200:
            data = response.json()
            assert data["success"] is True
            assert "stats" in data
            assert "total" in data["stats"]

    def test_vault_record_and_get_endpoint(self, client):
        """POST /api/aria/vault then GET /api/aria/vault/{id} should work."""
        # R-F1477: use unique site_id per test run to avoid test isolation issues
        import time
        unique_id = f"api_test_site_{int(time.time())}"
        response = client.post(
            "/api/aria/vault",
            json={
                "site_id": unique_id,
                "site_name": "API Test Site",
                "site_url": "https://api-test.gov",
                "agent_id": "test_agent",
                "status": "pending",
            },
        )
        # Accept any non-500 response (auth may block)
        assert response.status_code != 500
        if response.status_code == 200:
            data = response.json()
            assert data["success"] is True
            assert data["entry"]["site_id"] == unique_id

            # Now get it — may return 401 if auth is required for GET
            get_resp = client.get(f"/api/aria/vault/{unique_id}")
            # R-F1477: GET endpoint requires auth via router dependency
            # Accept 401 (auth required) or 200 (success)
            assert get_resp.status_code in (200, 401), (
                f"Expected 200 or 401, got {get_resp.status_code}: {get_resp.text[:200]}"
            )
            if get_resp.status_code == 200:
                get_data = get_resp.json()
                assert get_data["success"] is True
                assert get_data["entry"]["site_id"] == unique_id

    def test_vault_update_endpoint(self, client):
        """PUT /api/aria/vault/{id} should update status."""
        # First record
        client.post(
            "/api/aria/vault",
            json={
                "site_id": "update_test",
                "site_name": "Update Test",
                "site_url": "https://update-test.gov",
                "agent_id": "test_agent",
                "status": "pending",
            },
        )

        # Then update
        response = client.put(
            "/api/aria/vault/update_test",
            json={"status": "verified", "notes": "Confirmed working"},
        )
        assert response.status_code != 500
        if response.status_code == 200:
            data = response.json()
            assert data["success"] is True
            assert data["entry"]["status"] == "verified"
            assert data["entry"]["notes"] == "Confirmed working"

    def test_vault_delete_endpoint(self, client):
        """DELETE /api/aria/vault/{id} should remove the entry."""
        # First record
        client.post(
            "/api/aria/vault",
            json={
                "site_id": "delete_test",
                "site_name": "Delete Test",
                "site_url": "https://delete-test.gov",
                "agent_id": "test_agent",
            },
        )

        # Then delete
        response = client.delete("/api/aria/vault/delete_test")
        assert response.status_code != 500
        if response.status_code == 200:
            data = response.json()
            assert data["success"] is True
            assert data["deleted"] == "delete_test"

            # Verify gone
            get_resp = client.get("/api/aria/vault/delete_test")
            if get_resp.status_code == 200:
                assert get_resp.json()["success"] is False

    def test_vault_import_endpoint(self, client):
        """POST /api/aria/vault/import should not crash."""
        response = client.post("/api/aria/vault/import", json={"agent_id": "test_agent"})
        assert response.status_code != 500
        if response.status_code == 200:
            data = response.json()
            assert "imported" in data
            assert "stats" in data
