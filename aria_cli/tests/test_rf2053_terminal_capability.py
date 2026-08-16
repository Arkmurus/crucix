"""R-F2053 capability tests — terminal-capability negotiation for the input box.

The bug: under a console-less terminal (Git Bash / MSYS / winpty / redirected
stdio) prompt_toolkit's Win32Output raises NoConsoleScreenBufferError AT
PTPromptSession() construction — upstream of the per-prompt guard — so the whole
REPL crashed on launch with a traceback. The improvement decides up front whether
the anchored box can run and picks the basic line-mode prompt deliberately.

These tests drive the decision function (_probe_prompt_toolkit_capability) — the
exact code that gates box construction in _repl — and assert the user-visible
outcome: console-less → basic mode (no crash), real console → box.
"""
from __future__ import annotations

import io

import pytest

# R-F4079 (C-128) — importorskip, NOT a bare import. A module-level hard import
# of an OPTIONAL dependency makes pytest raise a COLLECTION error and stop: when
# prompt_toolkit was absent this single line prevented 45 CLI test files and 22
# service test files from running at all. An absent optional dep must skip ONE
# module, never silence the suite.
ptd = pytest.importorskip("prompt_toolkit.output.defaults")

import aria_cli.cli as cli  # noqa: E402  (must follow the importorskip guard)


class _TTY(io.StringIO):
    """A stdio stand-in that claims to be an interactive terminal."""
    def isatty(self) -> bool:  # noqa: D401
        return True


def _prep(monkeypatch, *, available=True, stdin_tty=True, stdout_tty=True):
    """Reset the probe's cache + present a controlled terminal environment."""
    cli._PT_CAPABILITY = None  # plain reset — independent of monkeypatch teardown
    monkeypatch.setattr(cli, "PROMPT_TOOLKIT_AVAILABLE", available, raising=False)
    monkeypatch.delenv("ARIA_CLI_BASIC_PROMPT", raising=False)
    monkeypatch.setattr(cli.sys, "stdin", _TTY() if stdin_tty else io.StringIO())
    monkeypatch.setattr(cli.sys, "stdout", _TTY() if stdout_tty else io.StringIO())


def _set_create_output(monkeypatch, fn):
    monkeypatch.setattr(ptd, "create_output", fn)
    assert ptd.create_output is fn  # patch must actually be in place


def test_console_less_terminal_falls_back_not_crash(monkeypatch):
    """create_output raising (the Git Bash / MSYS case) → basic mode, no raise."""
    def _raise(*a, **k):
        raise RuntimeError("Found xterm-256color, expecting a Windows console")

    _prep(monkeypatch)
    _set_create_output(monkeypatch, _raise)
    ok, reason = cli._probe_prompt_toolkit_capability()
    assert ok is False
    assert "console" in reason.lower()


def test_real_console_uses_box(monkeypatch):
    """create_output succeeding (real console) → anchored box is selected."""
    _prep(monkeypatch)
    _set_create_output(monkeypatch, lambda *a, **k: object())
    ok, reason = cli._probe_prompt_toolkit_capability()
    assert ok is True
    assert reason == "interactive console"


def test_non_tty_is_basic_mode(monkeypatch):
    """Piped/redirected stdio (not a TTY) → basic mode without even probing PT."""
    _prep(monkeypatch, stdin_tty=False)
    # create_output must never even be consulted on the non-TTY short-circuit.
    _set_create_output(monkeypatch, lambda *a, **k: pytest_fail_if_called())
    ok, reason = cli._probe_prompt_toolkit_capability()
    assert ok is False
    assert "interactive terminal" in reason


def pytest_fail_if_called(*a, **k):  # pragma: no cover - guard
    raise AssertionError("create_output should not be called on the non-TTY path")


def test_operator_override_forces_basic(monkeypatch):
    """ARIA_CLI_BASIC_PROMPT=1 forces basic mode even on a capable console."""
    _prep(monkeypatch)
    monkeypatch.setenv("ARIA_CLI_BASIC_PROMPT", "1")
    _set_create_output(monkeypatch, lambda *a, **k: object())
    ok, reason = cli._probe_prompt_toolkit_capability()
    assert ok is False
    assert "ARIA_CLI_BASIC_PROMPT" in reason


def test_prompt_toolkit_absent_is_basic(monkeypatch):
    """No prompt_toolkit installed → basic mode, clearly stated."""
    _prep(monkeypatch, available=False)
    ok, reason = cli._probe_prompt_toolkit_capability()
    assert ok is False
    assert "not installed" in reason


def test_verdict_is_cached(monkeypatch):
    """The probe is consulted on every loop boundary — it must cache, not re-run
    create_output each time."""
    calls = {"n": 0}

    def _count(*a, **k):
        calls["n"] += 1
        return object()

    _prep(monkeypatch)
    _set_create_output(monkeypatch, _count)
    cli._probe_prompt_toolkit_capability()
    cli._probe_prompt_toolkit_capability()
    cli._probe_prompt_toolkit_capability()
    assert calls["n"] == 1, "probe re-ran create_output instead of caching"
