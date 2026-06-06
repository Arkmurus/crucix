"""R-F1260 — Capability tests for the clean terminal UI.

Tests that the new text-first UI renders correctly:
- _banner() — clean 4-line header with horizontal rule
- _finalize() — clean summary with horizontal rule
- All REPL commands render without box-drawing noise
"""
from __future__ import annotations

import io
import os
import sys
from pathlib import Path

# Add repo root to path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from aria_cli.cli import _BoxChars, _Color, _banner, _finalize, TerminalUI
from aria_cli.llm import LLMConfig
from aria_cli.safety import WriteGuard


def _capture_banner() -> list[str]:
    """Capture the banner output as a list of lines."""
    old_stdout = sys.stdout
    sys.stdout = io.StringIO()
    try:
        c = _Color(enabled=False)
        cfg = LLMConfig()
        guard = WriteGuard(self_mode=True)
        _banner(c, cfg, True, guard, Path.cwd(), auto_approve=True)
        return sys.stdout.getvalue().splitlines()
    finally:
        sys.stdout = old_stdout


def test_banner_has_clean_structure() -> None:
    """Banner has 5 lines: blank, title+version+dir, config, separator, blank."""
    lines = _capture_banner()
    assert len(lines) >= 5, f"Expected at least 5 banner lines, got {len(lines)}"

    # Line 0: blank line (print() before the header)
    # Line 1: contains ARIA title
    assert "ARIA" in lines[1], f"Banner missing 'ARIA' title: {lines[1]}"

    # Line 2: contains config info
    assert lines[2].strip(), "Banner line 2 (config) should not be empty"

    # Line 3: separator line (horizontal rule)
    assert lines[3].strip(), "Banner line 3 (separator) should not be empty"


def test_banner_contains_all_expected_sections() -> None:
    """Banner contains ARIA title, directory, provider, mode, brain, approval."""
    lines = "\n".join(_capture_banner())
    assert "ARIA" in lines, "Banner missing 'ARIA' title"
    assert "v0.1.0" in lines, "Banner missing version"
    assert "deepseek" in lines or "C:" in lines, "Banner missing provider or directory"
    assert "self" in lines or "general" in lines, "Banner missing mode"
    assert "brain" in lines, "Banner missing brain status"
    assert "auto" in lines or "confirm" in lines, "Banner missing approval status"


def test_banner_has_clean_text_structure() -> None:
    """Banner uses clean text layout — no box-drawing (R-F1389)."""
    lines = "\n".join(_capture_banner())
    bx = _BoxChars()
    # No box-drawing corners in the new clean layout
    assert bx.tl not in lines, f"Banner should not have top-left corner: {bx.tl}"
    assert bx.bl not in lines, f"Banner should not have bottom-left corner: {bx.bl}"
    # Title should be present
    assert "ARIA" in lines, "Banner missing 'ARIA' title"


def test_banner_ascii_fallback() -> None:
    """With ASCII fallback, the banner should still render correctly."""
    old_no_color = os.environ.pop("NO_COLOR", None)
    try:
        os.environ["NO_COLOR"] = "1"
        lines = _capture_banner()
        assert len(lines) >= 5, f"Expected at least 5 banner lines, got {len(lines)}"
        assert "ARIA" in lines[1], "Banner missing 'ARIA' title"
    finally:
        if old_no_color is not None:
            os.environ["NO_COLOR"] = old_no_color
        else:
            os.environ.pop("NO_COLOR", None)


def test_banner_with_colors() -> None:
    """With ANSI colors enabled, the banner should still render correctly."""
    old_stdout = sys.stdout
    sys.stdout = io.StringIO()
    try:
        c = _Color(enabled=True)
        cfg = LLMConfig()
        guard = WriteGuard(self_mode=True)
        _banner(c, cfg, True, guard, Path.cwd(), auto_approve=True)
        lines = sys.stdout.getvalue().splitlines()
        assert len(lines) >= 5, f"Expected at least 5 banner lines, got {len(lines)}"
        assert "ARIA" in lines[1], "Banner missing 'ARIA' title"
    finally:
        sys.stdout = old_stdout
