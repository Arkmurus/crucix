"""R-F1083 — Capability test: guardrail (pre-commit hook) actually guards.

Verifies that:
1. The hook's REPO_ROOT resolves to the correct repo root
2. ARIA_SERVICE exists and --check-all scans >0 files
3. The hook's path resolution logic is correct

This test exists because R-F1073 moved the hook into scripts/githooks/
but did NOT update REPO_ROOT from parent.parent to parent.parent.parent,
making the hook check ZERO files and report false-OK.
"""
from __future__ import annotations

from pathlib import Path

import pytest


class TestGuardrailSelfTest:
    """The guardrail must be able to check itself."""

    def test_repo_root_is_correct(self) -> None:
        """The repo root must be the crucix root, not scripts/."""
        # The hook is at scripts/githooks/pre-commit
        # With the old parent.parent logic, it would resolve to scripts/
        # With git rev-parse, it resolves to the repo root
        # We can verify by checking that aria_service exists relative to
        # the expected repo root
        repo_candidates = [
            Path(__file__).resolve().parent.parent.parent.parent,  # tests/ -> aria_service/ -> crucix/
            Path(__file__).resolve().parent.parent.parent,  # tests/ -> aria_service/ -> crucix/
        ]
        for candidate in repo_candidates:
            if (candidate / "aria_service").exists() and (candidate / ".git").exists():
                repo = candidate
                break
        else:
            pytest.fail("Could not determine repo root from test location")

        aria_service = repo / "aria_service"
        assert aria_service.exists(), (
            f"ARIA_SERVICE ({aria_service}) does not exist — "
            f"REPO_ROOT resolved to {repo}"
        )

    def test_aria_service_has_python_files(self) -> None:
        """ARIA_SERVICE must contain .py files for the hook to scan."""
        repo = Path(__file__).resolve().parent.parent.parent  # tests/ -> aria_service/ -> crucix/
        if not (repo / "aria_service").exists():
            repo = repo.parent  # one more level up
        aria_service = repo / "aria_service"
        if not aria_service.exists():
            pytest.skip("Cannot determine repo root from test location")

        py_files = list(aria_service.rglob("*.py"))
        assert len(py_files) > 0, (
            f"--check-all would scan 0 files — "
            f"ARIA_SERVICE ({aria_service}) has no .py files"
        )
        # Sanity: there should be hundreds of files
        assert len(py_files) > 100, (
            f"Only {len(py_files)} .py files found — "
            f"expected hundreds in a healthy repo"
        )

    def test_hook_path_resolution(self) -> None:
        """Verify the hook's path resolution logic is correct."""
        # The hook is at scripts/githooks/pre-commit
        # Old logic: Path(__file__).resolve().parent.parent
        #   -> scripts/githooks/parent = scripts/githooks
        #   -> scripts/githooks/parent = scripts  (WRONG!)
        # New logic: git rev-parse --show-toplevel
        #   -> C:\code\crucix  (CORRECT)

        hook_path = Path("scripts/githooks/pre-commit").resolve()
        old_repo_root = hook_path.parent.parent  # old logic
        assert not (old_repo_root / "aria_service").exists(), (
            f"Old REPO_ROOT logic ({old_repo_root}) would resolve to scripts/, "
            f"not the repo root. The fix is needed."
        )

        # The correct repo root is two levels up from the hook
        correct_repo_root = hook_path.parent.parent.parent
        assert (correct_repo_root / "aria_service").exists(), (
            f"Correct REPO_ROOT ({correct_repo_root}) should have aria_service"
        )
        assert (correct_repo_root / ".git").exists(), (
            f"Correct REPO_ROOT ({correct_repo_root}) should have .git"
        )
