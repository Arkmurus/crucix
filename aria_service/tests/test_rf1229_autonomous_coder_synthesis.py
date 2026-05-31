"""R-F1229 — Capability test for AutonomousCoder real code synthesis.

Tests that the AutonomousCoder can produce REAL code edits (not stubs)
for common fix patterns: error handling, null checks, logging, retry logic.
"""
from __future__ import annotations

import pytest


# ── Fixtures ────────────────────────────────────────────────────────────────

SAMPLE_MODULE = '''"""Sample module for testing."""
from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger("aria.test_module")


async def process_item(item_id: str, data: dict) -> dict:
    """Process an item and return results."""
    result = {"status": "ok", "item_id": item_id}
    result["processed"] = data.get("value", 0) * 2
    return result


async def lookup_entity(name: str) -> dict:
    """Look up an entity by name."""
    result = {"status": "ok", "name": name}
    return result
'''


# ── Capability tests ───────────────────────────────────────────────────────

def test_autonomous_coder_adds_error_handling():
    """The coder should add try/except to a function when asked to fix errors."""
    from aria_service.intel.autonomous_coder import AutonomousCoder
    coder = AutonomousCoder()
    result = coder._edit_existing_code(
        SAMPLE_MODULE, "test_module",
        "Add error handling to process_item to catch exceptions",
        "aria_service/intel/test_module.py",
    )
    assert "try:" in result
    assert "except Exception" in result
    assert "logger.error" in result
    assert "process_item" in result  # function preserved


def test_autonomous_coder_adds_null_checks():
    """The coder should add null checks when asked to fix None/AttributeError."""
    from aria_service.intel.autonomous_coder import AutonomousCoder
    coder = AutonomousCoder()
    result = coder._edit_existing_code(
        SAMPLE_MODULE, "test_module",
        "Fix AttributeError when item_id is None",
        "aria_service/intel/test_module.py",
    )
    assert "is None" in result
    assert "logger.warning" in result


def test_autonomous_coder_adds_logging():
    """The coder should add logging to a function that lacks it."""
    from aria_service.intel.autonomous_coder import AutonomousCoder
    coder = AutonomousCoder()
    result = coder._edit_existing_code(
        SAMPLE_MODULE, "test_module",
        "Add debug logging to process_item",
        "aria_service/intel/test_module.py",
    )
    assert "logger.debug" in result
    assert "process_item" in result


def test_autonomous_coder_adds_retry_logic():
    """The coder should add retry logic when asked to fix flaky operations."""
    from aria_service.intel.autonomous_coder import AutonomousCoder
    coder = AutonomousCoder()
    result = coder._edit_existing_code(
        SAMPLE_MODULE, "test_module",
        "Add retry logic for flaky API calls in process_item",
        "aria_service/intel/test_module.py",
    )
    assert "MAX_RETRIES" in result
    assert "asyncio.sleep" in result
    assert "attempt" in result


def test_autonomous_coder_adds_timeout_wrapper():
    """The coder should add timeout wrapper to async functions."""
    from aria_service.intel.autonomous_coder import AutonomousCoder
    coder = AutonomousCoder()
    result = coder._edit_existing_code(
        SAMPLE_MODULE, "test_module",
        "Add timeout to prevent process_item from hanging",
        "aria_service/intel/test_module.py",
    )
    assert "TIMEOUT_S" in result
    assert "asyncio.TimeoutError" in result


def test_autonomous_coder_adds_docstring():
    """The coder should add a docstring to a function that lacks one."""
    from aria_service.intel.autonomous_coder import AutonomousCoder
    # Use a module where lookup_entity has no docstring
    code_no_doc = SAMPLE_MODULE.replace(
        '    """Look up an entity by name."""\n', ""
    )
    coder = AutonomousCoder()
    result = coder._edit_existing_code(
        code_no_doc, "test_module",
        "Add docstring to lookup_entity",
        "aria_service/intel/test_module.py",
    )
    assert '"""' in result
    assert "lookup_entity" in result


def test_autonomous_coder_adds_return_type():
    """The coder should add return type annotations."""
    from aria_service.intel.autonomous_coder import AutonomousCoder
    coder = AutonomousCoder()
    result = coder._edit_existing_code(
        SAMPLE_MODULE, "test_module",
        "Add return type annotation to lookup_entity",
        "aria_service/intel/test_module.py",
    )
    assert "-> dict" in result or "-> dict[str, Any]" in result


def test_autonomous_coder_classifies_fix_types():
    """The coder should correctly classify descriptions into fix types."""
    from aria_service.intel.autonomous_coder import AutonomousCoder
    coder = AutonomousCoder()
    assert coder._classify_fix_type("fix error in process_item") == "error_handling"
    assert coder._classify_fix_type("add wiring to brain") == "wiring"
    assert coder._classify_fix_type("add type hints") == "type_annotation"
    # "fix None crash" contains "crash" which maps to error_handling
    assert coder._classify_fix_type("fix None crash") == "error_handling"
    # But "add null check" maps to null_check
    assert coder._classify_fix_type("add null check for item_id") == "null_check"
    assert coder._classify_fix_type("add debug logging") == "logging"
    assert coder._classify_fix_type("add timeout to prevent hang") == "timeout"
    assert coder._classify_fix_type("add retry for flaky API") == "retry"
    assert coder._classify_fix_type("add docstring") == "docstring"
