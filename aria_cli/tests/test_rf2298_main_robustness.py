"""R-F2298 — the `aria` CLI entry point must fail gracefully.

Before this, main() had no top-level guard: a failure in _build_agent (missing
API key, brain import, network), a one-shot task error, an early Ctrl+C, or a
broken output pipe dumped a raw traceback on the operator. main() now wraps
_run_cli and returns clean exit codes with a friendly message (full traceback to
~/.aria/sessions/crash.log). The interactive REPL keeps its own per-turn Ctrl+C
handling — this guards everything OUTSIDE it.

(NB: ARIA's gap report claimed a duplicated `_append_log` in cli.py — verified
false; it is defined once. The real robustness gap was the missing top-level
guard, which these tests cover.)
"""
from __future__ import annotations

import io
import sys

import aria_cli.cli as cli


def _patch_run(monkeypatch, exc):
    def _boom(argv=None):
        raise exc
    monkeypatch.setattr(cli, "_run_cli", _boom)


def test_unexpected_exception_is_clean_exit_1(monkeypatch, capsys):
    _patch_run(monkeypatch, RuntimeError("boom-test"))
    rc = cli.main([])
    assert rc == 1
    err = capsys.readouterr().err
    assert "aria: RuntimeError: boom-test" in err          # friendly one-liner
    assert "Traceback (most recent call last)" not in err  # no raw dump to console
    assert "crash.log" in err                              # points at the crash log


def test_keyboard_interrupt_returns_130(monkeypatch, capsys):
    _patch_run(monkeypatch, KeyboardInterrupt())
    rc = cli.main([])
    assert rc == 130
    assert "Interrupted." in capsys.readouterr().err


def test_broken_pipe_returns_0(monkeypatch):
    _patch_run(monkeypatch, BrokenPipeError())
    assert cli.main([]) == 0


def test_systemexit_propagates(monkeypatch):
    # argparse --help/--version/bad-args must still exit as intended.
    def _sysexit(argv=None):
        raise SystemExit(2)
    monkeypatch.setattr(cli, "_run_cli", _sysexit)
    try:
        cli.main([])
    except SystemExit as e:
        assert e.code == 2
    else:  # pragma: no cover
        raise AssertionError("SystemExit was swallowed — argparse exits would break")


def test_success_passthrough(monkeypatch):
    monkeypatch.setattr(cli, "_run_cli", lambda argv=None: 0)
    assert cli.main([]) == 0
    monkeypatch.setattr(cli, "_run_cli", lambda argv=None: 7)
    assert cli.main([]) == 7


def test_append_log_defined_once():
    """Guards against the (false) GAP-2 ever becoming true: exactly one def."""
    import pathlib
    src = pathlib.Path(cli.__file__).read_text(encoding="utf-8")
    assert src.count("\ndef _append_log(") == 1
