"""R-F1265 — Capability test: _content handles overflow without breaking box alignment.

Tests that _content truncates content wider than 56 visible chars, and that
_banner renders correctly even with a long directory path.
"""
from __future__ import annotations

import io
import sys
from pathlib import Path

# Add repo root to path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from aria_cli.cli import _visible_len, _content, _BoxChars, _Color, _banner, __version__
from aria_cli.llm import LLMConfig
from aria_cli.safety import WriteGuard


def test_content_truncates_overflow() -> None:
    """_content truncates text wider than 56 visible chars to exactly 56."""
    c = _Color(enabled=False)
    # Build a string that's wider than 56 chars
    long_path = "C:\\" + "x" * 60
    text = c.cyan("ARIA Coder") + "  v" + __version__ + "  " + c.dim(long_path)
    visible = _visible_len(text)
    assert visible > 56, f"Test precondition failed: visible len {visible} <= 56"

    padded = _content(text)
    padded_visible = _visible_len(padded)
    assert padded_visible <= 56, f"_content overflow: {padded_visible} > 56"

    # The padded string should end with "…  " (ellipsis + 2 spaces)
    assert padded.rstrip().endswith("…"), f"_content should end with ellipsis: {repr(padded[-10:])}"


def test_content_pads_short_text() -> None:
    """_content pads text shorter than 56 chars to exactly 56."""
    c = _Color(enabled=False)
    text = c.dim("duration:") + "  5m 23s"
    padded = _content(text)
    assert len(padded) == 56, f"_content should be 56 chars, got {len(padded)}"


def test_banner_does_not_overflow_with_long_path() -> None:
    """_banner renders correctly even with a very long directory path."""
    old_stdout = sys.stdout
    sys.stdout = io.StringIO()
    try:
        c = _Color(enabled=False)
        cfg = LLMConfig()
        guard = WriteGuard(self_mode=True)
        # Use a very long path
        long_cwd = Path("C:\\" + "x" * 80)
        _banner(c, cfg, True, guard, long_cwd, auto_approve=True)
        output = sys.stdout.getvalue()
        lines = output.splitlines()

        # Line 2 should contain the title
        assert "ARIA Coder" in lines[2], f"Banner missing title: {lines[2]}"

        # The box should be well-formed: each content line should have
        # vertical bars at the start and end
        bx = _BoxChars()
        for line in lines[1:]:
            if bx.v in line:
                # Content line — should start with "  " + v and end with v
                assert line.strip().startswith(bx.v), f"Content line missing left border: {line}"
                assert line.strip().endswith(bx.v), f"Content line missing right border: {line}"
    finally:
        sys.stdout = old_stdout


def test_content_with_ansi_codes_truncates_correctly() -> None:
    """_content preserves ANSI codes when truncating."""
    c = _Color(enabled=True)
    # Build a string with ANSI codes that's wider than 56 chars
    long_path = "C:\\" + "x" * 60
    text = c.cyan("ARIA Coder") + "  v" + __version__ + "  " + c.dim(long_path)
    visible = _visible_len(text)
    assert visible > 56, f"Test precondition failed: visible len {visible} <= 56"

    padded = _content(text)
    padded_visible = _visible_len(padded)
    assert padded_visible <= 56, f"_content overflow with ANSI: {padded_visible} > 56"

    # ANSI codes should be preserved (the string should still contain \033)
    assert "\033" in padded, "ANSI codes lost during truncation"
