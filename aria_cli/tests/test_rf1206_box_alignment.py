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
    """Banner has 5 lines: blank, top-rule, title+version+dir, config, bottom-rule."""
    lines = _capture_banner()
    assert len(lines) >= 5, f"Expected at least 5 banner lines, got {len(lines)}"

    # Line 0: blank line (print() before the box)
    # Line 1: top rule (starts with spaces + box-drawing corner)
    assert lines[1].strip(), "Banner line 1 (top rule) should not be empty"

    # Line 2: contains ARIA Coder title
    assert "ARIA Coder" in lines[2], f"Banner missing 'ARIA Coder' title: {lines[2]}"

    # Line 4: bottom rule (starts with spaces + box-drawing corner)
    assert lines[4].strip(), "Banner line 4 (bottom rule) should not be empty"


def test_banner_contains_all_expected_sections() -> None:
    """Banner contains ARIA Coder title, directory, provider, mode, brain, approval."""
    lines = "\n".join(_capture_banner())
    assert "ARIA Coder" in lines, "Banner missing 'ARIA Coder' title"
    assert "v0.1.0" in lines, "Banner missing version"
    assert "deepseek" in lines or "C:" in lines, "Banner missing provider or directory"
    assert "self" in lines or "general" in lines, "Banner missing mode"
    assert "brain" in lines, "Banner missing brain status"
    assert "auto" in lines or "confirm" in lines, "Banner missing approval status"


def test_banner_has_box_structure() -> None:
    """Banner uses box-drawing characters for alignment (R-F1263)."""
    lines = "\n".join(_capture_banner())
    bx = _BoxChars()
    # Top rule should start with a corner character
    assert bx.tl in lines, f"Banner missing top-left corner: {bx.tl}"
    # Bottom rule should start with a corner character
    assert bx.bl in lines, f"Banner missing bottom-left corner: {bx.bl}"
    # Content lines should have vertical bars for alignment
    assert bx.v in lines, f"Banner missing vertical bars: {bx.v}"
    # Title should be present
    assert "ARIA Coder" in lines, "Banner missing 'ARIA Coder' title"


def test_banner_ascii_fallback() -> None:
    """With ASCII fallback, the banner should still render correctly."""
    old_no_color = os.environ.pop("NO_COLOR", None)
    try:
        os.environ["NO_COLOR"] = "1"
        lines = _capture_banner()
        assert len(lines) >= 5, f"Expected at least 5 banner lines, got {len(lines)}"
        assert "ARIA Coder" in lines[2], "Banner missing 'ARIA Coder' title"
        # Should use ASCII +-| instead of Unicode box-drawing
        assert "+" in lines[1], "ASCII fallback should use + for corners"
        assert "|" in lines[2], "ASCII fallback should use | for vertical bars"
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
        assert "ARIA Coder" in lines[2], "Banner missing 'ARIA Coder' title"
    finally:
        sys.stdout = old_stdout
