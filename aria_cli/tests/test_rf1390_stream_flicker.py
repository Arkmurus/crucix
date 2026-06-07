"""R-F1390 — anchored-mode streaming must COALESCE to line granularity.

Operator (2026-06-07): the terminal "glitches every time text appears — like
an old TV, blinking rather than flowing." Cause: behind the live prompt_toolkit
input box, every sys.stdout.flush() repaints the box region; token-by-token
write+flush = hundreds of repaints = flicker. Fix: in anchored mode, buffer
stream chunks and write only complete lines (partial tail flushed at
stream_end). Non-anchored streaming stays per-token (no patch_stdout app there).
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from aria_cli import cli  # noqa: E402


def _ui():
    return cli.TerminalUI(auto_approve=True, interactive=True,
                          color=cli._Color(enabled=False), theme="dark")


def test_rf1390_anchored_coalesces_to_lines(capsys):
    """Token-by-token deltas with NO newline must NOT reach stdout yet — they
    are held until a newline, so the box repaints per-line not per-token."""
    ui = _ui()
    ui.anchored = True
    for tok in ["Hel", "lo ", "wor", "ld"]:   # no newline anywhere
        ui.stream_delta(tok)
    out = capsys.readouterr().out
    # Only the prefix has been emitted; the partial line is buffered.
    assert "Hello world" not in out, "partial line leaked (would flicker per token)"
    assert ui._stream_line_buf == "Hello world"


def test_rf1390_anchored_flushes_on_newline(capsys):
    ui = _ui()
    ui.anchored = True
    ui.stream_delta("line one\npartial two")
    out = capsys.readouterr().out
    assert "line one\n" in out, "completed line should flush"
    assert "partial two" not in out, "partial tail should stay buffered"
    assert ui._stream_line_buf == "partial two"


def test_rf1390_stream_end_flushes_tail(capsys):
    ui = _ui()
    ui.anchored = True
    ui.stream_delta("only a partial")
    assert ui._stream_line_buf == "only a partial"
    ui.stream_end()
    out = capsys.readouterr().out
    assert "only a partial" in out, "stream_end must flush the buffered tail"
    assert ui._stream_line_buf == ""


def test_rf1390_nonanchored_streams_per_token(capsys):
    """Regression: one-shot / no-prompt_toolkit mode keeps live per-token output
    (no patch_stdout app to repaint there)."""
    ui = _ui()
    ui.anchored = False
    ui.stream_delta("tok-no-newline")
    out = capsys.readouterr().out
    assert "tok-no-newline" in out, "non-anchored must emit immediately"
