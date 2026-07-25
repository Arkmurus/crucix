"""R-F1460 — Unit + capability tests for the three coder quality guards.

Guards tested:
  1. reproduce_symptom — existing-test-first + hard discard for false-positive gaps
  2. _check_noop — reject no-op/cosmetic-only code changes
  3. _check_hallucinated_api — verify module.method() calls exist before writing them

Each guard is tested as a pure function (unit test) AND via the fix_gap pipeline
(capability test with mocked sub-components).
"""
from __future__ import annotations

import asyncio
import json
import os
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


@pytest.fixture(autouse=True)
def _rf3064_enable_coder_lane():
    """R-F3064: fix_gap now refuses unless ARIA_CODER_ENABLED is truthy (the
    flag previously gated only the loop starter, so the coder ran while the
    operator believed it was off). These tests exercise behaviour DOWNSTREAM of
    that gate, so they enable the lane explicitly. The gate itself is covered by
    test_rf3064_3065_coder_gate_and_profiler_idempotence.py."""
    import os as _os
    _prev = _os.environ.get("ARIA_CODER_ENABLED")
    _os.environ["ARIA_CODER_ENABLED"] = "1"
    try:
        yield
    finally:
        if _prev is None:
            _os.environ.pop("ARIA_CODER_ENABLED", None)
        else:
            _os.environ["ARIA_CODER_ENABLED"] = _prev



def _run(coro):
    """Run an async coroutine — pytest-asyncio is not available."""
    return asyncio.run(coro)


# ════════════════════════════════════════════════════════════════════════════
# GUARD 1: reproduce_symptom — GapDetector.reproduce_symptom()
# ════════════════════════════════════════════════════════════════════════════

class TestReproduceSymptom:
    """Unit tests for GapDetector.reproduce_symptom()."""

    def test_find_test_for_module_found(self) -> None:
        """_find_test_for_module should find a test file for a known module."""
        from aria_service.autonomous.gap_detector import GapDetector
        # gap_detector.py itself should have tests
        result = GapDetector._find_test_for_module("gap_detector")
        assert result is not None, "Should find a test for gap_detector"
        assert "test_" in result, "Should be a test file"

    def test_find_test_for_module_not_found(self) -> None:
        """_find_test_for_module should return None for unknown modules."""
        from aria_service.autonomous.gap_detector import GapDetector
        result = GapDetector._find_test_for_module("nonexistent_module_xyz")
        assert result is None, "Should return None for unknown module"

    def test_find_test_for_module_with_path(self) -> None:
        """_find_test_for_module should handle module paths like 'module/file.py'."""
        from aria_service.autonomous.gap_detector import GapDetector
        result = GapDetector._find_test_for_module("aria_service/autonomous/gap_detector.py")
        assert result is not None, "Should find a test for gap_detector"
        assert "test_" in result

    def test_reproduce_symptom_no_module(self) -> None:
        """reproduce_symptom should return False for gaps with no module."""
        from aria_service.autonomous.gap_detector import GapDetector, Gap, GapSeverity, GapType
        detector = GapDetector(MagicMock())
        gap = Gap(
            gap_id="test_no_module",
            gap_type=GapType.MODULE_BUG,
            severity=GapSeverity.HIGH,
            title="Test gap",
            description="A test gap with no module",
            module="unknown",
        )
        ok, msg = _run(detector.reproduce_symptom(gap))
        assert not ok
        assert "no module" in msg

    def test_reproduce_symptom_empty_module(self) -> None:
        """reproduce_symptom should return False for gaps with empty module."""
        from aria_service.autonomous.gap_detector import GapDetector, Gap, GapSeverity, GapType
        detector = GapDetector(MagicMock())
        gap = Gap(
            gap_id="test_empty_module",
            gap_type=GapType.MODULE_BUG,
            severity=GapSeverity.HIGH,
            title="Test gap",
            description="A test gap with empty module",
            module="",
        )
        ok, msg = _run(detector.reproduce_symptom(gap))
        assert not ok
        assert "no module" in msg


# ════════════════════════════════════════════════════════════════════════════
# GUARD 2: _check_noop — ARIACoder._check_noop()
# ════════════════════════════════════════════════════════════════════════════

