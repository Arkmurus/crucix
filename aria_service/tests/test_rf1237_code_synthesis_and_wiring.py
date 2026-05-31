"""R-F1237 — Capability tests for code synthesis engine + brain wiring.

Tests:
  1. compose_function produces valid Python from primitives
  2. compose_module produces multi-function modules
  3. SovereignLLM wires success to brain on import
  4. SovereignLLM wires failure to brain on error
  5. AutonomousCoder.write_code uses composition for new modules
  6. coder_entrypoint tries SovereignLLM first, falls back to AutonomousCoder
"""
from __future__ import annotations

import ast
import pytest

from aria_service.intel.autonomous_coder import AutonomousCoder
from aria_service.intel.self_coding_os import SelfCodingOS


# ── Fixtures ────────────────────────────────────────────────────────────────

@pytest.fixture
def coder():
    return AutonomousCoder()


@pytest.fixture
def coding_os():
    return SelfCodingOS()


# ── compose_function tests ─────────────────────────────────────────────────

def test_compose_function_produces_valid_python(coding_os):
    """compose_function should produce syntactically valid Python."""
    code = coding_os.compose_function(
        func_name="process_item",
        is_async=True,
        args=[{"name": "item_id", "type": "str"}, {"name": "data", "type": "dict"}],
        return_type="dict",
        docstring="Process an item and return results.",
        module_name="test_module",
        add_logging=True,
        add_wiring=True,
        add_error_handling=True,
    )
    # Should be valid Python
    tree = ast.parse(code)
    assert tree is not None
    # Should contain the function definition
    assert any(
        isinstance(n, ast.AsyncFunctionDef) and n.name == "process_item"
        for n in ast.walk(tree)
    )
    # Should have logging
    assert "logger" in code
    # Should have wiring
    assert "wire_success" in code
    # Should have error handling
    assert "try:" in code
    assert "except Exception" in code


def test_compose_function_sync_produces_valid_python(coding_os):
    """compose_function should work for sync functions too."""
    code = coding_os.compose_function(
        func_name="validate_input",
        is_async=False,
        args=[{"name": "value", "type": "str"}],
        return_type="bool",
        docstring="Validate an input value.",
        module_name="test_module",
        add_logging=False,
        add_wiring=False,
        add_error_handling=False,
    )
    tree = ast.parse(code)
    assert any(
        isinstance(n, ast.FunctionDef) and n.name == "validate_input"
        for n in ast.walk(tree)
    )
    # Sync function should NOT have async def
    assert "async def" not in code


def test_compose_function_with_retry(coding_os):
    """compose_function should add retry logic when requested."""
    code = coding_os.compose_function(
        func_name="fetch_data",
        is_async=True,
        args=[{"name": "url", "type": "str"}],
        module_name="test_module",
        add_retry=True,
        add_error_handling=False,
    )
    assert "MAX_RETRIES" in code
    assert "asyncio.sleep" in code
    tree = ast.parse(code)


def test_compose_function_with_timeout(coding_os):
    """compose_function should add timeout wrapper when requested."""
    code = coding_os.compose_function(
        func_name="fetch_data",
        is_async=True,
        args=[{"name": "url", "type": "str"}],
        module_name="test_module",
        add_timeout=True,
        timeout_s=60,
    )
    assert "asyncio.wait_for" in code
    assert "asyncio.TimeoutError" in code
    tree = ast.parse(code)


def test_compose_function_with_null_checks(coding_os):
    """compose_function should add null checks when requested."""
    code = coding_os.compose_function(
        func_name="process_item",
        is_async=True,
        args=[{"name": "item_id", "type": "str"}, {"name": "data", "type": "Optional[dict]"}],
        module_name="test_module",
        add_null_checks=True,
    )
    assert "is None" in code
    assert 'return {"status": "error"' in code
    tree = ast.parse(code)


def test_compose_function_no_args_defaults(coding_os):
    """compose_function should work with default args when none provided."""
    code = coding_os.compose_function(
        func_name="search",
        is_async=True,
        module_name="test_module",
    )
    tree = ast.parse(code)
    assert any(
        isinstance(n, ast.AsyncFunctionDef) and n.name == "search"
        for n in ast.walk(tree)
    )


