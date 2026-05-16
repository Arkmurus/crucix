"""R-F559 (2026-05-16) — verify-after-fix automation tests.

Capability tests the verifier script catches what it should:
  - Detects R-F<n> in commit messages
  - Maps each R-number to its expected test file
  - Reads the R-F540 registry correctly
  - Returns the right exit code on each failure mode

These are unit-style — they shell out via subprocess only for the
end-to-end argparse contract.
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "verify_commit.py"


def _import_module():
    """Import scripts/verify_commit.py as a module for white-box tests."""
    import importlib.util
    spec = importlib.util.spec_from_file_location("_vc_rf559", _SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


def test_script_exists_and_is_python() -> None:
    assert _SCRIPT.exists(), "R-F559 verifier script missing"
    text = _SCRIPT.read_text(encoding="utf-8")
    assert text.startswith("#!/usr/bin/env python3"), "shebang must be set"


def test_r_number_regex_catches_expected_forms() -> None:
    mod = _import_module()
    text = "fix: R-F540 bring up + R-F555 trim + cherry of R-F1 ancient"
    found = sorted({f"R-F{m.group(1)}" for m in mod._R_NUMBER_PATTERN.finditer(text)})
    # 2-4 digit window means R-F1 (one digit) is NOT captured — that's fine,
    # legacy single-digit R-numbers don't go through this discipline.
    assert "R-F540" in found
    assert "R-F555" in found


def test_test_files_present_for_rf540_finds_real_test() -> None:
    """Capability: the rf540 registry test must be detectable."""
    mod = _import_module()
    found = mod._test_files_present_for("R-F540")
    names = [p.name for p in found]
    assert any("rf540" in n for n in names), (
        f"Expected to find R-F540 test file; got {names}"
    )


def test_test_files_present_for_unknown_r_number_is_empty() -> None:
    mod = _import_module()
    found = mod._test_files_present_for("R-F99999")
    assert found == []


def test_registry_r_numbers_reads_real_log() -> None:
    """Capability: the verifier reads the same registry file as the
    reservation log writer."""
    mod = _import_module()
    found = mod._registry_r_numbers()
    assert "R-F540" in found, "R-F540 self-registration missing"


def test_cli_help_works() -> None:
    """Smoke: --help renders without crashing."""
    proc = subprocess.run(
        [sys.executable, str(_SCRIPT), "--help"],
        capture_output=True, text=True, timeout=15,
    )
    assert proc.returncode == 0
    assert "R-F559" in proc.stdout


def test_cli_fast_skip_passes_with_no_r_numbers(tmp_path: Path) -> None:
    """Capability: --skip-tests --range that finds zero R-numbers exits 0."""
    # Use a no-op range that yields no log output.
    proc = subprocess.run(
        [sys.executable, str(_SCRIPT), "--skip-tests", "--range", "HEAD..HEAD"],
        capture_output=True, text=True, timeout=15,
    )
    assert proc.returncode == 0, (
        f"Expected 0; got {proc.returncode}\nstdout: {proc.stdout}\nstderr: {proc.stderr}"
    )


def test_hook_file_exists_and_calls_verifier() -> None:
    """The shipped pre-push hook must call verify_commit.py."""
    hook = Path(__file__).resolve().parents[2] / "scripts" / "git-hooks" / "pre-push"
    assert hook.exists(), "R-F559 pre-push hook missing"
    text = hook.read_text(encoding="utf-8")
    assert "verify_commit.py" in text


def test_installer_sets_core_hookspath() -> None:
    """The installer script must actually configure core.hooksPath."""
    installer = Path(__file__).resolve().parents[2] / "scripts" / "install_git_hooks.py"
    assert installer.exists()
    text = installer.read_text(encoding="utf-8")
    assert "core.hooksPath" in text
    assert "scripts/git-hooks" in text or 'pre_push.parent.relative_to(repo)' in text
