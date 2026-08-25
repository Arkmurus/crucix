# -*- coding: utf-8 -*-
"""R-F4314 — lifecycle hooks for the ARIA Coder CLI.

Claude Code and Codex both expose lifecycle hooks (PreToolUse, PostToolUse,
Stop) that run shell commands or callbacks at key points in the agent loop.
This module brings the same capability to the ARIA Coder CLI, but as
in-process Python callbacks rather than external shell commands — the CLI is a
local tool and shelling out for every tool call would be slow and fragile.

The default hook set makes the anti-hallucination laws STRUCTURAL instead of
willpower:

  * ``PostToolUse`` on ``write_file`` / ``edit_file`` runs ``py_compile`` on the
    changed file, so a syntax error is caught the moment it is written — not at
    the next ``run``/deploy (the R-F2126 "31 syntax errors shipped to main"
    failure class).

Hooks are wired to the brain (§21a): a hook that fires (success) and a hook
that errors (failure) both reach a brain sink, so a broken hook is visible, not
silent. A hook can never break the agent loop — every callback is wrapped so an
exception is caught, reported, and the loop continues.
"""
from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field
from typing import Callable

from .tools import ToolResult

# Hook names — the lifecycle points the agent loop calls.
PRE_TOOL_USE = "PreToolUse"
POST_TOOL_USE = "PostToolUse"
STOP = "Stop"

# Tools that write Python files and therefore benefit from a compile check.
_PY_WRITE_TOOLS = {"write_file", "edit_file"}


def _py_compile_check(path: str) -> str | None:
    """Compile-check a Python file. Returns an error string, or None if clean.

    Only meaningful for ``.py`` files. Uses ``py_compile`` (no execution, no
    import side effects) so a syntax error is caught without running the code.
    """
    if not path.endswith(".py"):
        return None
    if not os.path.exists(path):
        return None
    try:
        import py_compile
        py_compile.compile(path, doraise=True)
        return None
    except Exception as exc:  # noqa: BLE001 — report the compile failure
        return f"py_compile failed for {path}: {exc}"


def _default_post_tool_use(name: str, args: dict, result: ToolResult) -> str | None:
    """Default PostToolUse hook: compile-check any Python file just written.

    Returns a warning string to surface, or None when nothing to report. This
    makes the R-F2126 failure class (syntax errors shipped to main) impossible
    to introduce silently: the moment a ``.py`` file is written with a syntax
    error, the hook flags it.
    """
    if name not in _PY_WRITE_TOOLS or result.is_error:
        return None
    path = (args or {}).get("path") or ""
    return _py_compile_check(path)


@dataclass
class Hooks:
    """Registry of lifecycle hooks.

    Each hook is a list of callables. A ``PreToolUse`` hook may return a
    ``ToolResult`` to BLOCK the tool call (fail-closed); returning None allows
    it. ``PostToolUse`` and ``Stop`` hooks return a warning string or None.
    """

    pre_tool_use: list[Callable[[str, dict], ToolResult | None]] = field(default_factory=list)
    post_tool_use: list[Callable[[str, dict, ToolResult], str | None]] = field(default_factory=list)
    stop: list[Callable[[object], str | None]] = field(default_factory=list)

    def __post_init__(self) -> None:
        # The default PostToolUse compile-check is always on (structural guard).
        if _default_post_tool_use not in self.post_tool_use:
            self.post_tool_use.append(_default_post_tool_use)

    def run_pre_tool_use(self, name: str, args: dict) -> ToolResult | None:
        """Run all PreToolUse hooks. Returns a blocking ToolResult if any hook
        blocks, else None. A hook that raises is caught and reported (never
        breaks the loop) and does NOT block — fail-open for the hook itself,
        because a broken hook must not silently stop legitimate work."""
        for hook in self.pre_tool_use:
            try:
                block = hook(name, args)
                if block is not None:
                    return block
            except Exception as exc:  # noqa: BLE001 — a hook must never break the loop
                _report_hook_failure(PRE_TOOL_USE, name, exc)
        return None

    def run_post_tool_use(self, name: str, args: dict, result: ToolResult) -> list[str]:
        """Run all PostToolUse hooks. Returns a list of warning strings to
        surface. A hook that raises is caught and reported (never breaks the
        loop)."""
        warnings: list[str] = []
        for hook in self.post_tool_use:
            try:
                warn = hook(name, args, result)
                if warn:
                    warnings.append(warn)
            except Exception as exc:  # noqa: BLE001 — a hook must never break the loop
                _report_hook_failure(POST_TOOL_USE, name, exc)
        return warnings

    def run_stop(self, turn_result: object) -> list[str]:
        """Run all Stop hooks. Returns a list of warning strings to surface."""
        warnings: list[str] = []
        for hook in self.stop:
            try:
                warn = hook(turn_result)
                if warn:
                    warnings.append(warn)
            except Exception as exc:  # noqa: BLE001 — a hook must never break the loop
                _report_hook_failure(STOP, "", exc)
        return warnings


def _report_hook_failure(hook_name: str, tool_name: str, exc: Exception) -> None:
    """Wire a hook failure to the brain (§21a) and stderr. Never raises."""
    try:
        from . import brain as brain_mod
        brain_mod.report_signal(
            signal_type="aria_cli_hook_failed",
            content=f"CLI hook {hook_name} failed on tool '{tool_name}': {exc}",
            self_mode=True,
        )
    except Exception:  # noqa: BLE001 — brain wiring must never break the loop
        pass
    print(f"[aria] hook {hook_name} error on '{tool_name}': {exc}", file=sys.stderr)
