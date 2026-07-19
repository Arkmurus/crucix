"""R-F1158 — Capability test for PortalCoverageExtractor.

Verifies that the extractor:
1. Returns gaps for unregistered portals
2. Returns empty list when all portals are registered
3. Respects the 2h lookback window (returns [] for old since)
4. Caps at 10 portals per cycle
5. Distinguishes CAPTCHA portals from auto-registrable ones
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def _vault_mock(*, pending=None, needs_operator=None):
    """A status-aware agent_signup_vault stub.

    extract() calls vault.list(status="pending") AND vault.list(status="needs_operator")
    (R-F1684). A MagicMock with a single return_value answers both identically and
    double-counts every row, so the stub must dispatch on the status kwarg.
    """
    rows = {"pending": list(pending or []), "needs_operator": list(needs_operator or [])}
    vault = MagicMock()
    vault.cleanup_test_data.return_value = None
    vault.list.side_effect = lambda status=None, limit=None: rows.get(status, [])
    return vault


def _empty_signup_vault():
    """R-F2801 — neutralise the agent_signup_vault gap source.

    `PortalCoverageExtractor.extract()` reads TWO sources: portal_coverage_audit
    (what this file tests) and, since R-F1233, the agent signup vault
    (gap_detector.py:1456+). These tests predate that second source and never
    stubbed it, so they picked up ~20 real pending-signup gaps from local vault
    state and asserted 22 == 2. That made them non-hermetic AND wrong about what
    they were measuring.

    Stubbing the vault to empty isolates the portal-coverage source under test.
    The vault source is NOT ignored — it gets its own dedicated test below, so
    coverage went up rather than down.
    """
    return patch("aria_service.intel.agent_signup_vault.get_vault",
                 return_value=_vault_mock())


class TestPortalCoverageExtractor:
    """Capability test: PortalCoverageExtractor must detect registration gaps."""

    @pytest.mark.asyncio
    async def test_returns_gaps_for_unregistered_portals(self) -> None:
        """Unregistered portals must produce portal_registration gaps."""
        from aria_service.autonomous.gap_detector import PortalCoverageExtractor

        mock_audit = {
            "total": 10,
            "registered": 3,
            "unregistered": [
                {"id": "sam_gov", "name": "SAM.gov", "url": "https://sam.gov",
                 "registration_type": "email_form", "requires_captcha": False},
                {"id": "eu_ted", "name": "EU TED", "url": "https://ted.europa.eu",
                 "registration_type": "email_form", "requires_captcha": True},
            ],
            "tier_1_gaps": 2,
            "tier_2_gaps": 0,
        }

        with _empty_signup_vault(), patch(
            "aria_service.intel.portal_coverage_audit.audit_portal_coverage",
            new_callable=AsyncMock,
            return_value=mock_audit,
        ):
            extractor = PortalCoverageExtractor(redis_client=None)
            since = datetime.now(timezone.utc) - timedelta(hours=1)
            gaps = await extractor.extract(since)

        assert len(gaps) == 2
        assert gaps[0].gap_type == "portal_registration"
        assert gaps[0].module == "portal_coverage_audit"

        # Check portal names
        gap_ids = [g.evidence["portal_id"] for g in gaps]
        assert "sam_gov" in gap_ids
        assert "eu_ted" in gap_ids

        # Check CAPTCHA distinction
        sam_gov = [g for g in gaps if g.evidence["portal_id"] == "sam_gov"][0]
        eu_ted = [g for g in gaps if g.evidence["portal_id"] == "eu_ted"][0]
        assert sam_gov.evidence["requires_captcha"] is False
        assert eu_ted.evidence["requires_captcha"] is True
        assert "CAPTCHA" in eu_ted.description
        assert "CAPTCHA" not in sam_gov.description

    @pytest.mark.asyncio
    async def test_returns_empty_when_all_registered(self) -> None:
        """Fully registered portals must produce no gaps."""
        from aria_service.autonomous.gap_detector import PortalCoverageExtractor

        mock_audit = {
            "total": 10,
            "registered": 10,
            "unregistered": [],
            "tier_1_gaps": 0,
            "tier_2_gaps": 0,
        }

        with _empty_signup_vault(), patch(
            "aria_service.intel.portal_coverage_audit.audit_portal_coverage",
            new_callable=AsyncMock,
            return_value=mock_audit,
        ):
            extractor = PortalCoverageExtractor(redis_client=None)
            since = datetime.now(timezone.utc) - timedelta(hours=1)
            gaps = await extractor.extract(since)

        assert len(gaps) == 0

    @pytest.mark.asyncio
    async def test_respects_lookback_window(self) -> None:
        """Extractor must return [] when since is outside the 2h window."""
        from aria_service.autonomous.gap_detector import PortalCoverageExtractor

        extractor = PortalCoverageExtractor(redis_client=None)
        # 3 hours ago — outside the 2h lookback window
        since = datetime.now(timezone.utc) - timedelta(hours=3)
        gaps = await extractor.extract(since)

        assert len(gaps) == 0

    @pytest.mark.asyncio
    async def test_caps_at_ten_portals(self) -> None:
        """Extractor must cap at 10 portals per cycle."""
        from aria_service.autonomous.gap_detector import PortalCoverageExtractor

        unregistered = [
            {"id": f"portal_{i}", "name": f"Portal {i}", "url": f"https://portal{i}.com",
             "registration_type": "email_form", "requires_captcha": False}
            for i in range(20)
        ]
        mock_audit = {
            "total": 25,
            "registered": 5,
            "unregistered": unregistered,
            "tier_1_gaps": 20,
            "tier_2_gaps": 0,
        }

        with _empty_signup_vault(), patch(
            "aria_service.intel.portal_coverage_audit.audit_portal_coverage",
            new_callable=AsyncMock,
            return_value=mock_audit,
        ):
            extractor = PortalCoverageExtractor(redis_client=None)
            since = datetime.now(timezone.utc) - timedelta(hours=1)
            gaps = await extractor.extract(since)

        assert len(gaps) == 10  # capped at 10

    @pytest.mark.asyncio
    async def test_handles_audit_failure_gracefully(self) -> None:
        """Extractor must return [] when the audit function fails."""
        from aria_service.autonomous.gap_detector import PortalCoverageExtractor

        with _empty_signup_vault(), patch(
            "aria_service.intel.portal_coverage_audit.audit_portal_coverage",
            new_callable=AsyncMock,
            side_effect=RuntimeError("audit failed"),
        ):
            extractor = PortalCoverageExtractor(redis_client=None)
            since = datetime.now(timezone.utc) - timedelta(hours=1)
            gaps = await extractor.extract(since)

        assert len(gaps) == 0

    # ── R-F2801: the SECOND gap source, previously untested ────────────────
    # R-F1233 added the agent-signup-vault source to extract(). Nothing covered
    # it — it was only ever observed as noise leaking into the assertions above.
    # Now it is a contract in its own right.

    @pytest.mark.asyncio
    async def test_pending_signups_become_gaps(self) -> None:
        """A stale pending signup must surface as a portal_registration gap."""
        from aria_service.autonomous.gap_detector import PortalCoverageExtractor

        # extract() queries the vault TWICE with different statuses — "pending"
        # and, since R-F1684, "needs_operator". A mock that ignores the status
        # kwarg returns the same row for both and double-counts, so it must be
        # status-aware to model the real vault.
        vault = _vault_mock(pending=[
            {"site_id": "acme_portal", "site_name": "Acme Portal",
             "site_url": "https://acme.test", "agent_id": "aria",
             "created_at": 1_600_000_000},
        ])
        empty_audit = {"total": 0, "registered": 0, "unregistered": [],
                       "tier_1_gaps": 0, "tier_2_gaps": 0}

        with patch("aria_service.intel.agent_signup_vault.get_vault", return_value=vault), \
             patch("aria_service.intel.portal_coverage_audit.audit_portal_coverage",
                   new_callable=AsyncMock, return_value=empty_audit):
            extractor = PortalCoverageExtractor(redis_client=None)
            gaps = await extractor.extract(datetime.now(timezone.utc) - timedelta(hours=1))

        assert len(gaps) == 1, "a pending signup must produce exactly one gap"
        g = gaps[0]
        assert g.gap_type == "portal_registration"
        assert g.module == "agent_signup_vault", "must be attributed to the vault, not the audit"
        assert g.evidence["site_id"] == "acme_portal"
        assert g.evidence["source"] == "vault"

    @pytest.mark.asyncio
    async def test_test_agent_signups_are_not_reported_as_gaps(self) -> None:
        """R-F1684 guard: test artifacts must never become phantom gaps."""
        from aria_service.autonomous.gap_detector import PortalCoverageExtractor

        vault = _vault_mock(pending=[
            {"site_id": "x", "site_name": "X", "site_url": "", "agent_id": "test_agent",
             "created_at": 1_600_000_000},
            {"site_id": "y", "site_name": "Y", "site_url": "", "agent_id": "test",
             "created_at": 1_600_000_000},
        ])
        empty_audit = {"total": 0, "registered": 0, "unregistered": [],
                       "tier_1_gaps": 0, "tier_2_gaps": 0}

        with patch("aria_service.intel.agent_signup_vault.get_vault", return_value=vault), \
             patch("aria_service.intel.portal_coverage_audit.audit_portal_coverage",
                   new_callable=AsyncMock, return_value=empty_audit):
            extractor = PortalCoverageExtractor(redis_client=None)
            gaps = await extractor.extract(datetime.now(timezone.utc) - timedelta(hours=1))

        assert gaps == [], "test-agent signups must be filtered out (R-F1684)"
