"""R-F1009 — Tests for Infrastructure Health Monitor."""
from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, patch, MagicMock


class TestInfraHealthMonitor:
    """Test the infrastructure health monitor."""

    @pytest.mark.asyncio
    async def test_check_services(self):
        """check_services should return service statuses."""
        from aria_service.intel.infra_health import InfraHealthMonitor
        monitor = InfraHealthMonitor()
        with patch("httpx.AsyncClient") as mock_client:
            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_response.elapsed.total_seconds.return_value = 0.1
            mock_response.json.return_value = {"status": "operational"}
            mock_client.return_value.__aenter__.return_value.get = AsyncMock(return_value=mock_response)
            result = await monitor._check_services()
        assert "aria-intel" in result
        assert "aria-web" in result
        assert "aria-wa" in result
        assert result["aria-intel"]["status"] == "ok"

    @pytest.mark.asyncio
    async def test_check_bd_pipeline(self):
        """check_bd_pipeline should return BD endpoint statuses."""
        from aria_service.intel.infra_health import InfraHealthMonitor
        monitor = InfraHealthMonitor()
        with patch("httpx.AsyncClient") as mock_client:
            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_client.return_value.__aenter__.return_value.get = AsyncMock(return_value=mock_response)
            result = await monitor._check_bd_pipeline()
        assert "deals" in result
        assert "contacts" in result
        assert "proactive" in result
        assert "gtm" in result

    @pytest.mark.asyncio
    async def test_check_all(self):
        """check_all should return a complete health report."""
        from aria_service.intel.infra_health import InfraHealthMonitor
        monitor = InfraHealthMonitor()
        with patch.object(monitor, "_check_services", AsyncMock(return_value={"aria-intel": {"status": "ok"}})):
            with patch.object(monitor, "_check_bd_pipeline", AsyncMock(return_value={"deals": {"status": "ok"}})):
                with patch.object(monitor, "_check_data_sync", AsyncMock(return_value={"brain_stats": {"status": "ok"}})):
                    result = await monitor.check_all()
        assert "timestamp" in result
        assert "overall" in result
        assert "checks" in result