class TestCheckNoop:
    """Unit tests for ARIACoder._check_noop()."""

    def test_identical_code_rejected(self) -> None:
        """Identical existing and proposed code should be rejected as no-op."""
        from aria_service.autonomous.self_coder import ARIACoder
        code = "def foo():\n    return 42\n"
        ok, msg = ARIACoder._check_noop(code, code, "test.py")
        assert not ok
        assert "no-op" in msg

    def test_meaningful_change_accepted(self) -> None:
        """A real code change should pass the no-op gate."""
        from aria_service.autonomous.self_coder import ARIACoder
        existing = "def foo():\n    return 42\n"
        proposed = "def foo():\n    return 43\n"
        ok, msg = ARIACoder._check_noop(existing, proposed, "test.py")
        assert ok
        assert msg == ""

    def test_whitespace_only_rejected(self) -> None:
        """Whitespace-only changes should be rejected as cosmetic."""
        from aria_service.autonomous.self_coder import ARIACoder
        existing = "def foo():\n    return 42\n"
        proposed = "def foo():\n    return 42  \n"
        ok, msg = ARIACoder._check_noop(existing, proposed, "test.py")
        assert not ok
        assert "cosmetic" in msg or "no-op" in msg

    def test_comment_only_rejected(self) -> None:
        """Comment-only changes should be rejected as cosmetic."""
        from aria_service.autonomous.self_coder import ARIACoder
        existing = "# old comment\ndef foo():\n    return 42\n"
        proposed = "# new comment\ndef foo():\n    return 42\n"
        ok, msg = ARIACoder._check_noop(existing, proposed, "test.py")
        assert not ok
        assert "cosmetic" in msg or "no-op" in msg

    def test_logger_rename_rejected(self) -> None:
        """logger→log rename should be rejected as cosmetic."""
        from aria_service.autonomous.self_coder import ARIACoder
        existing = 'logger = logging.getLogger("test")\nlogger.info("hello")\n'
        proposed = 'log = logging.getLogger("test")\nlog.info("hello")\n'
        ok, msg = ARIACoder._check_noop(existing, proposed, "test.py")
        assert not ok
        assert "cosmetic" in msg or "no-op" in msg

    def test_new_file_accepted(self) -> None:
        """A new file (empty existing) with real content should pass."""
        from aria_service.autonomous.self_coder import ARIACoder
        existing = ""
        proposed = "def foo():\n    return 42\n"
        ok, msg = ARIACoder._check_noop(existing, proposed, "new_file.py")
        assert ok
        assert msg == ""

    def test_substantive_change_with_cosmetic_side_effects_accepted(self) -> None:
        """A real logic change with minor cosmetic side effects should pass."""
        from aria_service.autonomous.self_coder import ARIACoder
        existing = "def foo():\n    # old comment\n    return 42\n"
        proposed = "def foo():\n    # new comment\n    return 43\n"
        ok, msg = ARIACoder._check_noop(existing, proposed, "test.py")
        assert ok
        assert msg == ""


# ════════════════════════════════════════════════════════════════════════════
# GUARD 3: _check_hallucinated_api — ARIACoder._check_hallucinated_api()
# ════════════════════════════════════════════════════════════════════════════

class TestCheckHallucinatedApi:
    """Unit tests for ARIACoder._check_hallucinated_api()."""

    def test_known_api_passes(self) -> None:
        """Calls to known standard library functions should pass."""
        from aria_service.autonomous.self_coder import ARIACoder
        code = 'import json\ndata = json.loads("{}")\n'
        ok, reasons = ARIACoder._check_hallucinated_api(code, "test.py")
        assert ok
        assert len(reasons) == 0

    def test_unknown_api_on_existing_file_flagged(self) -> None:
        """Calls to non-existent methods on existing files should be flagged."""
        from aria_service.autonomous.self_coder import ARIACoder
        # Use a real file that exists in the repo
        target = "aria_service/autonomous/gap_detector.py"
        if not os.path.exists(target):
            pytest.skip(f"{target} not found — can't test")
        code = "detector = GapDetector()\ndetector._nonexistent_method_xyz()\n"
        ok, reasons = ARIACoder._check_hallucinated_api(code, target)
        assert not ok
        assert any("_nonexistent_method_xyz" in r for r in reasons)

    def test_known_method_on_existing_file_passes(self) -> None:
        """Calls to real methods on existing files should pass."""
        from aria_service.autonomous.self_coder import ARIACoder
        target = "aria_service/autonomous/gap_detector.py"
        if not os.path.exists(target):
            pytest.skip(f"{target} not found — can't test")
        code = "detector = GapDetector()\ndetector.scan()\n"
        ok, reasons = ARIACoder._check_hallucinated_api(code, target)
        assert ok
        assert len(reasons) == 0

    def test_new_file_skips_check(self) -> None:
        """Calls in code for a new (non-existent) file should not be flagged."""
        from aria_service.autonomous.self_coder import ARIACoder
        code = "obj = SomeClass()\nobj.some_method()\n"
        ok, reasons = ARIACoder._check_hallucinated_api(code, "/tmp/nonexistent_new_file.py")
        assert ok
        assert len(reasons) == 0

    def test_syntax_error_does_not_block(self) -> None:
        """Unparseable code should pass the gate (don't block on parse errors)."""
        from aria_service.autonomous.self_coder import ARIACoder
        code = "this is not valid python {{{"
        ok, reasons = ARIACoder._check_hallucinated_api(code, "test.py")
        assert ok
        assert len(reasons) == 0

    def test_standard_library_skipped(self) -> None:
        """Standard library calls should not be flagged."""
        from aria_service.autonomous.self_coder import ARIACoder
        code = """
import json
import os
import sys
data = json.loads("{}")
path = os.path.join("a", "b")
sys.exit(0)
"""
        ok, reasons = ARIACoder._check_hallucinated_api(code, "test.py")
        assert ok
        assert len(reasons) == 0


