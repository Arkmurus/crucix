"""R-F1385 — clean CLI: complete box (base via toolbar), no spinner spam, capped output.

Operator (2026-06-06, after R-F1383): the box "has a ceiling but not a base",
the bottom margin "looks messy", and the screen still fills with running logs.
Mechanics under test: the toolbar renders the box BASE + status; anchored mode
suppresses the \\r-redraw spinner (each tick became a new line above the box
through patch_stdout); code-looking run output is capped at 20 lines.
"""
from __future__ import annotations

import sys
import time
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from aria_cli import cli  # noqa: E402


def _ui():
    color = cli._Color(enabled=False)
    return cli.TerminalUI(auto_approve=True, interactive=True, color=color, theme="dark")


def test_rf1385_toolbar_renders_box_base_and_status():
    cfg = types.SimpleNamespace(provider="deepseek", model="deepseek-chat")
    cli._TURN_STATE.update(busy=False, task="")
    out = cli._toolbar_text(cfg, self_mode=True)
    base, status = out.split("\n", 1)
    assert base.startswith("╰") and set(base[1:]) == {"─"}, "line 1 must close the box"
    assert "ready" in status
    cli._TURN_STATE.update(busy=True, task="polish", started=time.time())
    try:
        out2 = cli._toolbar_text(cfg, self_mode=True)
        assert out2.splitlines()[0].startswith("╰")
        assert "working" in out2
    finally:
        cli._TURN_STATE.update(busy=False, task="")


def test_rf1385_anchored_mode_suppresses_spinner_and_clearline(capsys):
    ui = _ui()
    ui.anchored = True
    ui.thinking_start("researching")
    assert ui._spin_thread is None, "anchored mode must not start the spinner thread"
    ui._ensure_clear_line()
    out = capsys.readouterr().out
    assert "\r" not in out and "\x1b[K" not in out, "anchored mode must not emit redraw noise"
    assert "researching" not in out, "anchored mode prints no static spinner line either"


def test_rf1385_unanchored_spinner_still_works(capsys):
    """Regression: one-shot / fallback mode keeps the static activity line."""
    ui = _ui()
    ui.anchored = False
    ui._can_animate = False  # static branch (no TTY in tests)
    ui.thinking_start("thinking")
    out = capsys.readouterr().out
    assert "thinking" in out


def test_rf1385_code_output_capped(capsys, monkeypatch):
    """A 200-line code-looking run result prints <= ~21 lines + a hint —
    never the full wall (the 'takes the entire screen' fix)."""
    from aria_cli.coder_tools import ToolResult

    ui = _ui()
    ui._rich_console = None  # plain path: deterministic line counting
    big = "\n".join(f"    def f_{i}(): return {i}" for i in range(200))
    ui.tool_result("run", ToolResult(output=big, is_error=False))
    out_lines = [l for l in capsys.readouterr().out.splitlines() if l.strip()]
    assert any("+180 lines" in l for l in out_lines), "truncation hint required"
    assert len(out_lines) <= 25, f"output not capped: {len(out_lines)} lines"
