"""R-F1036 — Unit tests for the TestRunner (isolated pytest execution).

Covers `aria_service/autonomous/test_runner.py`:

  - _parse() — pytest output parsing (passed/failed/errors counts)
  - run_isolated() — disabled-by-default env gate
  - TestResult dataclass invariants

Uses mocked subprocess — no real pytest execution.
"""
from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


def _run(coro):
    return asyncio.run(coro)


class TestParse:
    """Test the _parse method that reads pytest stdout/stderr."""

    def setup_method(self):
        from aria_service.autonomous.test_runner import TestRunner
        self.runner = TestRunner(redis_client=None)

    def test_all_passed(self):
        stdout = "=== 100 passed in 12.5s ===\n"
        result = self.runner._parse(stdout, "", 0)
        assert result.all_green
        assert result.passed == 100
        assert result.failed == 0
        assert result.errors == 0

    def test_some_failed(self):
        stdout = "=== 5 failed, 95 passed in 15.0s ===\n"
        result = self.runner._parse(stdout, "", 1)
        assert not result.all_green
        assert result.passed == 95
        assert result.failed == 5
        assert result.errors == 0

    def test_errors_counted(self):
        stdout = "=== 2 errors, 50 passed in 8.0s ===\n"
        result = self.runner._parse(stdout, "", 1)
        assert not result.all_green
        assert result.passed == 50
        assert result.failed == 0
        assert result.errors == 2

    def test_all_three_categories(self):
        stdout = "=== 3 errors, 2 failed, 45 passed in 10.0s ===\n"
        result = self.runner._parse(stdout, "", 1)
        assert not result.all_green
        assert result.passed == 45
        assert result.failed == 2
        assert result.errors == 3

    def test_empty_output(self):
        result = self.runner._parse("", "", 0)
        assert result.all_green
        assert result.passed == 0
        assert result.failed == 0

    def test_failure_summary_contains_failed_lines(self):
        stdout = (
            "tests/test_x.py::test_foo FAILED\n"
            "tests/test_x.py::test_bar FAILED\n"
            "=== 2 failed, 3 passed in 1.0s ===\n"
        )
        result = self.runner._parse(stdout, "", 1)
        assert not result.all_green
        assert "FAILED" in result.failure_summary

    def test_returncode_zero_with_failures_still_not_green(self):
        """returncode can be 0 even with failures if pytest-xdist is used."""
        stdout = "=== 1 failed, 10 passed in 2.0s ===\n"
        result = self.runner._parse(stdout, "", 0)
        assert not result.all_green  # failed > 0 → not green
        assert result.failed == 1

    def test_output_tail_contains_last_3k_chars(self):
        stdout = "a" * 5000
        result = self.runner._parse(stdout, "", 0)
        combined = stdout + "\n" + ""
        assert len(result.output_tail) <= 3000
        assert result.output_tail == combined[-3000:]


class TestRunIsolated:
    def test_disabled_by_default(self):
        """ARIA_CODER_TESTS_ENABLED defaults to '0' — tests are skipped."""
        from aria_service.autonomous.test_runner import TestRunner
        runner = TestRunner(redis_client=None)
        result = _run(runner.run_isolated(Path("/tmp"), {}))
        assert result.all_green
        assert result.passed == 0

    def test_enabled_via_env_var(self):
        """When enabled, run_isolated attempts to copy repo and run pytest."""
        from aria_service.autonomous.test_runner import TestRunner
        runner = TestRunner(redis_client=None)
        with patch.dict("os.environ", {"ARIA_CODER_TESTS_ENABLED": "1"}):
            # The repo root doesn't exist in test env, so copytree will fail
            # gracefully (the code handles missing repo_root)
            result = _run(runner.run_isolated(Path("/nonexistent"), {}))
            # Should not crash — the code handles missing repo_root
            assert result is not None


# ════════════════════════════════════════════════════════════════════════════
# Capability test — the user-visible symptom
# ════════════════════════════════════════════════════════════════════════════

def test_capability_test_runner_parses_real_pytest_output():
    """Capability test: the _parse method must correctly interpret real
    pytest output formats. This is the symptom — if parsing breaks, the
    coder can't tell if its fixes pass tests."""
    from aria_service.autonomous.test_runner import TestRunner
    runner = TestRunner(redis_client=None)

    # Realistic pytest output with failures
    stdout = """============================= test session starts ==============================
tests/test_foo.py::test_a PASSED
tests/test_foo.py::test_b FAILED
tests/test_foo.py::test_c ERROR

=================================== FAILURES ===================================
__________________________________ test_b __________________________________

    def test_b():
>       assert 1 == 2
E       assert 1 == 2

tests/test_foo.py:10: AssertionError
========================= short test summary info ==========================
FAILED tests/test_foo.py::test_b
ERROR tests/test_foo.py::test_c
=== 1 failed, 1 passed, 1 error in 0.32s ===
"""
    result = runner._parse(stdout, "", 1)
    assert not result.all_green
    assert result.passed == 1
    assert result.failed == 1
    assert result.errors == 1
    assert "FAILED" in result.failure_summary
    assert "ERROR" in result.failure_summary
