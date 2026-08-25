# -*- coding: utf-8 -*-
"""R-F4314 — capability tests for the CLI lifecycle hooks.

These live in aria_service/tests/ so the pre-commit capability-test guard can
find them (it scans aria_service/tests/). They drive the REAL Hooks class to
prove the user-visible behaviour:

1. The default PostToolUse compile-check fires when a broken .py file is
   written (the R-F2126 "syntax error shipped to main" failure class is caught
   the moment it is written).
2. A PreToolUse hook can BLOCK a tool call (fail-closed).
3. A hook that raises is caught and reported — it never breaks the agent loop.
4. A fresh Hooks() always carries the structural compile-check hook.
"""
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from aria_cli.hooks import Hooks, _default_post_tool_use  # noqa: E402
from aria_cli.tools import ToolResult  # noqa: E402


def test_rf4314_default_compile_check_catches_broken_py(tmp_path):
    """The default PostToolUse hook flags a .py file with a syntax error."""
    bad = tmp_path / "broken.py"
    bad.write_text("def broken(:\n    pass\n", encoding="utf-8")
    warn = _default_post_tool_use("write_file", {"path": str(bad)}, ToolResult("ok"))
    assert warn is not None
    assert "py_compile failed" in warn
    assert str(bad) in warn


def test_rf4314_default_compile_check_passes_clean_py(tmp_path):
    """A clean .py file produces no warning."""
    good = tmp_path / "good.py"
    good.write_text("def ok():\n    return 1\n", encoding="utf-8")
    warn = _default_post_tool_use("write_file", {"path": str(good)}, ToolResult("ok"))
    assert warn is None


def test_rf4314_pre_tool_use_hook_can_block():
    """A PreToolUse hook returning a ToolResult blocks the tool call."""
    hooks = Hooks()
    hooks.pre_tool_use.append(
        lambda name, args: ToolResult("blocked by test hook", is_error=True)
    )
    block = hooks.run_pre_tool_use("run", {"command": "rm -rf /"})
    assert block is not None
    assert "blocked by test hook" in block.output


def test_rf4314_pre_tool_use_hook_allow_passthrough():
    """A PreToolUse hook returning None allows the tool call."""
    hooks = Hooks()
    hooks.pre_tool_use.append(lambda name, args: None)
    assert hooks.run_pre_tool_use("run", {"command": "echo hi"}) is None


def test_rf4314_hook_exception_never_breaks_loop():
    """A hook that raises is caught and reported; the loop continues."""
    hooks = Hooks()

    def boom(name, args):
        raise RuntimeError("hook exploded")

    hooks.pre_tool_use.append(boom)
    hooks.post_tool_use.append(boom)
    hooks.stop.append(boom)
    assert hooks.run_pre_tool_use("run", {}) is None
    assert hooks.run_post_tool_use("run", {}, ToolResult("ok")) == []
    assert hooks.run_stop(object()) == []


def test_rf4314_hooks_default_compile_check_is_always_on():
    """A fresh Hooks() always carries the structural compile-check hook."""
    hooks = Hooks()
    assert _default_post_tool_use in hooks.post_tool_use


def test_rf4314_report_signal_never_raises():
    """report_signal (used by hooks and sub-agents to wire to the brain) never
    raises and returns a status string — even when the brain is unreachable or
    not configured. This is the §21a wiring contract: a broken brain must never
    break the hook/sub-agent path."""
    from aria_cli import brain as brain_mod

    # Unconfigured (no token) → returns a "not wired" status, never raises.
    status = brain_mod.report_signal(
        signal_type="aria_cli_hook_failed",
        content="test signal",
        self_mode=False,
    )
    assert isinstance(status, str)
    assert status  # non-empty status string

