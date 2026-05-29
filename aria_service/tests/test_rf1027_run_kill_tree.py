"""R-F1027 — the aria CLI `run` tool must never let a hung command wedge the
agent: on timeout it kills the whole process TREE and returns promptly, and
normal commands still work + report the real exit code.
"""
from __future__ import annotations

import sys
import time

from aria_cli.safety import WriteGuard
from aria_cli.tools import Toolbox


def _box(tmp_path):
    return Toolbox(root=tmp_path, guard=WriteGuard(self_mode=False))


def test_run_timeout_kills_tree_and_returns_promptly(tmp_path):
    box = _box(tmp_path)
    cmd = "Start-Sleep -Seconds 30" if sys.platform == "win32" else "sleep 30"
    start = time.monotonic()
    r = box.run(cmd, timeout=2)
    elapsed = time.monotonic() - start
    assert r.is_error and "timed out" in r.output.lower()
    assert "process tree killed" in r.output.lower()
    # must return shortly after the 2s timeout, not hang for the full 30s
    assert elapsed < 12, f"run did not return promptly after timeout ({elapsed:.1f}s)"


def test_run_normal_command_works_and_reports_exit_code(tmp_path):
    box = _box(tmp_path)
    r = box.run("echo hello-bulletproof")
    assert "hello-bulletproof" in r.output
    assert "exit code: 0" in r.output


def test_run_nonzero_exit_is_error(tmp_path):
    box = _box(tmp_path)
    r = box.run('python -c "import sys; sys.exit(3)"')
    assert r.is_error and "exit code: 3" in r.output


def test_run_streams_output_live(tmp_path):
    """R-F1030 — run() emits each output line via on_output as it is produced,
    so the UI shows progress during long commands (never silent)."""
    box = _box(tmp_path)
    seen: list[str] = []
    box.on_output = lambda ln: seen.append(ln)
    if sys.platform == "win32":
        cmd = "Write-Output line-a; Write-Output line-b; Write-Output line-c"
    else:
        cmd = "printf 'line-a\\nline-b\\nline-c\\n'"
    r = box.run(cmd, timeout=30)
    assert "exit code: 0" in r.output
    assert "line-a" in seen and "line-c" in seen, f"output not streamed live: {seen}"
    assert "line-a" in r.output and "line-c" in r.output  # also captured for the model
