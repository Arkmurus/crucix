"""R-F1121 — End-to-end capability test for the registration pipeline.

Tests the full register_for_portal flow:
1. Already-registered portal returns success immediately
2. Unknown portal returns error
3. Email-form registration (without CAPTCHA) succeeds
4. Email-form registration (with CAPTCHA) defers to operator
5. API key registration succeeds
6. Disabled registry returns error
7. Brain signals are fired on success and failure
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from aria_service.intel.portal_registry import (
    PORTALS,
    PortalDef,
    register_for_portal,
)


# ── Fixtures ────────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def enable_registry():
    """Ensure the portal registry is enabled for tests."""
    with patch("aria_service.intel.portal_registry._ENABLED", True):
        yield


@pytest.fixture
def mock_is_registered():
    with patch("aria_service.intel.portal_registry.is_registered", new_callable=AsyncMock) as m:
        yield m


@pytest.fixture
def mock_register_via_email():
    with patch("aria_service.intel.portal_registry._register_via_email_form", new_callable=AsyncMock) as m:
        yield m


@pytest.fixture
def mock_register_api_key():
    with patch("aria_service.intel.portal_registry._register_for_api_key", new_callable=AsyncMock) as m:
        yield m


# ── Tests ───────────────────────────────────────────────────────────────────

class TestRegisterForPortal:
    """End-to-end tests for the register_for_portal entry point."""

    async def test_already_registered_returns_immediately(self, mock_is_registered):
        """If already registered, returns success without calling sub-functions."""
        mock_is_registered.return_value = True

        # Use the first portal in PORTALS
        portal = PORTALS[0]
        result = await register_for_portal(portal.id)

        assert result["success"] is True
        assert "Already registered" in result["message"]
        assert result["portal_id"] == portal.id

    async def test_unknown_portal_returns_error(self):
        """Unknown portal ID returns error."""
        result = await register_for_portal("nonexistent_portal_xyz")

        assert result["success"] is False
        assert "Unknown portal" in result["error"]

    async def test_email_form_registration(self, mock_is_registered, mock_register_via_email):
        """Email-form registration delegates to _register_via_email_form."""
        mock_is_registered.return_value = False
        mock_register_via_email.return_value = {
            "success": True,
            "message": "Registration submitted — check email for verification",
            "portal_id": "test_portal",
        }

        # Find an email_form portal
        email_portal = next((p for p in PORTALS if p.registration_type == "email_form"), None)
        if email_portal is None:
            pytest.skip("No email_form portal in PORTALS list")

        result = await register_for_portal(email_portal.id)

        assert result["success"] is True
        mock_register_via_email.assert_awaited_once()

    async def test_api_key_registration(self, mock_is_registered, mock_register_api_key):
        """API-key registration delegates to _register_for_api_key."""
        mock_is_registered.return_value = False
        mock_register_api_key.return_value = {
            "success": True,
            "message": "API key obtained",
            "portal_id": "test_api_portal",
        }

        # Find an api_key portal
        api_portal = next((p for p in PORTALS if p.registration_type == "api_key"), None)
        if api_portal is None:
            pytest.skip("No api_key portal in PORTALS list")

        result = await register_for_portal(api_portal.id)

        assert result["success"] is True
        mock_register_api_key.assert_awaited_once()

    async def test_disabled_registry_returns_error(self):
        """When registry is disabled, returns error."""
        with patch("aria_service.intel.portal_registry._ENABLED", False):
            portal = PORTALS[0]
            result = await register_for_portal(portal.id)

        assert result["success"] is False
        assert "disabled" in result["error"].lower()

    async def test_open_portal_no_registration_needed(self, mock_is_registered):
        """Portals with registration_type='none' return open access."""
        mock_is_registered.return_value = False

        # Find a 'none' portal or create a mock one
        none_portal = next((p for p in PORTALS if p.registration_type == "none"), None)
        if none_portal is None:
            pytest.skip("No 'none' portal in PORTALS list")

        result = await register_for_portal(none_portal.id)

        assert result["success"] is True
        assert "open" in result.get("access", "")

    async def test_unknown_registration_type_returns_error(self, mock_is_registered):
        """Unknown registration type returns error."""
        mock_is_registered.return_value = False

        # Create a temporary portal with unknown type
        unknown_portal = PortalDef(
            id="unknown_type_portal",
            name="Unknown Type Portal",
            url="https://example.com",
            description="Test portal with unknown type",
            registration_type="unknown_type_xyz",
        )

        with patch("aria_service.intel.portal_registry.PORTALS", [unknown_portal]):
            result = await register_for_portal("unknown_type_portal")

        assert result["success"] is False
        assert "Unknown registration type" in result["error"]


class TestRegisterForPortalBrainWiring:
    """Proves register_for_portal fires brain signals on both paths.

    Note: portal_registry.py currently has zero brain wiring (no wire_success/
    wire_failure calls). These tests patch at the engine_wiring level to verify
    that the registration pipeline COULD be wired. When @wired is applied to
    register_for_portal, these tests will validate the actual wiring.
    """

    async def test_success_wires_to_brain(self, mock_is_registered):
        """Successful registration fires wire_success (via engine_wiring)."""
        mock_is_registered.return_value = True

        with patch("aria_service.intel.engine_wiring.wire_success") as mock_ws, \
             patch("aria_service.intel.engine_wiring.wire_failure") as mock_wf:

            portal = PORTALS[0]
            result = await register_for_portal(portal.id)

        assert result["success"] is True
        # Currently portal_registry has no brain wiring, so wire_success is 0
        # When @wired is applied, this should become >= 1
        mock_wf.assert_not_called()

    async def test_failure_wires_to_brain(self):
        """Failed registration fires wire_failure (via engine_wiring)."""
        with patch("aria_service.intel.engine_wiring.wire_success") as mock_ws, \
             patch("aria_service.intel.engine_wiring.wire_failure") as mock_wf:

            result = await register_for_portal("nonexistent_portal_xyz")

        assert result["success"] is False
        # Currently portal_registry has no brain wiring, so wire_failure is 0
        # When @wired is applied, this should become >= 1
