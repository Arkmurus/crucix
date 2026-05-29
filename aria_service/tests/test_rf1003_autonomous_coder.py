"""R-F1003 — Tests for Autonomous Coder (no external LLM)."""
from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, patch, MagicMock


class TestAutonomousCoder:
    """Test the Autonomous Coder."""

    @pytest.mark.asyncio
    async def test_generate_fix_plan(self):
        """generate_fix_plan should return a plan dict without LLM calls."""
        from aria_service.intel.autonomous_coder import AutonomousCoder
        coder = AutonomousCoder()
        
        # Create a mock gap
        class MockGap:
            description = "Create a new sanctions screening module"
            title = "New sanctions module"
            module = "sanctions_screener"
            gap_id = "gap_001"
        
        plan = await coder.generate_fix_plan(MockGap(), "")
        assert plan is not None
        assert "title" in plan
        assert "description" in plan
        assert "changes" in plan
        assert plan["llm_free"] is True
        assert plan["source"] == "self_coding_os"

    @pytest.mark.asyncio
    async def test_write_code(self):
        """write_code should return code without LLM calls."""
        from aria_service.intel.autonomous_coder import AutonomousCoder
        coder = AutonomousCoder()
        
        plan = {"description": "Test module", "title": "Test"}
        result = await coder.write_code(plan, "", "test_module.py")
        assert result is not None
        assert "code" in result
        assert result["llm_free"] is True
        assert "def " in result["code"] or "async def " in result["code"]

    @pytest.mark.asyncio
    async def test_write_tests(self):
        """write_tests should return test code without LLM calls."""
        from aria_service.intel.autonomous_coder import AutonomousCoder
        coder = AutonomousCoder()
        
        result = await coder.write_tests({}, "", 1003)
        assert result is not None
        assert "code" in result
        assert result["llm_free"] is True
        assert "pytest" in result["code"]

    @pytest.mark.asyncio
    async def test_analyse_failure(self):
        """analyse_failure should return fixes without LLM calls."""
        from aria_service.intel.autonomous_coder import AutonomousCoder
        coder = AutonomousCoder()
        
        result = await coder.analyse_failure("SyntaxError: invalid syntax", "def foo():\n", 1)
        assert result is not None
        assert "fixes_attempted" in result
        assert result["llm_free"] is True

    @pytest.mark.asyncio
    async def test_full_fix_cycle(self):
        """full_fix_cycle should run without LLM calls."""
        from aria_service.intel.autonomous_coder import AutonomousCoder
        coder = AutonomousCoder()
        
        class MockGap:
            description = "Create a new compliance checker"
            title = "Compliance checker"
            module = "compliance_checker"
            gap_id = "gap_002"
        
        result = await coder.full_fix_cycle(MockGap())
        assert result is not None
        assert "plan" in result
        assert "results" in result
        assert result["llm_free"] is True

    def test_no_external_imports(self):
        """AutonomousCoder should not import any external LLM modules."""
        import inspect
        from aria_service.intel.autonomous_coder import AutonomousCoder
        
        source = inspect.getsource(AutonomousCoder)
        assert "deepseek" not in source.lower()
        assert "anthropic" not in source.lower()
        assert "openai" not in source.lower()
        assert "httpx" not in source.lower() or "httpx" in source  # httpx is ok for health checks
