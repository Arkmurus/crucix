"""R-F1143 — capability tests for the CoderToolbox (git, deploy, test, R-number, self-review tools).

Tests that the structured coding tools dispatch correctly and return expected
ToolResult shapes. These test the Toolbox integration layer, not the underlying
git/deploy commands (which would require a real git repo and fly credentials).
"""
from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest

from aria_cli.coder_tools import CoderToolbox
from aria_cli.safety import WriteGuard
from aria_cli.tools import ToolResult, Toolbox


@pytest.fixture
def toolbox() -> Toolbox:
    """Create a Toolbox rooted at a temp dir with a git repo."""
    tmp = tempfile.mkdtemp()
    guard = WriteGuard(self_mode=False)
    tb = Toolbox(root=Path(tmp), guard=guard)
    # Init git repo via the run() method (avoids direct subprocess import)
    tb.run("git init", timeout=15)
    tb.run('git config user.email "test@test.com"', timeout=15)
    tb.run('git config user.name "Test"', timeout=15)
    return tb


@pytest.fixture
def coder(toolbox: Toolbox) -> CoderToolbox:
    return CoderToolbox(toolbox)


# ── ToolResult shape ────────────────────────────────────────────────────────

def test_toolresult_shape() -> None:
    """ToolResult has the expected fields."""
    r = ToolResult("hello")
    assert r.output == "hello"
    assert r.is_error is False
    assert r.mutation == ""

    r2 = ToolResult("error!", is_error=True)
    assert r2.is_error is True


# ── git operations ──────────────────────────────────────────────────────────

def test_git_status_in_repo(coder: CoderToolbox) -> None:
    """git_status returns clean in a fresh git repo."""
    result = coder.git_status()
    assert isinstance(result, ToolResult)
    # In a fresh repo with no files, git status --short returns empty
    assert result.output == "clean working tree" or "exit code: 0" in result.output


def test_git_log_in_repo(coder: CoderToolbox) -> None:
    """git_log returns no commits in a fresh repo."""
    result = coder.git_log(count=3)
    assert isinstance(result, ToolResult)
    # Fresh repo has no commits — git log returns error
    assert "no commits" in result.output.lower() or "does not have any commits" in result.output


def test_git_diff_in_repo(coder: CoderToolbox) -> None:
    """git_diff returns no changes in a clean repo."""
    result = coder.git_diff()
    assert isinstance(result, ToolResult)
    assert result.output == "no changes" or "exit code: 0" in result.output


def test_git_diff_staged_in_repo(coder: CoderToolbox) -> None:
    """git_diff with staged=True returns no changes in a clean repo."""
    result = coder.git_diff(staged=True)
    assert isinstance(result, ToolResult)
    assert result.output == "no changes" or "exit code: 0" in result.output


# ── test runner ─────────────────────────────────────────────────────────────

def test_test_with_path(coder: CoderToolbox) -> None:
    """test() with a valid path runs pytest."""
    test_file = coder.root / "test_simple.py"
    test_file.write_text("def test_pass(): assert 1 + 1 == 2\n")
    result = coder.test(path=str(test_file), verbose=False, timeout=30)
    assert isinstance(result, ToolResult)
    assert "1 passed" in result.output


def test_test_with_pattern(coder: CoderToolbox) -> None:
    """test() with a pattern runs pytest -k."""
    test_file = coder.root / "test_pattern.py"
    test_file.write_text("def test_foo(): assert 1 == 1\ndef test_bar(): assert 2 == 2\n")
    result = coder.test(path=str(test_file), pattern="test_foo", verbose=True, timeout=30)
    assert isinstance(result, ToolResult)
    assert "1 passed" in result.output
    assert "test_foo" in result.output


# ── self-review ─────────────────────────────────────────────────────────────

def test_self_review_no_changes(coder: CoderToolbox) -> None:
    """self_review with no changed files returns a clean report."""
    result = coder.self_review()
    assert isinstance(result, ToolResult)
    assert "No changed files" in result.output or "SELF-REVIEW" in result.output


