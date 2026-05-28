"""R-F998 — Tests for ecosystem dashboard, adversarial suite, and security scanner."""
from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, patch, MagicMock


class TestEcosystemDashboard:
    """Test the ecosystem dashboard module."""

    def test_scan_wiring_coverage(self):
        """scan_wiring_coverage should return a dict with total/wired/dark."""
        from aria_service.intel.ecosystem_dashboard import scan_wiring_coverage
        result = scan_wiring_coverage()
        assert "total" in result
        assert "wired" in result
        assert "dark" in result
        assert "pct" in result
        assert result["total"] > 0  # There are intel modules
        assert result["wired"] >= 0
        assert result["pct"] >= 0.0

    def test_set_and_clear_task(self):
        """set_current_task and clear_current_task should work."""
        from aria_service.intel.ecosystem_dashboard import (
            set_current_task, clear_current_task, get_current_task,
        )
        assert get_current_task() is None
        set_current_task("test task")
        assert get_current_task() == "test task"
        clear_current_task("completed")
        assert get_current_task() is None

    @pytest.mark.asyncio
    async def test_get_autonomous_loop_status(self):
        """get_autonomous_loop_status should not raise."""
        from aria_service.intel.ecosystem_dashboard import get_autonomous_loop_status
        result = await get_autonomous_loop_status()
        assert isinstance(result, dict)

    @pytest.mark.asyncio
    async def test_run_security_scan(self):
        """run_security_scan should return findings."""
        from aria_service.intel.ecosystem_dashboard import run_security_scan
        result = await run_security_scan()
        assert "total_findings" in result
        assert "critical" in result
        assert "high" in result
        assert "medium" in result
        assert "findings" in result

    @pytest.mark.asyncio
    async def test_run_adversarial_suite_no_llm(self):
        """run_adversarial_suite should handle missing LLM gracefully."""
        from aria_service.intel.ecosystem_dashboard import run_adversarial_suite
        result = await run_adversarial_suite(llm_provider=None)
        assert "total" in result
        assert "passed" in result
        assert "failed" in result
        assert "pass_rate" in result
        assert result["total"] > 0

    @pytest.mark.asyncio
    async def test_get_ecosystem_status(self):
        """get_ecosystem_status should return a full snapshot."""
        from aria_service.intel.ecosystem_dashboard import get_ecosystem_status
        result = await get_ecosystem_status()
        assert "timestamp" in result
        assert "current_task" in result
        assert "wiring" in result
        assert "autonomous_loop" in result
        assert "services" in result


class TestAdversarialSuite:
    """Test the adversarial test definitions."""

    def test_adversarial_tests_defined(self):
        """ADVERSARIAL_TESTS should have entries."""
        from aria_service.intel.ecosystem_dashboard import ADVERSARIAL_TESTS
        assert len(ADVERSARIAL_TESTS) > 0
        for test in ADVERSARIAL_TESTS:
            assert "id" in test
            assert "name" in test
            assert "prompt" in test
            assert "expected" in test
            assert "severity" in test

    def test_adversarial_tests_cover_key_areas(self):
        """Tests should cover injection, fabrication, exfiltration, spoofing."""
        from aria_service.intel.ecosystem_dashboard import ADVERSARIAL_TESTS
        names = [t["name"] for t in ADVERSARIAL_TESTS]
        categories = ["injection", "bypass", "fabrication", "exfiltration", "spoofing"]
        found = sum(1 for c in categories if any(c in n.lower() for n in names))
        assert found >= 3, f"Only {found}/5 categories covered: {names}"