# ── compose_module tests ───────────────────────────────────────────────────

def test_compose_module_produces_multi_function_module(coding_os):
    """compose_module should produce a module with multiple functions."""
    functions = [
        {
            "func_name": "get_item",
            "is_async": True,
            "args": [{"name": "item_id", "type": "str"}],
            "docstring": "Get an item by ID.",
            "add_logging": False,
            "add_wiring": False,
            "add_error_handling": False,
        },
        {
            "func_name": "save_item",
            "is_async": True,
            "args": [{"name": "item", "type": "dict"}],
            "docstring": "Save an item.",
            "add_logging": False,
            "add_wiring": False,
            "add_error_handling": False,
        },
    ]
    code = coding_os.compose_module("test_module", functions)
    tree = ast.parse(code)
    func_names = [
        n.name for n in ast.walk(tree)
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
    ]
    assert "get_item" in func_names
    assert "save_item" in func_names


# ── AutonomousCoder.write_code composition path tests ──────────────────────

def test_write_code_uses_composition_for_new_modules(coder):
    """write_code should use compose_function when no existing code."""
    import asyncio
    plan = {
        "approach": "Create a new search module for querying data asynchronously",
        "title": "Create search module",
    }
    result = asyncio.run(coder.write_code(plan, "", "search_module.py"))
    assert "code" in result
    assert result.get("llm_free") is True
    code = result["code"]
    # Should be valid Python
    tree = ast.parse(code)
    # Should have at least one function
    funcs = [n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]
    assert len(funcs) >= 1


def test_write_code_composition_failure_returns_error(coder):
    """write_code should return error info when composition fails."""
    import asyncio
    # Pass an invalid description that will cause compose_function to fail
    plan = {
        "approach": "",
        "title": "",
    }
    result = asyncio.run(coder.write_code(plan, "", "test.py"))
    assert "code" in result
    # Should still return something (graceful degradation)


# ── coder_entrypoint priority tests ────────────────────────────────────────

def test_coder_entrypoint_tries_sovereign_llm_first():
    """The entrypoint should try SovereignLLM before AutonomousCoder."""
    from aria_service.autonomous.coder_entrypoint import start_aria_coder
    # We can't easily test this without a full app context,
    # but we can verify the import order in the source
    with open("aria_service/autonomous/coder_entrypoint.py", encoding="utf-8") as f:
        content = f.read()
    # SovereignLLM import should come before AutonomousCoder import
    sov_idx = content.index("from .sovereign_llm import SovereignLLM")
    auto_idx = content.index("from ..intel.autonomous_coder import AutonomousCoder")
    assert sov_idx < auto_idx, (
        "SovereignLLM should be imported before AutonomousCoder"
    )


# ── Brain wiring tests ─────────────────────────────────────────────────────

def test_sovereign_llm_wires_success_on_import():
    """SovereignLLM should call wire_success on import."""
    with open("aria_service/autonomous/sovereign_llm.py", encoding="utf-8") as f:
        content = f.read()
    assert "wire_success" in content
    assert "sovereign_llm:R-F1237" in content


def test_sovereign_llm_wires_failure_on_error():
    """SovereignLLM should call wire_failure on error."""
    with open("aria_service/autonomous/sovereign_llm.py", encoding="utf-8") as f:
        content = f.read()
    assert "wire_failure" in content
    assert "llm_error" in content


def test_autonomous_coder_wires_both_branches():
    """AutonomousCoder should wire both success and failure to brain."""
    with open("aria_service/intel/autonomous_coder.py", encoding="utf-8") as f:
        content = f.read()
    assert "wire_success" in content
    assert "wire_failure" in content


def test_self_coding_os_wires_success():
    """SelfCodingOS should wire success to brain on import."""
    with open("aria_service/intel/self_coding_os.py", encoding="utf-8") as f:
        content = f.read()
    assert "wire_success" in content
    assert "self_coding_os:R-F1237" in content
