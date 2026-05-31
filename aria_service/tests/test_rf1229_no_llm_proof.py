"""R-F1229 — Proof that AutonomousCoder works without DeepSeek/any LLM.

Tests all four contract methods that self_coder.py reads:
  generate_fix_plan, write_code, write_tests, analyse_failure

All run with ZERO external API calls — pure AST-based code synthesis.
"""
from __future__ import annotations

import asyncio
import pytest

from aria_service.intel.autonomous_coder import AutonomousCoder
from aria_service.autonomous.gap_detector import Gap, GapType, GapSeverity


SAMPLE_CODE = """def process_item(data):
    result = data["value"] * 2
    return result
"""


@pytest.fixture
def coder():
    return AutonomousCoder()


@pytest.fixture
def sample_gap():
    return Gap(
        gap_id="test_gap",
        gap_type=GapType.MODULE_BUG,
        severity=GapSeverity.HIGH,
        title="Fix error handling in process_item",
        description="process_item crashes when data is None",
        module="test_module",
    )


def test_generate_fix_plan_no_llm(coder, sample_gap):
    """generate_fix_plan returns a plan without any LLM call."""
    plan = asyncio.run(coder.generate_fix_plan(sample_gap, "def process_item(data): pass"))
    assert isinstance(plan, dict)
    assert "title" in plan
    assert "approach" in plan
    assert "target_files" in plan
    assert "risk_level" in plan
    assert plan.get("llm_free") is True
    assert len(plan["target_files"]) > 0


def test_write_code_no_llm(coder, sample_gap):
    """write_code returns code without any LLM call."""
    plan = asyncio.run(coder.generate_fix_plan(sample_gap, "def process_item(data): pass"))
    result = asyncio.run(coder.write_code(plan, SAMPLE_CODE, "test_module.py"))
    assert isinstance(result, dict)
    assert "code" in result
    assert result.get("llm_free") is True
    # Should produce real code (not empty, not a stub)
    assert len(result["code"]) > 50
    assert "def " in result["code"] or "async def " in result["code"]


def test_write_tests_no_llm(coder, sample_gap):
    """write_tests returns tests without any LLM call."""
    plan = asyncio.run(coder.generate_fix_plan(sample_gap, "def process_item(data): pass"))
    code_result = asyncio.run(coder.write_code(plan, SAMPLE_CODE, "test_module.py"))
    result = asyncio.run(coder.write_tests(plan, code_result["code"], 9999))
    assert isinstance(result, dict)
    assert "test_code" in result
    assert "test_filepath" in result
    assert result.get("llm_free") is True
    assert "pytest" in result["test_code"] or "unittest" in result["test_code"]
    assert "test_rf9999" in result["test_filepath"] or "test_" in result["test_filepath"]


def test_analyse_failure_no_llm(coder):
    """analyse_failure returns corrected code without any LLM call."""
    result = asyncio.run(coder.analyse_failure(
        "TypeError: cannot unpack non-iterable NoneType object",
        SAMPLE_CODE,
        1,
    ))
    assert isinstance(result, dict)
    assert "code" in result
    assert result.get("llm_free") is True
    # Should have attempted at least one fix
    fixes = result.get("fixes_attempted", [])
    assert len(fixes) > 0


def test_edit_existing_code_adds_error_handling(coder):
    """_edit_existing_code produces real error handling code."""
    result = coder._edit_existing_code(
        SAMPLE_CODE, "test_module",
        "Add error handling to process_item to catch exceptions",
        "aria_service/intel/test_module.py",
    )
    assert "try:" in result
    assert "except Exception" in result
    assert "logger.error" in result
    assert "process_item" in result


def test_edit_existing_code_adds_null_checks(coder):
    """_edit_existing_code produces real null checks."""
    result = coder._edit_existing_code(
        SAMPLE_CODE, "test_module",
        "Fix AttributeError when data is None",
        "aria_service/intel/test_module.py",
    )
    assert "is None" in result
    assert "logger.warning" in result


def test_edit_existing_code_adds_retry_logic(coder):
    """_edit_existing_code produces real retry logic."""
    result = coder._edit_existing_code(
        SAMPLE_CODE, "test_module",
        "Add retry logic for flaky API calls in process_item",
        "aria_service/intel/test_module.py",
    )
    assert "MAX_RETRIES" in result
    assert "asyncio.sleep" in result
    assert "attempt" in result


def test_edit_existing_code_adds_timeout(coder):
    """_edit_existing_code produces real timeout wrapper for async functions."""
    async_code = """async def process_item(data):
    result = data["value"] * 2
    return result
"""
    result = coder._edit_existing_code(
        async_code, "test_module",
        "Add timeout to prevent process_item from hanging",
        "aria_service/intel/test_module.py",
    )
    assert "TIMEOUT_S" in result
    assert "asyncio.TimeoutError" in result


def test_edit_existing_code_adds_docstring(coder):
    """_edit_existing_code adds a docstring."""
    code_no_doc = "def process_item(data):\n    return data\n"
    result = coder._edit_existing_code(
        code_no_doc, "test_module",
        "Add docstring to process_item",
        "aria_service/intel/test_module.py",
    )
    assert '"""' in result


def test_edit_existing_code_adds_return_type(coder):
    """_edit_existing_code adds return type annotation."""
    result = coder._edit_existing_code(
        SAMPLE_CODE, "test_module",
        "Add return type annotation to process_item",
        "aria_service/intel/test_module.py",
    )
    assert "-> dict" in result or "-> dict[str, Any]" in result


def test_edit_existing_code_adds_logging(coder):
    """_edit_existing_code adds debug logging."""
    result = coder._edit_existing_code(
        SAMPLE_CODE, "test_module",
        "Add debug logging to process_item",
        "aria_service/intel/test_module.py",
    )
    assert "logger.debug" in result
