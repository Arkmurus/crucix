"""R-F1438 — capability tests for the CLI prompt crash-proofing.

These drive the REAL `aria_cli.cli._read_line_robust` helper (not a
re-implementation), proving the prompt_toolkit input box can never crash the
REPL: on any box failure it latches and degrades to the plain input() prompt.

Context: the operator hit two CLI problems — (1) an infinite "sent to ARIA
mid-task" flood (a _OPERATOR_QUEUE feedback loop, fixed by the busy-gates in
cli.py) and (2) the box crashing the whole session (R-F1407b/R-F1423/R-F1426
all shipped broken because units can't see the screen). This guard makes the
box failure non-fatal so the CLI stays usable even when the fancy box breaks.
"""
from __future__ import annotations

import pytest

from aria_cli import cli


@pytest.fixture(autouse=True)
def _reset_latch():
    cli._PT_PROMPT_BROKEN[0] = False
    yield
    cli._PT_PROMPT_BROKEN[0] = False


def test_happy_path_uses_box():
    """When the box works, its result is returned and no latch is set."""
    out = cli._read_line_robust(lambda: "typed in box", lambda: "FALLBACK")
    assert out == "typed in box"
    assert cli._PT_PROMPT_BROKEN[0] is False


def test_box_failure_falls_back_and_latches():
    """A box error degrades to the fallback, latches, and notifies once."""
    seen: list[Exception] = []

    def boom():
        raise RuntimeError("got Future attached to a different loop")

    out = cli._read_line_robust(
        boom, lambda: "FALLBACK", on_box_failure=seen.append
    )
    assert out == "FALLBACK"
    assert cli._PT_PROMPT_BROKEN[0] is True          # latched
    assert len(seen) == 1 and isinstance(seen[0], RuntimeError)


def test_once_latched_box_is_skipped():
    """After a latch, the box callable is never invoked again this session."""
    cli._PT_PROMPT_BROKEN[0] = True
    calls = {"box": 0}

    def box():
        calls["box"] += 1
        return "BOX"

    out = cli._read_line_robust(box, lambda: "FALLBACK")
    assert out == "FALLBACK"
    assert calls["box"] == 0


def test_none_prompt_uses_fallback():
    """No prompt_toolkit available (prompt_fn=None) -> plain fallback, no latch."""
    out = cli._read_line_robust(None, lambda: "FALLBACK")
    assert out == "FALLBACK"
    assert cli._PT_PROMPT_BROKEN[0] is False


def test_eof_and_keyboardinterrupt_propagate():
    """Control-flow exceptions must NOT be swallowed or latched."""
    def eof():
        raise EOFError()

    with pytest.raises(EOFError):
        cli._read_line_robust(eof, lambda: "X")
    assert cli._PT_PROMPT_BROKEN[0] is False

    def kbi():
        raise KeyboardInterrupt()

    with pytest.raises(KeyboardInterrupt):
        cli._read_line_robust(kbi, lambda: "X")
    assert cli._PT_PROMPT_BROKEN[0] is False


def test_on_box_failure_error_is_swallowed():
    """A broken on_box_failure callback must not break the fallback."""
    def boom():
        raise ValueError("box dead")

    def bad_notify(_e):
        raise RuntimeError("notify also dead")

    out = cli._read_line_robust(boom, lambda: "FALLBACK", on_box_failure=bad_notify)
    assert out == "FALLBACK"
    assert cli._PT_PROMPT_BROKEN[0] is True
