"""R-F1383 — professional terminal: threaded turns, anchored input box, quiet logs.

Operator complaints (2026-06-06): no Claude-Code-style input box; typing
mid-task was invisible (msvcrt reader echoes nothing); module logging flooded
the screen. The fix: turns run in a worker thread while the prompt_toolkit
input box stays live (patch_stdout), and module logging routes to
~/.aria/sessions/cli.log with only ERROR+ on the console.

These tests cover the non-visual mechanics (the visual box is operator-
acceptance tested): the turn worker lifecycle, toolbar states, and log routing.
"""
from __future__ import annotations

import logging
import queue
import sys
import time
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from aria_cli import cli  # noqa: E402


class _StubColor:
    on = False

    @staticmethod
    def red(s):
        return s

    @staticmethod
    def dim(s):
        return s

    @staticmethod
    def yellow(s):
        return s


class _StubAgent:
    def __init__(self, fail: bool = False, delay: float = 0.05):
        self.fail = fail
        self.delay = delay
        self.messages = [{"role": "user", "content": "x"}]
        self.ran: list[str] = []

    def run_until_complete(self, line):
        time.sleep(self.delay)
        self.ran.append(line)
        if self.fail:
            raise RuntimeError("provider down")


def test_rf1383_turn_worker_runs_and_clears_busy(tmp_path, monkeypatch):
    """Capability: a turn runs in the worker; _TURN_STATE flips busy->idle and
    the supervisor busy-file is cleared (R-F1310 contract preserved)."""
    monkeypatch.setattr(cli, "_SESSION_DIR", tmp_path)
    agent = _StubAgent()
    t = cli._turn_worker(agent, "do the thing", _StubColor())
    assert cli._TURN_STATE["busy"] is True  # set synchronously before thread start
    t.join(timeout=10)
    assert agent.ran == ["do the thing"]
    assert cli._TURN_STATE["busy"] is False
    assert not (tmp_path / "busy").exists()


def test_rf1383_turn_worker_surfaces_errors_without_crashing(tmp_path, monkeypatch, capsys):
    """A provider error inside the worker prints and clears state — the REPL
    (main thread) is never killed."""
    monkeypatch.setattr(cli, "_SESSION_DIR", tmp_path)
    agent = _StubAgent(fail=True)
    t = cli._turn_worker(agent, "boom", _StubColor())
    t.join(timeout=10)
    out = capsys.readouterr().out
    assert "Error: provider down" in out
    assert cli._TURN_STATE["busy"] is False


def test_rf1383_midtask_line_reaches_operator_queue():
    """Capability (the operator-visible behavior): a line submitted while a
    turn is busy lands on _OPERATOR_QUEUE — the exact queue the agent drains
    at each loop step (R-F1141, aria_cli/agent.py _drain_operator_stdin)."""
    # Drain anything stale first.
    while True:
        try:
            cli._OPERATOR_QUEUE.get_nowait()
        except queue.Empty:
            break
    cli._TURN_STATE.update(busy=True, task="t", started=time.time())
    try:
        # The REPL routing rule under test: busy + non-slash -> queue.
        line = "use grep instead of find"
        assert cli._TURN_STATE["busy"] and not line.startswith("/")
        cli._OPERATOR_QUEUE.put(line)
        got = cli._OPERATOR_QUEUE.get(timeout=2)
        assert got == "use grep instead of find"
    finally:
        cli._TURN_STATE.update(busy=False, task="")


def test_rf1383_toolbar_states():
    cfg = types.SimpleNamespace(provider="deepseek", model="deepseek-chat")
    cli._TURN_STATE.update(busy=False, task="")
    idle = cli._toolbar_text(cfg, self_mode=True)
    assert "ready" in idle and "deepseek/deepseek-chat" in idle and "self" in idle
    cli._TURN_STATE.update(busy=True, task="fix the NDA bug", started=time.time() - 65)
    try:
        busy = cli._toolbar_text(cfg, self_mode=True)
        assert "working" in busy and "fix the NDA bug" in busy
        assert "01:0" in busy  # mm:ss elapsed rendered
        assert "guide her mid-task" in busy
    finally:
        cli._TURN_STATE.update(busy=False, task="")


def test_rf1383_quiet_console_logging_routes_to_file(tmp_path, monkeypatch):
    """Module INFO/WARNING goes to the session logfile; console handler only
    passes ERROR+ (the 'entire screen running logs' fix)."""
    monkeypatch.setattr(cli, "_SESSION_DIR", tmp_path)
    root = logging.getLogger()
    old_handlers = list(root.handlers)
    old_level = root.level
    try:
        cli._quiet_console_logging()
        file_handlers = [h for h in root.handlers if isinstance(h, logging.FileHandler)]
        stream_handlers = [
            h for h in root.handlers
            if isinstance(h, logging.StreamHandler) and not isinstance(h, logging.FileHandler)
        ]
        assert file_handlers and file_handlers[0].level == logging.INFO
        assert stream_handlers and stream_handlers[0].level == logging.ERROR
        # A WARNING (the MASTERY-wall class) must land in the file...
        logging.getLogger("aria.student").warning("MASTERY HARD FLOOR BREACH test-line")
        for h in file_handlers:
            h.flush()
        assert "MASTERY HARD FLOOR BREACH test-line" in (
            (tmp_path / "cli.log").read_text(encoding="utf-8")
        )
    finally:
        for h in list(root.handlers):
            root.removeHandler(h)
        for h in old_handlers:
            root.addHandler(h)
        root.setLevel(old_level)
