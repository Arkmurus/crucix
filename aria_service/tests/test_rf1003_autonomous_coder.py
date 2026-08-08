"""R-F1003 — Tests for Autonomous Coder (no external LLM)."""
from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, patch, MagicMock

# R-F3788/§16 — NOT inspect.getsource: it slices at line numbers captured
# AT IMPORT, so a mid-run edit silently returns a DIFFERENT function's body.
from ._source_probe import class_source


class TestAutonomousCoder:
    """Test the Autonomous Coder."""

    @pytest.mark.asyncio
    async def test_generate_fix_plan(self):
        """generate_fix_plan should return a plan dict matching self_coder contract."""
        from aria_service.intel.autonomous_coder import AutonomousCoder
        coder = AutonomousCoder()
        
        # Create a mock gap
        class MockGap:
            description = "Create a new sanctions screening module"
            title = "New sanctions module"
            module = "sanctions_screener"
            gap_id = "gap_001"
            gap_type = "missing_capability"
        
        plan = await coder.generate_fix_plan(MockGap(), "")
        assert plan is not None
        assert "title" in plan
        assert "approach" in plan  # self_coder reads "approach", not "description"
        assert "target_files" in plan  # self_coder reads "target_files"
        assert "risk_level" in plan  # self_coder reads "risk_level"
        assert plan["llm_free"] is True
        # R-F3294 — R-F1232 replaced the plan generator with the AST
        # dataflow engine and renamed the marker accordingly. The marker names
        # WHICH LLM-free engine produced the plan; `llm_free` above is the
        # load-bearing claim and is unchanged.
        assert plan["source"] == "code_understanding"

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
        """write_tests should return test code matching self_coder contract."""
        from aria_service.intel.autonomous_coder import AutonomousCoder
        coder = AutonomousCoder()
        
        result = await coder.write_tests({}, "", 1003)
        assert result is not None
        assert "test_code" in result  # self_coder reads "test_code", not "code"
        assert "test_filepath" in result  # self_coder reads "test_filepath"
        assert result["llm_free"] is True
        assert "pytest" in result["test_code"]

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
        """End-to-end: plan -> code -> test without LLM calls."""
        from aria_service.intel.autonomous_coder import AutonomousCoder
        coder = AutonomousCoder()
        
        class MockGap:
            description = "Create a new compliance checker"
            title = "Compliance checker"
            module = "compliance_checker"
            gap_id = "gap_002"
            gap_type = "missing_capability"
        
        # Plan
        plan = await coder.generate_fix_plan(MockGap(), "")
        assert plan is not None
        assert "target_files" in plan
        assert plan["llm_free"] is True
        
        # Code (for each target file)
        for target in plan.get("target_files", []):
            code_result = await coder.write_code(plan, "", target)
            assert code_result is not None
            assert "code" in code_result
            assert code_result["llm_free"] is True
        
        # Tests
        test_result = await coder.write_tests(plan, "", 1112)
        assert test_result is not None
        assert "test_code" in test_result
        assert test_result["llm_free"] is True

    def test_no_external_imports(self):
        """AutonomousCoder should not import any external LLM modules."""
        import inspect
        from aria_service.intel.autonomous_coder import AutonomousCoder
        
        source = class_source("aria_service.intel.autonomous_coder", "AutonomousCoder")
        assert "deepseek" not in source.lower()
        assert "anthropic" not in source.lower()
        assert "openai" not in source.lower()
        assert "httpx" not in source.lower() or "httpx" in source  # httpx is ok for health checks
