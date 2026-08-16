"""R-F1211: Verify PowerShell temp-script approach handles double-quotes correctly.

The old -Command approach mangled double-quotes in commands like
`python -m pytest -k "test_pattern"`. The new -File approach should
preserve them exactly.
"""
import sys
import tempfile
from pathlib import Path

import pytest

from aria_cli.tools import Toolbox, _NO_TEMP_SCRIPT, _cleanup_temp_script
from aria_cli.safety import WriteGuard


def test_temp_script_cleanup_noop() -> None:
    """_cleanup_temp_script is a no-op for sentinel and empty strings."""
    _cleanup_temp_script("")       # should not raise
    _cleanup_temp_script(_NO_TEMP_SCRIPT)  # should not raise
    _cleanup_temp_script("/nonexistent/path/foo.ps1")  # should not raise


def test_temp_script_cleanup_removes_file() -> None:
    """_cleanup_temp_script actually removes the temp file."""
    tmp = tempfile.NamedTemporaryFile(suffix=".ps1", delete=False, mode="w")
    tmp.write("echo hello")
    tmp.close()
    assert Path(tmp.name).exists()
    _cleanup_temp_script(tmp.name)
    assert not Path(tmp.name).exists()


@pytest.mark.skipif(sys.platform != "win32", reason="Windows-specific quoting test")
def test_run_with_double_quotes_preserved() -> None:
    """Commands with double-quotes must execute correctly via temp script."""
    tb = Toolbox(root=Path.cwd(), guard=WriteGuard(Path.cwd()))
    # A command that would break under -Command: echo a string with double-quotes
    result = tb.run('python -c "print(42)"', timeout=10)
    assert not result.is_error, f"Command failed: {result.output}"
    assert "42" in result.output, f"Expected '42' in output: {result.output}"


@pytest.mark.skipif(sys.platform != "win32", reason="Windows-specific quoting test")
def test_run_with_escaped_quotes() -> None:
    """Commands with escaped quotes (like regex patterns) must work."""
    tb = Toolbox(root=Path.cwd(), guard=WriteGuard(Path.cwd()))
    # Simulate a pytest -k pattern with special chars
    result = tb.run('python -c "import re; print(re.search(r\'\\d+\', \'abc123\').group())"', timeout=10)
    assert not result.is_error, f"Command failed: {result.output}"
    assert "123" in result.output, f"Expected '123' in output: {result.output}"


@pytest.mark.skipif(sys.platform != "win32", reason="Windows-specific quoting test")
def test_run_with_pytest_k_pattern() -> None:
    """pytest -k with a pattern containing double-quotes must work."""
    tb = Toolbox(root=Path.cwd(), guard=WriteGuard(Path.cwd()))
    # This is the exact pattern that broke: -k "pattern" inside -Command
    # R-F4080 (C-129) — sys.executable, not the bare name. `python` resolves via
    # PATH to the SYSTEM interpreter here, which has no pytest, so this failed
    # with "No module named pytest" — a PATH accident reported as a quoting bug,
    # which is the defect this file exists to catch. It was invisible until the
    # prompt_toolkit collection abort (C-128) was fixed and these tests ran.
    _py = sys.executable.replace("\\", "/")
    result = tb.run(
        f'"{_py}" -m pytest aria_cli/tests/test_rf1143_coder_tools.py '
        f'-k "test_toolresult_shape" --no-header -q',
        timeout=60,
    )
    assert not result.is_error, f"Command failed: {result.output}"
    assert "1 passed" in result.output, f"Expected test pass: {result.output}"
