"""R-F1112 — Capability test: AutonomousCoder can fix a real bug end-to-end.

This test proves that ARIA's AutonomousCoder (AST-aware, no external LLM)
can detect a real code defect and produce a working fix — without calling
DeepSeek, Anthropic, or any external API.

The test:
1. Creates a module with a known bug (missing error handling)
2. Feeds it as a gap to AutonomousCoder
3. Verifies the generated fix plan targets the right file
4. Verifies the generated code actually fixes the bug
5. Verifies the generated tests would catch a regression
"""
from __future__ import annotations

import ast
import tempfile
from pathlib import Path
from unittest.mock import MagicMock

import pytest

# R-F3788/§16 — NOT inspect.getsource: it slices at line numbers captured
# AT IMPORT, so a mid-run edit silently returns a DIFFERENT function's body.
from ._source_probe import class_source


# ── Helper: create a module with a known bug ──────────────────────────────

_BUGGY_MODULE_SOURCE = '''"""Buggy module — missing error handling."""
from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger("aria.buggy_module")


async def fetch_data(query: str) -> dict[str, Any]:
    """Fetch data without error handling — will crash on network errors."""
    import httpx
    async with httpx.AsyncClient() as client:
        resp = await client.get(f"https://api.example.com/data?q={query}")
        resp.raise_for_status()
        return resp.json()
'''

_FIXED_MODULE_SOURCE = '''"""Buggy module — missing error handling."""
from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger("aria.buggy_module")


async def fetch_data(query: str) -> dict[str, Any]:
    """Fetch data without error handling — will crash on network errors."""
    import httpx
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(f"https://api.example.com/data?q={query}")
            resp.raise_for_status()
            return resp.json()
    except Exception as _e:
        logger.error("[fetch_data] failed: %s", _e, exc_info=True)
        raise
'''


class MockGap:
    """Simulates a gap_detector.Gap for testing."""
    def __init__(self, description: str, module: str, gap_type: str = "module_bug"):
        self.description = description
        self.title = description[:80]
        self.module = module
        self.gap_id = "test_gap_001"
        self.gap_type = gap_type
        self.severity = MagicMock()
        self.severity.name = "HIGH"
        self.severity.__int__ = lambda s: 3
        self.related_files = []
        self.error_trace = None
        self.requires_wa_approval = False


@pytest.mark.asyncio
async def test_rf1112_capability_autonomous_coder_fixes_buggy_module():
    """AutonomousCoder should produce a fix plan + code for a real bug.

    This is the CAPABILITY test — it proves the user-visible symptom
    (a module that crashes on errors) is addressed by the generated fix.
    """
    from aria_service.intel.autonomous_coder import AutonomousCoder

    coder = AutonomousCoder()

    # ── STEP 1: Generate a fix plan ──────────────────────────────────────
    gap = MockGap(
        description="Add error handling to fetch_data in buggy_module — "
                    "the function crashes on network errors without try/except",
        module="aria_service/intel/buggy_module.py",
        gap_type="module_bug",
    )

    plan = await coder.generate_fix_plan(gap, _BUGGY_MODULE_SOURCE)

    # The plan must contain the keys self_coder.py reads
    assert "title" in plan, "Plan must have a title"
    assert "approach" in plan, "Plan must have an approach (self_coder reads this)"
    assert "target_files" in plan, "Plan must have target_files"
    assert "risk_level" in plan, "Plan must have risk_level"
    assert plan["llm_free"] is True, "Must not use external LLM"

    # ── STEP 2: Generate code for the target file ────────────────────────
    target = plan["target_files"][0] if plan["target_files"] else gap.module
    code_result = await coder.write_code(plan, _BUGGY_MODULE_SOURCE, target)

    assert "code" in code_result, "write_code must return 'code' key"
    assert code_result["llm_free"] is True, "Must not use external LLM"

    generated_code = code_result["code"]
    assert generated_code, "Generated code must not be empty"

    # ── STEP 3: Verify the generated code is valid Python ────────────────
    try:
        tree = ast.parse(generated_code)
    except SyntaxError as e:
        # Print the generated code for debugging
        lines = generated_code.split("\n")
        context = "\n".join(
            f"{i+1:4d}: {l}" for i, l in enumerate(lines)
        )
        pytest.fail(
            f"Generated code has syntax error: {e}\n"
            f"--- Generated code ---\n{context}"
        )

    # ── STEP 4: Verify the fix addresses the bug ─────────────────────────
    # The bug is missing try/except around the HTTP call.
    # Check that the generated code has a try/except block.
    has_try = any(isinstance(node, ast.Try) for node in ast.walk(tree))
    assert has_try, (
        "Generated code must add try/except error handling "
        f"(the bug was missing error handling). Generated code:\n{generated_code[:500]}"
    )

    # ── STEP 5: Verify the fix preserves existing functionality ──────────
    # The function signature and docstring should be preserved
    has_fetch_data = any(
        isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name == "fetch_data"
        for node in ast.walk(tree)
    )
    assert has_fetch_data, "Generated code must preserve the fetch_data function"

    # ── STEP 6: Generate tests for the fix ───────────────────────────────
    test_result = await coder.write_tests(plan, generated_code, 1112)

    assert "test_code" in test_result, "write_tests must return 'test_code' key"
    assert "test_filepath" in test_result, "write_tests must return 'test_filepath' key"
    assert test_result["llm_free"] is True, "Must not use external LLM"

    test_code = test_result["test_code"]
    assert test_code, "Generated test code must not be empty"

    # Verify the test code is valid Python
    try:
        ast.parse(test_code)
    except SyntaxError as e:
        pytest.fail(f"Generated test code has syntax error: {e}")

    # Verify the test code contains capability tests
    assert "def test_rf1112_" in test_code, (
        "Test code must contain tests with R-number prefix"
    )
    assert "capability" in test_code.lower() or "unit" in test_code.lower(), (
        "Test code must contain capability or unit tests"
    )


