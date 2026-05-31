"""R-F1196: Test _BoxChars Unicode/ASCII fallback for Windows console."""
import os
import sys
from pathlib import Path

# Add repo root to path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from aria_cli.cli import _BoxChars, _Color, _banner
from aria_cli.llm import LLMConfig
from aria_cli.safety import WriteGuard


def test_boxchars_unicode_on_utf8_terminal():
    """_BoxChars should use Unicode when stdout encoding is UTF-8."""
    old_enc = getattr(sys.stdout, "encoding", "")
    old_no_color = os.environ.pop("NO_COLOR", None)
    try:
        # Simulate UTF-8 terminal
        sys.stdout.reconfigure(encoding="utf-8")
        bx = _BoxChars()
        assert bx._unicode is True, f"Expected unicode=True, got {bx._unicode}"
        assert bx.tl == "╔"
        assert bx.tr == "╗"
        assert bx.bl == "╚"
        assert bx.br == "╝"
        assert bx.h == "═"
        assert bx.v == "║"
        assert bx.tm == "╠"
        assert bx.check == "✓"
        assert bx.cross == "✗"
    finally:
        if old_no_color is not None:
            os.environ["NO_COLOR"] = old_no_color
        try:
            sys.stdout.reconfigure(encoding=old_enc)
        except Exception:
            pass


def test_boxchars_ascii_on_cp1252_terminal():
    """_BoxChars should use ASCII when stdout encoding is cp1252."""
    old_enc = getattr(sys.stdout, "encoding", "")
    old_no_color = os.environ.pop("NO_COLOR", None)
    try:
        # Simulate cp1252 terminal (Windows cmd.exe default)
        sys.stdout.reconfigure(encoding="cp1252")
        bx = _BoxChars()
        assert bx._unicode is False, f"Expected unicode=False, got {bx._unicode}"
        assert bx.tl == "+"
        assert bx.tr == "+"
        assert bx.bl == "+"
        assert bx.br == "+"
        assert bx.h == "-"
        assert bx.v == "|"
        assert bx.tm == "+"
        assert bx.check == "v"
        assert bx.cross == "x"
    finally:
        if old_no_color is not None:
            os.environ["NO_COLOR"] = old_no_color
        try:
            sys.stdout.reconfigure(encoding=old_enc)
        except Exception:
            pass


def test_boxchars_ascii_when_no_color_set():
    """_BoxChars should use ASCII when NO_COLOR is set (conservative fallback)."""
    old_enc = getattr(sys.stdout, "encoding", "")
    old_no_color = os.environ.get("NO_COLOR")
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        os.environ["NO_COLOR"] = "1"
        bx = _BoxChars()
        assert bx._unicode is False, "Expected unicode=False when NO_COLOR is set"
        assert bx.tl == "+"
    finally:
        if old_no_color:
            os.environ["NO_COLOR"] = old_no_color
        else:
            os.environ.pop("NO_COLOR", None)
        try:
            sys.stdout.reconfigure(encoding=old_enc)
        except Exception:
            pass


def test_banner_does_not_crash_on_cp1252():
    """_banner should not crash when stdout encoding is cp1252."""
    old_enc = getattr(sys.stdout, "encoding", "")
    old_no_color = os.environ.pop("NO_COLOR", None)
    try:
        sys.stdout.reconfigure(encoding="cp1252")
        c = _Color(enabled=False)
        cfg = LLMConfig()
        guard = WriteGuard(self_mode=True)
        # Should not raise UnicodeEncodeError
        _banner(c, cfg, True, guard, Path.cwd(), auto_approve=True)
    finally:
        if old_no_color is not None:
            os.environ["NO_COLOR"] = old_no_color
        try:
            sys.stdout.reconfigure(encoding=old_enc)
        except Exception:
            pass
