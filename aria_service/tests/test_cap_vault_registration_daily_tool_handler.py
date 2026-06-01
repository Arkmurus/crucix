"""R-F1256: Capability test for vault_registration_daily tool handler.

Verifies that the vault_registration_daily tool handler exists in tasks.py
and correctly picks up pending vault entries and attempts registration.
"""
import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from aria_service.autonomous.tasks import _execute_direct_tool
from aria_service.autonomous.tasks import Task


@pytest.fixture
def mock_task():
    return Task(
        id="DAILY-VAULT-REGISTRATION",
        name="Daily vault registration",
        cron="0 5 * * *",
        enabled=True,
        priority="MEDIUM",
        timeout_seconds=600,
        cost_cap_usd=0.00,
        tool_chain=[{"tool": "vault_registration_daily"}],
        delivery_channels=["mem0", "brain_hook"],
        mem0_tags=["portals", "registration", "vault", "daily", "autonomous"],
    )


class TestVaultRegistrationDaily:
    """Capability tests for vault_registration_daily tool handler."""

    @pytest.mark.asyncio
    async def test_no_pending_entries(self, mock_task):
        """No pending entries should return cleanly."""
        mock_vault = MagicMock()
        mock_vault.list.return_value = []
        mock_vault.close = MagicMock()

        with patch("aria_service.intel.agent_signup_vault.AgentSignupVault", return_value=mock_vault):
            result = await _execute_direct_tool("vault_registration_daily", mock_task, llm=None)
            assert result["checked"] == 0
            assert result["message"] == "no pending entries"

    @pytest.mark.asyncio
    async def test_pending_entries_attempted(self, mock_task):
        """Pending entries should trigger registration attempts."""
        mock_vault = MagicMock()
        mock_vault.list.return_value = [
            {"site_id": "sam_gov", "site_name": "SAM.gov"},
            {"site_id": "ted_europa", "site_name": "TED Europa"},
        ]
        mock_vault.close = MagicMock()

        with patch("aria_service.intel.agent_signup_vault.AgentSignupVault", return_value=mock_vault):
            with patch("aria_service.intel.portal_registry.register_for_portal", new_callable=AsyncMock) as mock_reg:
                mock_reg.return_value = {"success": True, "message": "Registered"}
                result = await _execute_direct_tool("vault_registration_daily", mock_task, llm=None)
                assert result["checked"] == 2
                assert result["registered"] == 2
                assert mock_reg.call_count == 2

    @pytest.mark.asyncio
    async def test_registration_failures_handled(self, mock_task):
        """Registration failures should be captured, not crash."""
        mock_vault = MagicMock()
        mock_vault.list.return_value = [
            {"site_id": "bad_portal", "site_name": "Bad Portal"},
        ]
        mock_vault.close = MagicMock()

        with patch("aria_service.intel.agent_signup_vault.AgentSignupVault", return_value=mock_vault):
            with patch("aria_service.intel.portal_registry.register_for_portal", new_callable=AsyncMock) as mock_reg:
                mock_reg.side_effect = RuntimeError("Connection failed")
                result = await _execute_direct_tool("vault_registration_daily", mock_task, llm=None)
                assert result["checked"] == 1
                assert result["registered"] == 0
                assert "Connection failed" in result["results"][0]["error"]
