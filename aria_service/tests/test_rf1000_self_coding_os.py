"""R-F1000 — Tests for ARIA Self-Coding Operating System."""
from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, patch, MagicMock


class TestSelfCodingOS:
    """Test the SelfCodingOS class."""

    def test_analyze_codebase(self):
        """analyze_codebase should return pattern counts."""
        from aria_service.intel.self_coding_os import SelfCodingOS
        os = SelfCodingOS()
        result = os.analyze_codebase()
        assert "patterns" in result
        assert "total_functions" in result
        assert result["total_functions"] > 0

    def test_categorize_function(self):
        """_categorize_function should return correct category."""
        from aria_service.intel.self_coding_os import SelfCodingOS
        os = SelfCodingOS()
        assert os._categorize_function("render_report") == "output"
        assert os._categorize_function("check_sanctions") == "validation"
        assert os._categorize_function("search_entity") == "search"
        assert os._categorize_function("unknown_func") == "other"

    def test_infer_module_name(self):
        """_infer_module_name should extract key words."""
        from aria_service.intel.self_coding_os import SelfCodingOS
        os = SelfCodingOS()
        name = os._infer_module_name("Create a new sanctions screening module")
        assert "sanctions" in name
        assert "screening" in name

    def test_infer_function_name(self):
        """_infer_function_name should detect action words."""
        from aria_service.intel.self_coding_os import SelfCodingOS
        os = SelfCodingOS()
        assert os._infer_function_name("Check sanctions status") == "check_sanctions"
        assert os._infer_function_name("Search for entities") == "search_for"
        assert os._infer_function_name("Verify compliance report") == "verify_compliance"

    def test_plan_change_new_module(self):
        """plan_change should create a CodingPlan for new modules."""
        from aria_service.intel.self_coding_os import SelfCodingOS
        os = SelfCodingOS()
        plan = os.plan_change("Create a new test module for verification")
        assert len(plan.changes) >= 2  # module + test file
        assert any(c.type == "create" for c in plan.changes)

    def test_generate_module(self):
        """_generate_module should produce valid Python."""
        import ast
        from aria_service.intel.self_coding_os import SelfCodingOS
        os = SelfCodingOS()
        code = os._generate_module("test_mod", "verify_item", "Test module", [])
        assert "def verify_item" in code or "async def verify_item" in code
        assert "wire_success" in code
        # Should be valid Python
        ast.parse(code)

    def test_generate_test(self):
        """_generate_test should produce valid Python."""
        import ast
        from aria_service.intel.self_coding_os import SelfCodingOS
        os = SelfCodingOS()
        code = os._generate_test("test_mod", "verify_item")
        assert "class TestTestMod" in code
        assert "test_verify_item_basic" in code
        ast.parse(code)

    def test_add_wiring(self):
        """_add_wiring should add wire_success call."""
        from aria_service.intel.self_coding_os import SelfCodingOS
        os = SelfCodingOS()
        source = "def my_func():\n    x = 1\n    return x\n"
        result = os._add_wiring(source, "my_module", "My function")
        assert "wire_success" in result
        assert "my_module" in result

    @pytest.mark.asyncio
    async def test_execute_plan_create(self, tmp_path):
        """execute_plan should create files."""
        from aria_service.intel.self_coding_os import SelfCodingOS, CodeChange, CodingPlan
        os = SelfCodingOS()
        os.root = tmp_path
        plan = CodingPlan(title="Test", description="Test", r_number=1000, changes=[
            CodeChange(type="create", file_path="test_file.py", content="x = 1"),
        ])
        result = await os.execute_plan(plan)
        assert result["success"] is True
        assert (tmp_path / "test_file.py").exists()

    @pytest.mark.asyncio
    async def test_execute_plan_edit(self, tmp_path):
        """execute_plan should edit files."""
        from aria_service.intel.self_coding_os import SelfCodingOS, CodeChange, CodingPlan
        os = SelfCodingOS()
        os.root = tmp_path
        (tmp_path / "test_file.py").write_text("x = 1", encoding="utf-8")
        plan = CodingPlan(title="Test", description="Test", r_number=1000, changes=[
            CodeChange(type="edit", file_path="test_file.py", old_string="x = 1", new_string="x = 2"),
        ])
        result = await os.execute_plan(plan)
        assert result["success"] is True
        assert (tmp_path / "test_file.py").read_text(encoding="utf-8") == "x = 2"
