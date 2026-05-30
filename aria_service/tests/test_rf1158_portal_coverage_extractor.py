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
from unittest.mock import AsyncMock, patch

import pytest


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

        with patch(
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

        with patch(
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

        with patch(
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

        with patch(
            "aria_service.intel.portal_coverage_audit.audit_portal_coverage",
            new_callable=AsyncMock,
            side_effect=RuntimeError("audit failed"),
        ):
            extractor = PortalCoverageExtractor(redis_client=None)
            since = datetime.now(timezone.utc) - timedelta(hours=1)
            gaps = await extractor.extract(since)

        assert len(gaps) == 0
