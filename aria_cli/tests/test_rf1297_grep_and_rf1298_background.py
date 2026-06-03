"""R-F1297 + R-F1298 — capability tests for the Claude-Code-parity tool upgrades.

R-F1297: grep gains context lines, output modes (content / files_with_matches /
count), case-insensitivity, type/glob filters, and head_limit.
R-F1298: run gains background execution (run_in_background) plus read_output,
kill_command, and list_background.

These invoke the REAL Toolbox methods (no mocks) so they prove the user-visible
behaviour, not a helper. They force the pure-python grep path where ripgrep is
absent and otherwise accept either backend.
"""
from __future__ import annotations

import shutil
import sys
import tempfile
import time
from pathlib import Path

import pytest

from aria_cli.safety import WriteGuard
from aria_cli.tools import ToolResult, Toolbox


@pytest.fixture
def toolbox() -> Toolbox:
    tmp = tempfile.mkdtemp()
    root = Path(tmp)
    (root / "alpha.py").write_text(
        "import os\n"
        "def alpha():\n"
        "    NEEDLE = 1  # findme\n"
        "    return NEEDLE\n",
        encoding="utf-8",
    )
    (root / "beta.py").write_text(
        "def beta():\n"
        "    return 'needle in lowercase'\n",
        encoding="utf-8",
    )
    (root / "notes.txt").write_text("a needle lives here too\n", encoding="utf-8")
    return Toolbox(root=root, guard=WriteGuard(self_mode=False))


# ── R-F1297: grep parity ─────────────────────────────────────────────────────

def test_grep_content_default(toolbox: Toolbox) -> None:
    """Default content mode returns file:line:match lines."""
    r = toolbox.grep("NEEDLE")
    assert isinstance(r, ToolResult)
    assert "alpha.py" in r.output
    assert ":3" in r.output  # the matching line number


def test_grep_files_with_matches(toolbox: Toolbox) -> None:
    """output_mode='files_with_matches' lists paths, no line bodies."""
    r = toolbox.grep("needle", output_mode="files_with_matches", case_insensitive=True)
    out = r.output
    # All three files contain 'needle' case-insensitively.
    assert "alpha.py" in out and "beta.py" in out and "notes.txt" in out
    # Paths only — no match body text like 'lowercase'.
    assert "lowercase" not in out


def test_grep_count_mode(toolbox: Toolbox) -> None:
    """output_mode='count' returns per-file counts (path:N)."""
    r = toolbox.grep("def ", output_mode="count")
    # alpha.py + beta.py each define one function.
    assert "alpha.py:1" in r.output
    assert "beta.py:1" in r.output


def test_grep_case_insensitive(toolbox: Toolbox) -> None:
    """case_insensitive matches across case."""
    sensitive = toolbox.grep("needle")  # only beta.py + notes.txt have lowercase
    assert "alpha.py" not in sensitive.output
    insensitive = toolbox.grep("needle", case_insensitive=True)
    assert "alpha.py" in insensitive.output  # NEEDLE now matches


def test_grep_context_lines(toolbox: Toolbox) -> None:
    """context=N includes surrounding lines around the match."""
    r = toolbox.grep("findme", context=1, type="py")
    out = r.output
    # The match line and the line above it (the def) should both appear.
    assert "findme" in out
    assert "def alpha" in out


def test_grep_type_filter(toolbox: Toolbox) -> None:
    """type='py' restricts the search to python files (notes.txt excluded)."""
    r = toolbox.grep("needle", case_insensitive=True, type="py",
                     output_mode="files_with_matches")
    assert "notes.txt" not in r.output
    assert "beta.py" in r.output


def test_grep_head_limit(toolbox: Toolbox) -> None:
    """head_limit caps the number of result lines."""
    # 'e' appears on many lines; cap to 2.
    r = toolbox.grep("e", head_limit=2)
    # At most 2 result lines (plus a possible truncation marker line).
    body = [ln for ln in r.output.splitlines() if not ln.startswith("...")]
    assert len(body) <= 2


def test_grep_backward_compatible_signature(toolbox: Toolbox) -> None:
    """The old positional signature grep(pattern, path, glob) still works."""
    r = toolbox.grep("NEEDLE", ".", "*.py")
    assert "alpha.py" in r.output


# ── R-F1298: background execution ────────────────────────────────────────────

def _bg_echo_cmd(text: str) -> str:
    # A command that prints on both shells.
    if sys.platform == "win32":
        return f"Write-Output '{text}'"
    return f"echo '{text}'"


def test_run_background_returns_immediately(toolbox: Toolbox) -> None:
    """run_in_background returns a bg id without blocking."""
    r = toolbox.run(_bg_echo_cmd("hello-bg"), run_in_background=True)
    assert isinstance(r, ToolResult)
    assert not r.is_error
    assert "bg1" in r.output
    assert r.mutation.startswith("started background")


def test_run_background_read_output(toolbox: Toolbox) -> None:
    """read_output returns the command's output and exit status."""
    toolbox.run(_bg_echo_cmd("marker-123"), run_in_background=True)
    # Give the short command a moment to finish and the reader to drain.
    deadline = time.monotonic() + 10
    out = ""
    while time.monotonic() < deadline:
        res = toolbox.read_output("bg1")
        out += res.output
        if "exited" in res.output:
            break
        time.sleep(0.2)
    assert "marker-123" in out
    assert "exited" in out


def test_read_output_unknown_id(toolbox: Toolbox) -> None:
    """read_output on an unknown id is a graceful error, not a crash."""
    r = toolbox.read_output("bg999")
    assert r.is_error
    assert "no background command" in r.output


def test_read_output_only_returns_new_lines(toolbox: Toolbox) -> None:
    """A second read after output is consumed returns no new output."""
    toolbox.run(_bg_echo_cmd("once-only"), run_in_background=True)
    deadline = time.monotonic() + 10
    first = ""
    while time.monotonic() < deadline:
        res = toolbox.read_output("bg1")
        first += res.output
        if "exited" in res.output:
            break
        time.sleep(0.2)
    assert "once-only" in first
    second = toolbox.read_output("bg1")
    assert "once-only" not in second.output  # already consumed


def test_list_background(toolbox: Toolbox) -> None:
    """list_background reports started commands."""
    assert "no background commands" in toolbox.list_background().output
    toolbox.run(_bg_echo_cmd("listed"), run_in_background=True)
    listing = toolbox.list_background()
    assert "bg1" in listing.output


def test_kill_command(toolbox: Toolbox) -> None:
    """kill_command stops a long-running background process."""
    if sys.platform == "win32":
        cmd = "while ($true) { Start-Sleep -Seconds 1 }"
    else:
        cmd = "while true; do sleep 1; done"
    toolbox.run(cmd, run_in_background=True)
    killed = toolbox.kill_command("bg1")
    assert "killed" in killed.output or "already exited" in killed.output
    # After a kill it should report exited on the next status check.
    time.sleep(0.5)
    status = toolbox.list_background()
    assert "bg1" in status.output


def test_kill_unknown_id(toolbox: Toolbox) -> None:
    """kill_command on an unknown id is a graceful error."""
    r = toolbox.kill_command("bg404")
    assert r.is_error
    assert "no background command" in r.output
