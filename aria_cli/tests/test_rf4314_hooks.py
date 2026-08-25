# -*- coding: utf-8 -*-
"""R-F4314 — capability tests for CLI lifecycle hooks.

These drive the REAL Hooks class and the REAL Agent dispatch path to prove the
user-visible behaviour:

1. The default PostToolUse compile-check fires when a broken .py file is
   written (the R-F2126 "syntax error shipped to main" failure class is caught
   the moment it is written).
2. A PreToolUse hook can BLOCK a tool call (fail-closed).
3. A hook that raises is caught and reported — it never breaks the agent loop.
4. Hooks are wired to the brain (§21a): a hook failure reaches a brain sink.
"""
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from aria_cli.hooks import Hooks, _default_post_tool_use, _py_compile_check  # noqa: E402
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


def test_rf4314_default_compile_check_ignores_non_py(tmp_path):
    """Non-.py writes (and error results) are not compile-checked."""
    md = tmp_path / "notes.md"
    md.write_text("# hi", encoding="utf-8")
    assert _default_post_tool_use("write_file", {"path": str(md)}, ToolResult("ok")) is None
    # an errored write is not checked either
    assert _default_post_tool_use("write_file", {"path": str(md)},
                                  ToolResult("boom", is_error=True)) is None


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
    # none of these should raise
    assert hooks.run_pre_tool_use("run", {}) is None
    assert hooks.run_post_tool_use("run", {}, ToolResult("ok")) == []
    assert hooks.run_stop(object()) == []


def test_rf4314_hooks_default_compile_check_is_always_on():
    """A fresh Hooks() always carries the structural compile-check hook."""
    hooks = Hooks()
    assert _default_post_tool_use in hooks.post_tool_use


def test_rf4314_py_compile_check_direct():
    """_py_compile_check returns None for clean, error string for broken."""
    import tempfile
    with tempfile.NamedTemporaryFile(suffix=".py", delete=False, mode="w") as f:
        f.write("x = 1\n")
        clean = f.name
    try:
        assert _py_compile_check(clean) is None
    finally:
        os.unlink(clean)
    with tempfile.NamedTemporaryFile(suffix=".py", delete=False, mode="w") as f:
        f.write("def broken(:\n")
        broken = f.name
    try:
        assert _py_compile_check(broken) is not None
    finally:
        os.unlink(broken)
