"""R-F1124 — Capability tests for the pre-commit capability test gate.

Tests that check_capability_tests() correctly:
1. Flags new functions without corresponding test coverage
2. Passes functions that HAVE test coverage
3. Ignores private helpers (starting with _)
4. Ignores exempt functions (main, lifespan, setup, teardown)
"""
from __future__ import annotations

from pathlib import Path

import pytest

from scripts.pre_commit_checks import check_capability_tests


# ── Fixtures ────────────────────────────────────────────────────────────────

@pytest.fixture
def sample_intel_file(tmp_path: Path) -> Path:
    """Create a mock intel module file."""
    f = tmp_path / "my_engine.py"
    f.write_text("async def my_public_function(): pass\n")
    return f


@pytest.fixture
def sample_test_file(tmp_path: Path) -> Path:
    """Create a mock test file."""
    f = tmp_path / "test_my_engine.py"
    f.write_text("def test_my_public_function(): pass\n")
    return f


# ── Tests ───────────────────────────────────────────────────────────────────

class TestCapabilityTestGate:
    """Proves the pre-commit capability test gate works correctly."""

    def test_flags_function_without_test(self, monkeypatch, tmp_path: Path):
        """A new function without a corresponding test is flagged."""
        # Create a mock intel file with a new function
        intel_file = tmp_path / "aria_service" / "intel" / "my_engine.py"
        intel_file.parent.mkdir(parents=True)
        intel_file.write_text("async def my_new_function(): pass\n")

        # Point the test dir to a dir with no matching tests
        test_dir = tmp_path / "aria_service" / "tests"
        test_dir.mkdir(parents=True)
        (test_dir / "test_other.py").write_text("def test_other(): pass\n")

        # Monkey-patch ARIA_SERVICE to point to our tmp structure
        from scripts import pre_commit_checks as pcc
        monkeypatch.setattr(pcc, "ARIA_SERVICE", tmp_path / "aria_service")

        issues = check_capability_tests([intel_file])
        assert len(issues) > 0
        assert "my_new_function" in issues[0]
        assert "NO capability test" in issues[0]

    def test_passes_function_with_test(self, monkeypatch, tmp_path: Path):
        """A new function WITH a corresponding test passes."""
        intel_file = tmp_path / "aria_service" / "intel" / "my_engine.py"
        intel_file.parent.mkdir(parents=True)
        intel_file.write_text("async def my_public_function(): pass\n")

        test_dir = tmp_path / "aria_service" / "tests"
        test_dir.mkdir(parents=True)
        (test_dir / "test_my_engine.py").write_text(
            "def test_my_public_function(): pass\n"
        )

        from scripts import pre_commit_checks as pcc
        monkeypatch.setattr(pcc, "ARIA_SERVICE", tmp_path / "aria_service")

        issues = check_capability_tests([intel_file])
        assert len(issues) == 0

    def test_ignores_private_helpers(self, monkeypatch, tmp_path: Path):
        """Private helpers (starting with _) are not flagged."""
        intel_file = tmp_path / "aria_service" / "intel" / "my_engine.py"
        intel_file.parent.mkdir(parents=True)
        intel_file.write_text("def _private_helper(): pass\n")

        test_dir = tmp_path / "aria_service" / "tests"
        test_dir.mkdir(parents=True)

        from scripts import pre_commit_checks as pcc
        monkeypatch.setattr(pcc, "ARIA_SERVICE", tmp_path / "aria_service")

        issues = check_capability_tests([intel_file])
        assert len(issues) == 0

    def test_ignores_exempt_functions(self, monkeypatch, tmp_path: Path):
        """Exempt functions (main, lifespan) are not flagged."""
        intel_file = tmp_path / "aria_service" / "intel" / "my_engine.py"
        intel_file.parent.mkdir(parents=True)
        intel_file.write_text("def main(): pass\nasync def lifespan(): pass\n")

        test_dir = tmp_path / "aria_service" / "tests"
        test_dir.mkdir(parents=True)

        from scripts import pre_commit_checks as pcc
        monkeypatch.setattr(pcc, "ARIA_SERVICE", tmp_path / "aria_service")

        issues = check_capability_tests([intel_file])
        assert len(issues) == 0

    def test_skips_test_files(self, monkeypatch, tmp_path: Path):
        """Test files themselves are not checked."""
        test_file = tmp_path / "aria_service" / "tests" / "test_foo.py"
        test_file.parent.mkdir(parents=True)
        test_file.write_text("def test_something(): pass\n")

        from scripts import pre_commit_checks as pcc
        monkeypatch.setattr(pcc, "ARIA_SERVICE", tmp_path / "aria_service")

        issues = check_capability_tests([test_file])
        assert len(issues) == 0