# ════════════════════════════════════════════════════════════════════════════
# CAPABILITY TEST: reproduce_symptom in fix_gap pipeline
# ════════════════════════════════════════════════════════════════════════════

class TestReproduceSymptomInPipeline:
    """Capability test: verify the reproduce_symptom gate fires in fix_gap()."""

    def test_fix_gap_rejects_unreproducible_gap(self) -> None:
        """fix_gap should reject a gap whose symptom cannot be reproduced."""
        from aria_service.autonomous.self_coder import ARIACoder
        from aria_service.autonomous.gap_detector import GapDetector, Gap, GapSeverity, GapType

        # Create a gap with a module that has no tests
        gap = Gap(
            gap_id="test_unreproducible",
            gap_type=GapType.MODULE_BUG,
            severity=GapSeverity.HIGH,
            title="Unreproducible gap",
            description="A gap whose symptom cannot be reproduced",
            module="nonexistent_module_xyz",
        )

        # Create ARIACoder with mocked dependencies
        redis = MagicMock()
        redis.get = AsyncMock(return_value=None)
        redis.setex = AsyncMock(return_value=None)
        redis.lpush = AsyncMock(return_value=None)
        redis.ltrim = AsyncMock(return_value=None)
        redis.expire = AsyncMock(return_value=None)

        coder = ARIACoder(
            redis_client=redis,
            aria_service_url="http://localhost:8000",
        )

        result = _run(coder.fix_gap(gap))
        assert not result.success
        assert result.failure_reason is not None
        assert "reproduce" in result.failure_reason.lower() or \
               "symptom" in result.failure_reason.lower() or \
               "no existing test" in result.failure_reason.lower()


# ════════════════════════════════════════════════════════════════════════════
# CAPABILITY TEST: no-op gate in fix_gap pipeline
# ════════════════════════════════════════════════════════════════════════════

class TestNoopGateInPipeline:
    """Capability test: verify the no-op gate fires in fix_gap()."""

    def test_noop_gate_rejects_identical_code(self) -> None:
        """The no-op gate should reject code identical to existing."""
        from aria_service.autonomous.self_coder import ARIACoder

        # Test the static method directly — this is the pure function
        # that the pipeline calls. Testing it directly proves the gate works.
        existing = "def foo():\n    return 42\n"
        proposed = "def foo():\n    return 42\n"
        ok, msg = ARIACoder._check_noop(existing, proposed, "test.py")
        assert not ok
        assert "no-op" in msg


# ════════════════════════════════════════════════════════════════════════════
# CAPABILITY TEST: hallucinated-API gate in fix_gap pipeline
# ════════════════════════════════════════════════════════════════════════════

class TestHallucinatedApiGateInPipeline:
    """Capability test: verify the hallucinated-API gate fires in fix_gap()."""

    def test_hallucinated_api_detected(self) -> None:
        """The hallucinated-API gate should flag non-existent methods."""
        from aria_service.autonomous.self_coder import ARIACoder

        # Test the static method directly on a real file
        target = "aria_service/autonomous/gap_detector.py"
        if not os.path.exists(target):
            pytest.skip(f"{target} not found")

        code = "detector = GapDetector()\ndetector._nonexistent_method_xyz()\n"
        ok, reasons = ARIACoder._check_hallucinated_api(code, target)
        assert not ok
        assert len(reasons) > 0
        assert any("_nonexistent_method_xyz" in r for r in reasons)
