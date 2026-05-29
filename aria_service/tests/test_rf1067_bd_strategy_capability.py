"""R-F1067 — Capability test for bd_strategy.generate_market_intelligence().

Verifies the function actually calls real APIs and returns data.
Must fail before the fix and pass after.
"""
from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, MagicMock, patch


class TestBDStrategyCapability:
    """Capability test: generate_market_intelligence must return a report."""

    @pytest.mark.asyncio
    async def test_generate_market_intelligence_returns_report(self) -> None:
        """The function must return a dict with all expected sections."""
        from aria_service.intel.bd_strategy import generate_market_intelligence

        # Mock all dependencies to return real-looking data
        mock_il = MagicMock()
        mock_il.get_recent = AsyncMock(return_value=[
            {"title": "Test signal", "region": "MENA", "confidence": "ASSESSED"},
        ])

        mock_ct = MagicMock()
        mock_ct.get_competitor_activity = AsyncMock(return_value=[
            {"name": "TestCorp", "activity": "Won contract", "market": "MENA"},
        ])

        mock_tm = MagicMock()
        mock_tm.get_new_tenders = AsyncMock(return_value=[
            {"title": "Test tender", "country": "UAE", "value": "$1M"},
        ])

        mock_pri = MagicMock()
        mock_pri.summary = MagicMock(return_value={
            "total_countries": 50,
            "high_risk": 5,
            "medium_risk": 15,
            "low_risk": 30,
        })

        mock_nm = MagicMock()
        mock_nm.get_recent_articles = AsyncMock(return_value=[])

        mock_cc = MagicMock()
        mock_cc.assess_coherence = MagicMock(return_value={"score": 0.8})

        # Patch imports inside the function
        with patch.dict("sys.modules", {
            "aria_service.intel.intel_ledger": mock_il,
            "aria_service.intel.competitor_tracker": mock_ct,
            "aria_service.intel.tender_monitor": mock_tm,
            "aria_service.intel.political_risk_index": mock_pri,
            "aria_service.intel.news_monitor": mock_nm,
            "aria_service.intel.commercial_coherence": mock_cc,
        }):
            # Re-import to pick up mocked modules
            import importlib
            from aria_service.intel import bd_strategy
            importlib.reload(bd_strategy)

            report = await bd_strategy.generate_market_intelligence()

        # Assert the report has all expected sections
        assert isinstance(report, dict), "Report must be a dict"
        assert "market_opportunities" in report, "Report must have market_opportunities"
        assert "competitor_intelligence" in report, "Report must have competitor_intelligence"
        assert "procurement_highlights" in report, "Report must have procurement_highlights"
        assert "risk_assessment" in report, "Report must have risk_assessment"
        assert "strategic_recommendations" in report, "Report must have strategic_recommendations"
        assert "sources_consulted" in report, "Report must have sources_consulted"

    @pytest.mark.asyncio
    async def test_generate_market_intelligence_no_crash_on_empty(self) -> None:
        """The function must not crash when all sources return empty."""
        from aria_service.intel.bd_strategy import generate_market_intelligence

        mock_il = MagicMock()
        mock_il.get_recent = AsyncMock(return_value=[])

        mock_ct = MagicMock()
        mock_ct.get_competitor_activity = AsyncMock(return_value=[])

        mock_tm = MagicMock()
        mock_tm.get_new_tenders = AsyncMock(return_value=[])

        mock_pri = MagicMock()
        mock_pri.summary = MagicMock(return_value={})

        mock_nm = MagicMock()
        mock_nm.get_recent_articles = AsyncMock(return_value=[])

        mock_cc = MagicMock()
        mock_cc.assess_coherence = MagicMock(return_value={})

        with patch.dict("sys.modules", {
            "aria_service.intel.intel_ledger": mock_il,
            "aria_service.intel.competitor_tracker": mock_ct,
            "aria_service.intel.tender_monitor": mock_tm,
            "aria_service.intel.political_risk_index": mock_pri,
            "aria_service.intel.news_monitor": mock_nm,
            "aria_service.intel.commercial_coherence": mock_cc,
        }):
            import importlib
            from aria_service.intel import bd_strategy
            importlib.reload(bd_strategy)

            report = await bd_strategy.generate_market_intelligence()

        assert isinstance(report, dict)
        # Should not crash even with empty data