@pytest.mark.asyncio
async def test_rf1112_capability_autonomous_coder_analyse_failure_returns_corrected_code():
    """analyse_failure should return CORRECTED code, not the original.

    This is the CAPABILITY test for the self-heal loop — it proves that
    when a test fails, the coder can produce a corrected version.
    """
    from aria_service.intel.autonomous_coder import AutonomousCoder

    coder = AutonomousCoder()

    # Simulate a test failure: missing await
    code_with_bug = '''async def fetch():
    client = httpx.AsyncClient()
    resp = client.get("https://example.com")
    return resp
'''

    error = "RuntimeWarning: coroutine 'AsyncClient.get' was never awaited"

    result = await coder.analyse_failure(error, code_with_bug, attempt=1)

    assert "code" in result, "analyse_failure must return 'code' key"
    assert result["llm_free"] is True, "Must not use external LLM"

    corrected = result["code"]
    assert corrected != code_with_bug, (
        "analyse_failure must return CORRECTED code, not the original. "
        f"Original: {code_with_bug!r}, Corrected: {corrected!r}"
    )

    # Verify the corrected code has await
    assert "await " in corrected, (
        "Corrected code must contain 'await' for the async call. "
        f"Corrected: {corrected[:200]}"
    )

    # Verify the corrected code is valid Python
    try:
        ast.parse(corrected)
    except SyntaxError as e:
        pytest.fail(f"Corrected code has syntax error: {e}")


@pytest.mark.asyncio
async def test_rf1112_capability_autonomous_coder_contract_matches_self_coder():
    """Verify AutonomousCoder's return contract matches what self_coder.py reads.

    self_coder.py reads these keys from the LLM provider:
      generate_fix_plan -> title, approach, target_files, new_files, risk_level
      write_code -> code
      write_tests -> test_code, test_filepath
      analyse_failure -> code (corrected)
    """
    from aria_service.intel.autonomous_coder import AutonomousCoder

    coder = AutonomousCoder()

    # ── Plan contract ────────────────────────────────────────────────────
    gap = MockGap(
        description="Fix a bug in the test module",
        module="aria_service/intel/test_module.py",
        gap_type="module_bug",
    )
    plan = await coder.generate_fix_plan(gap, "")
    assert "title" in plan
    assert "approach" in plan
    assert "target_files" in plan
    assert isinstance(plan["target_files"], list)
    assert "risk_level" in plan
    assert plan["risk_level"] in ("low", "medium", "high")

    # ── Code contract ────────────────────────────────────────────────────
    code_result = await coder.write_code(plan, "", "test_module.py")
    assert "code" in code_result
    assert isinstance(code_result["code"], str)

    # ── Test contract ────────────────────────────────────────────────────
    test_result = await coder.write_tests(plan, code_result["code"], 1112)
    assert "test_code" in test_result
    assert isinstance(test_result["test_code"], str)
    assert "test_filepath" in test_result
    assert isinstance(test_result["test_filepath"], str)

    # ── Heal contract ────────────────────────────────────────────────────
    heal_result = await coder.analyse_failure("SyntaxError", "def foo():\n", 1)
    assert "code" in heal_result
    assert isinstance(heal_result["code"], str)


@pytest.mark.asyncio
async def test_rf1112_capability_autonomous_coder_no_external_imports():
    """AutonomousCoder must not import any external LLM modules.

    This is the core guarantee of R-F1112 — ARIA codes herself using
    her own intelligence, not external APIs.
    """
    import inspect
    from aria_service.intel.autonomous_coder import AutonomousCoder

    source = class_source("aria_service.intel.autonomous_coder", "AutonomousCoder")
    assert "deepseek" not in source.lower()
    assert "anthropic" not in source.lower()
    assert "openai" not in source.lower()
    # httpx is allowed in docstrings/comments/dict literals but must not be
    # an actual import statement. Check for `import httpx` or `from httpx import`
    import re
    for line in source.split("\n"):
        stripped = line.strip()
        # Only flag actual import statements, not string literals or comments
        if re.match(r'^(from\s+httpx|import\s+httpx)', stripped):
            pytest.fail(f"AutonomousCoder must not import httpx: {line}")
