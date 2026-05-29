"""R-F1006 — Tests for System Health, Performance Optimizer."""
from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, patch, MagicMock


class TestSystemHealthMonitor:
    """Test the system health monitor."""

    def test_record_metric(self):
        """record_metric should store metrics."""
        from aria_service.intel.system_health import SystemHealthMonitor
        monitor = SystemHealthMonitor()
        monitor.record_metric("test.metric", 1.0, threshold=1)
        assert "test.metric" in monitor._metrics
        assert monitor._metrics["test.metric"][0].value == 1.0

    def test_record_log(self):
        """record_log should store log entries."""
        from aria_service.intel.system_health import SystemHealthMonitor
        monitor = SystemHealthMonitor()
        monitor.record_log("ERROR", "test_module", "Test error")
        assert len(monitor._logs) == 1
        assert monitor._logs[0].level == "ERROR"

    def test_get_status(self):
        """get_status should return system status."""
        from aria_service.intel.system_health import SystemHealthMonitor
        monitor = SystemHealthMonitor()
        status = monitor.get_status()
        assert "services" in status
        assert "errors_5min" in status
        assert "active_anomalies" in status
        assert "status" in status

    def test_get_recent_logs(self):
        """get_recent_logs should return log entries."""
        from aria_service.intel.system_health import SystemHealthMonitor
        monitor = SystemHealthMonitor()
        monitor.record_log("INFO", "mod1", "msg1")
        monitor.record_log("ERROR", "mod2", "msg2")
        logs = monitor.get_recent_logs()
        assert len(logs) == 2
        error_logs = monitor.get_recent_logs(level="ERROR")
        assert len(error_logs) == 1

    @pytest.mark.asyncio
    async def test_monitor_loop(self):
        """The monitor loop should check services."""
        from aria_service.intel.system_health import SystemHealthMonitor
        monitor = SystemHealthMonitor()
        with patch("httpx.AsyncClient") as mock_client:
            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_response.elapsed.total_seconds.return_value = 0.1
            mock_client.return_value.__aenter__.return_value.get = AsyncMock(return_value=mock_response)
            await monitor._check_services()
        assert "service.aria-intel.status" in monitor._metrics


class TestPerformanceOptimizer:
    """Test the performance optimizer."""

    def test_record_timing(self):
        """record_timing should store timings."""
        from aria_service.intel.performance_optimizer import PerformanceOptimizer
        opt = PerformanceOptimizer()
        opt.record_timing("test_op", 100.0)
        assert "test_op" in opt._timings
        assert opt._timings["test_op"][0] == 100.0

    def test_get_timing_stats(self):
        """get_timing_stats should return statistics."""
        from aria_service.intel.performance_optimizer import PerformanceOptimizer
        opt = PerformanceOptimizer()
        opt.record_timing("test_op", 100.0)
        opt.record_timing("test_op", 200.0)
        stats = opt.get_timing_stats("test_op")
        assert stats["samples"] == 2
        assert stats["avg_ms"] == 150.0

    def test_identify_slow_operations(self):
        """identify_slow_operations should find slow ops."""
        from aria_service.intel.performance_optimizer import PerformanceOptimizer
        opt = PerformanceOptimizer()
        opt.record_timing("fast_op", 10.0)
        opt.record_timing("slow_op", 2000.0)
        slow = opt.identify_slow_operations(threshold_ms=1000)
        assert len(slow) == 1
        assert slow[0]["operation"] == "slow_op"

    def test_get_all_stats(self):
        """get_all_stats should return all stats."""
        from aria_service.intel.performance_optimizer import PerformanceOptimizer
        opt = PerformanceOptimizer()
        opt.record_timing("op1", 100.0)
        opt.record_timing("op2", 200.0)
        stats = opt.get_all_stats()
        assert len(stats) == 2

    @pytest.mark.asyncio
    async def test_optimize_imports(self):
        """optimize_imports should find import issues."""
        from aria_service.intel.performance_optimizer import PerformanceOptimizer
        opt = PerformanceOptimizer()
        result = await opt.optimize_imports()
        assert "findings" in result
        assert "total" in result

    @pytest.mark.asyncio
    async def test_optimize_async(self):
        """optimize_async should find sync functions doing I/O."""
        from aria_service.intel.performance_optimizer import PerformanceOptimizer
        opt = PerformanceOptimizer()
        result = await opt.optimize_async()
        assert "findings" in result
        assert "total" in result
