"""R-F2093 — ARIA CLI terminal-UI robustness:
  1. _visible_len uses display WIDTH (wcwidth), not len() → boxes stay aligned
     when content contains emoji / CJK / zero-width chars.
  2. _sanitize_output strips terminal control sequences from UNTRUSTED output
     (tool/command output, exceptions) so a malicious file/command can't corrupt
     or spoof the operator's terminal.
"""
from aria_cli.cli import _visible_len, _sanitize_output


# ── _visible_len: true display width ─────────────────────────────────────────
def test_rf2093_visible_len_ascii():
    assert _visible_len("hello") == 5


def test_rf2093_visible_len_strips_ansi():
    assert _visible_len("\033[36mAB\033[0m") == 2


def test_rf2093_visible_len_wide_chars():
    # CJK are 2 columns each → "中文" is 4 columns (len() would say 2)
    assert _visible_len("中文") == 4
    # a width-2 emoji counts as 2 (len() would say 1)
    assert _visible_len("❌") == 2


def test_rf2093_visible_len_zero_width():
    # combining acute is zero-width → "e" + U+0301 is 1 column (len() says 2)
    assert _visible_len("é") == 1


def test_rf2093_visible_len_falls_back_on_control_chars():
    # wcswidth returns -1 on a control char → fall back to len() (never crash)
    assert _visible_len("a\x07b") >= 1


# ── _sanitize_output: untrusted text is safe to print ────────────────────────
def test_rf2093_sanitize_strips_ansi_injection():
    # a command/file that prints fake colored text must not inject ANSI
    assert _sanitize_output("ok\033[31mFAKE ERROR\033[0m") == "okFAKE ERROR"


def test_rf2093_sanitize_carriage_return_cannot_overwrite():
    # \r could overwrite the visible line with attacker text → convert to \n
    assert _sanitize_output("good\rEVIL") == "good\nEVIL"
    assert _sanitize_output("a\r\nb") == "a\nb"


def test_rf2093_sanitize_strips_backspace_and_bell_and_null():
    assert _sanitize_output("a\x08\x08hi") == "ahi"
    assert _sanitize_output("a\x07\x00b") == "ab"


def test_rf2093_sanitize_strips_osc_title_injection():
    # OSC set-window-title (ESC ] 0 ; ... BEL) must not pass through
    assert _sanitize_output("x\033]0;pwned\x07y") == "xy"


def test_rf2093_sanitize_keeps_tab_and_newline():
    assert _sanitize_output("a\tb\nc") == "a\tb\nc"


def test_rf2093_sanitize_empty_and_none_safe():
    assert _sanitize_output("") == ""
    assert _sanitize_output(None) is None


def test_rf2093_sanitized_output_has_no_control_chars_left():
    nasty = "line1\033[2J\033[H\x1b]0;t\x07col\x1b[31m\x08\rmore\x00\x1f"
    out = _sanitize_output(nasty)
    # nothing below 0x20 except \t and \n may remain; no DEL/C1 either
    assert all((c in "\t\n") or (ord(c) >= 0x20 and not (0x7f <= ord(c) <= 0x9f)) for c in out)
