"""R-F1255: Capability test for dd_full_sweep tool handler.

Verifies that the dd_full_sweep tool handler exists in tasks.py and
correctly reads the watchlist and attempts DD on each entity.
"""
import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from aria_service.autonomous.tasks import _execute_direct_tool
from aria_service.autonomous.tasks import Task


@pytest.fixture
def mock_task():
    return Task(
        id="WEEKLY-DD-FULL-SWEEP",
        name="Full DD sweep",
        cron="0 8 * * mon",
        enabled=False,
        priority="MEDIUM",
        timeout_seconds=900,
        cost_cap_usd=5.00,
        tool_chain=[{"tool": "dd_full_sweep", "max_entities": 3}],
        delivery_channels=["mem0", "intel_ledger"],
        mem0_tags=["dd", "orchestrator", "full-sweep", "weekly", "autonomous"],
    )


class TestDDFullSweep:
    """Capability tests for dd_full_sweep tool handler."""

    @pytest.mark.asyncio
    async def test_dd_full_sweep_empty_watchlist(self, mock_task):
        """Empty watchlist should return cleanly with 0 entities."""
        with patch("aria_service.intel.redis_store.get_json", new_callable=AsyncMock) as mock_get:
            mock_get.return_value = []
            result = await _execute_direct_tool("dd_full_sweep", mock_task, llm=None)
            assert result["entities_screened"] == 0
            assert result["message"] == "watchlist is empty"

    @pytest.mark.asyncio
    async def test_dd_full_sweep_with_entities(self, mock_task):
        """Watchlist with entities should attempt DD on each."""
        watchlist = [
            {"name": "Acme Corp", "type": "company"},
            {"name": "John Doe", "type": "person"},
        ]
        with patch("aria_service.intel.redis_store.get_json", new_callable=AsyncMock) as mock_get:
            mock_get.return_value = watchlist
            with patch("aria_service.intel.dd_orchestrator.orchestrate_dd", new_callable=AsyncMock) as mock_dd:
                mock_dd.return_value = MagicMock(
                    composite_score=0.75,
                    report_id="test-report-1",
                )
                result = await _execute_direct_tool("dd_full_sweep", mock_task, llm=None)
                assert result["entities_screened"] == 2
                assert result["success_count"] == 2
                assert result["error_count"] == 0
                assert mock_dd.call_count == 2

    @pytest.mark.asyncio
    async def test_dd_full_sweep_handles_errors(self, mock_task):
        """DD failures should be captured as errors, not crash."""
        watchlist = [
            {"name": "Bad Entity", "type": "company"},
        ]
        with patch("aria_service.intel.redis_store.get_json", new_callable=AsyncMock) as mock_get:
            mock_get.return_value = watchlist
            with patch("aria_service.intel.dd_orchestrator.orchestrate_dd", new_callable=AsyncMock) as mock_dd:
                mock_dd.side_effect = ValueError("Invalid entity")
                result = await _execute_direct_tool("dd_full_sweep", mock_task, llm=None)
                assert result["entities_screened"] == 1
                assert result["success_count"] == 0
                assert result["error_count"] == 1
                assert "Invalid entity" in result["errors"][0]["error"]
