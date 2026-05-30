"""R-F1126 — Capability tests for the portal coverage auto-audit.

Tests that portal_coverage_audit correctly:
1. Audits all portals against the credential vault
2. Reports registered vs missing portals
3. Identifies tier 1-2 gaps
4. Auto-registers for missing high-value portals
5. Wires results to the brain
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from aria_service.intel.portal_registry import PORTALS, PortalDef


# ── Fixtures ────────────────────────────────────────────────────────────────

@pytest.fixture
def mock_registered_portals():
    """Mock get_registered_portals to return a subset with registered=True."""
    # Patch at the portal_registry module level (where the function lives)
    with patch(
        "aria_service.intel.portal_registry.get_registered_portals",
        new_callable=AsyncMock,
    ) as m:
        # Return ALL portals with correct registered flags
        # Use actual portal IDs from PORTALS so the audit can match them
        m.return_value = [
            {"id": p.id, "name": p.name, "registered": True} for p in PORTALS[:5]
        ] + [
            {"id": p.id, "name": p.name, "registered": False} for p in PORTALS[5:]
        ]
        yield m


# ── Tests ───────────────────────────────────────────────────────────────────

class TestPortalCoverageAudit:
    """Proves the portal coverage audit works correctly."""

    async def test_audit_counts_total_portals(self, mock_registered_portals):
        """Audit reports the correct total number of portals."""
        from aria_service.intel.portal_coverage_audit import audit_portal_coverage

        result = await audit_portal_coverage()

        assert result["total"] == len(PORTALS)
        assert result["total"] > 0

    async def test_audit_reports_registered_count(self, mock_registered_portals):
        """Audit reports the correct number of registered portals."""
        from aria_service.intel.portal_coverage_audit import audit_portal_coverage

        result = await audit_portal_coverage()

        assert result["registered"] == 5  # First 5 are mocked as registered
        assert result["missing"] == result["total"] - 5

    async def test_audit_identifies_tier_1_gaps(self, mock_registered_portals):
        """Audit identifies missing tier 1 portals as gaps."""
        from aria_service.intel.portal_coverage_audit import audit_portal_coverage

        result = await audit_portal_coverage()

        # All gaps should have tier info
        for gap in result["gaps"]:
            assert "tier" in gap
            assert gap["tier"] <= 2

    async def test_audit_wires_to_brain(self, mock_registered_portals):
        """Audit wires results to the brain via wire_success."""
        from aria_service.intel.portal_coverage_audit import audit_portal_coverage

        with patch("aria_service.intel.engine_wiring.wire_success") as mock_ws:
            result = await audit_portal_coverage()

        mock_ws.assert_called_once()
        args, kwargs = mock_ws.call_args
        assert kwargs.get("module") == "portal_coverage_audit"
        assert str(result["registered"]) in kwargs.get("summary", "")

    async def test_audit_returns_tier_breakdown(self, mock_registered_portals):
        """Audit returns breakdown by intelligence value tier."""
        from aria_service.intel.portal_coverage_audit import audit_portal_coverage

        result = await audit_portal_coverage()

        assert "by_tier" in result
        for tier in [1, 2, 3, 4]:
            assert tier in result["by_tier"]
            assert "total" in result["by_tier"][tier]
            assert "registered" in result["by_tier"][tier]
            assert "missing" in result["by_tier"][tier]

    async def test_audit_returns_details(self, mock_registered_portals):
        """Audit returns per-portal details."""
        from aria_service.intel.portal_coverage_audit import audit_portal_coverage

        result = await audit_portal_coverage()

        assert "details" in result
        assert len(result["details"]) == result["total"]
        for detail in result["details"]:
            assert "id" in detail
            assert "name" in detail
            assert "status" in detail
            assert detail["status"] in ("registered", "missing")


class TestAutoRegisterGaps:
    """Proves auto-registration for missing portals works."""

    async def test_auto_register_skips_captcha_portals(self, mock_registered_portals):
        """Portals requiring CAPTCHA are skipped."""
        from aria_service.intel.portal_coverage_audit import auto_register_gaps

        with patch(
            "aria_service.intel.portal_coverage_audit.audit_portal_coverage",
            new_callable=AsyncMock,
        ) as mock_audit:
            mock_audit.return_value = {
                "gaps": [
                    {"id": "captcha_portal", "name": "CAPTCHA Portal",
                     "tier": 1, "url": "https://example.com",
                     "registration_type": "email_form",
                     "requires_captcha": True},
                    {"id": "no_captcha_portal", "name": "No CAPTCHA Portal",
                     "tier": 1, "url": "https://example.com",
                     "registration_type": "email_form",
                     "requires_captcha": False},
                ],
                "registered": 0,
                "missing": 2,
                "total": 2,
                "by_tier": {1: {"total": 2, "registered": 0, "missing": 2}},
            }

            with patch(
                "aria_service.intel.portal_registry.register_for_portal",
                new_callable=AsyncMock,
                return_value={"success": True, "message": "Registered"},
            ):
                results = await auto_register_gaps(max_portals=5)

        # Only the non-CAPTCHA portal should have been attempted
        captcha_results = [r for r in results if r["portal_id"] == "captcha_portal"]
        no_captcha_results = [r for r in results if r["portal_id"] == "no_captcha_portal"]

        assert len(captcha_results) == 0  # Skipped
        assert len(no_captcha_results) == 1
        assert no_captcha_results[0]["success"] is True

    async def test_auto_register_respects_max_portals(self, mock_registered_portals):
        """Auto-register respects the max_portals limit."""
        from aria_service.intel.portal_coverage_audit import auto_register_gaps

        with patch(
            "aria_service.intel.portal_coverage_audit.audit_portal_coverage",
            new_callable=AsyncMock,
        ) as mock_audit:
            mock_audit.return_value = {
                "gaps": [
                    {"id": f"portal_{i}", "name": f"Portal {i}",
                     "tier": 1, "url": "https://example.com",
                     "registration_type": "email_form",
                     "requires_captcha": False}
                    for i in range(10)
                ],
                "registered": 0,
                "missing": 10,
                "total": 10,
                "by_tier": {1: {"total": 10, "registered": 0, "missing": 10}},
            }

            with patch(
                "aria_service.intel.portal_registry.register_for_portal",
                new_callable=AsyncMock,
                return_value={"success": True, "message": "Registered"},
            ):
                results = await auto_register_gaps(max_portals=3)

        assert len(results) == 3

    async def test_auto_register_wires_to_brain(self, mock_registered_portals):
        """Auto-registration wires results to the brain."""
        from aria_service.intel.portal_coverage_audit import auto_register_gaps

        with patch(
            "aria_service.intel.portal_coverage_audit.audit_portal_coverage",
            new_callable=AsyncMock,
        ) as mock_audit:
            mock_audit.return_value = {
                "gaps": [],
                "registered": 0,
                "missing": 0,
                "total": 0,
                "by_tier": {},
            }

            with patch(
                "aria_service.intel.engine_wiring.wire_success",
            ) as mock_ws:
                await auto_register_gaps(max_portals=1)

        mock_ws.assert_called_once()
        args, kwargs = mock_ws.call_args
        assert kwargs.get("module") == "portal_coverage_audit"