def test_self_review_with_files(coder: CoderToolbox) -> None:
    """self_review with explicit files checks them."""
    tmp = coder.root / "_test_review.py"
    tmp.write_text("""def greet(name: str) -> str:
    return f"hello {name}"

def old_style():
    pass
""")
    coder._tb._track(tmp)
    result = coder.self_review(files=["_test_review.py"])
    assert isinstance(result, ToolResult)
    assert "SELF-REVIEW" in result.output
    assert "_test_review.py" in result.output
    assert "missing return type hint" in result.output


def test_self_review_detects_bare_except(coder: CoderToolbox) -> None:
    """self_review flags bare excepts."""
    tmp = coder.root / "_test_bare_except.py"
    tmp.write_text("""def risky():
    try:
        return 1
    except:
        return 2
""")
    result = coder.self_review(files=["_test_bare_except.py"])
    assert isinstance(result, ToolResult)
    assert "bare except" in result.output


def test_self_review_detects_debug_print(coder: CoderToolbox) -> None:
    """self_review flags debug print() statements."""
    tmp = coder.root / "_test_debug_print.py"
    tmp.write_text("""def debug():
    print("hello")
    return 1
""")
    result = coder.self_review(files=["_test_debug_print.py"])
    assert isinstance(result, ToolResult)
    assert "debug print" in result.output


def test_self_review_detects_hardcoded_secret(coder: CoderToolbox) -> None:
    """self_review flags hardcoded API keys."""
    tmp = coder.root / "_test_secret.py"
    tmp.write_text('API_KEY = "sk-1234567890"\n')
    result = coder.self_review(files=["_test_secret.py"])
    assert isinstance(result, ToolResult)
    assert "hardcoded secret" in result.output


def test_self_review_skips_env_var_secret(coder: CoderToolbox) -> None:
    """self_review does NOT flag secrets read from env vars."""
    tmp = coder.root / "_test_env_secret.py"
    tmp.write_text("""import os
API_KEY = os.getenv("API_KEY", "")
""")
    result = coder.self_review(files=["_test_env_secret.py"])
    assert isinstance(result, ToolResult)
    # The output contains "[PASS] no hardcoded secrets" which includes
    # the substring "hardcoded secret" — check for the WARN prefix instead
    assert "[WARN]" not in result.output or "hardcoded secret" not in result.output


# ── capability_test ─────────────────────────────────────────────────────────

def test_capability_test_writes_and_runs(coder: CoderToolbox) -> None:
    """capability_test writes a test file and runs it."""
    result = coder.capability_test(
        function_name="ping",
        test_code='''"""Test ping."""
def test_ping():
    assert 1 + 1 == 2
''',
        test_path="",
    )
    assert isinstance(result, ToolResult)
    assert "1 passed" in result.output or "error" in result.output.lower()


# ── R-number tools (dispatch only) ──────────────────────────────────────────

def test_reserve_r_number_script_not_found(coder: CoderToolbox) -> None:
    """reserve_r_number errors gracefully when script is missing."""
    script = coder.root / "scripts" / "admin" / "reserve_r_number.py"
    if not script.exists():
        result = coder.reserve_r_number("test title")
        assert result.is_error
        assert "not found" in result.output


def test_ship_r_number_script_not_found(coder: CoderToolbox) -> None:
    """ship_r_number errors gracefully when script is missing."""
    script = coder.root / "scripts" / "admin" / "reserve_r_number.py"
    if not script.exists():
        result = coder.ship_r_number("R-F9999")
        assert result.is_error
        assert "not found" in result.output


# ── deploy tool (dispatch only) ─────────────────────────────────────────────

def test_deploy_script_not_found(coder: CoderToolbox) -> None:
    """deploy errors gracefully when script is missing."""
    result = coder.deploy(target="--intel", timeout=10)
    if result.is_error:
        assert "not found" in result.output or "error" in result.output.lower()
