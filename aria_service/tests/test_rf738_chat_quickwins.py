"""R-F738 (2026-05-20) — chat quick-wins regression tests.

These tests confirm the frontend additions are present in
`public/aria.html` and that the markdown-table regex shape is sound.
A real browser-render test is out of scope (no JSDOM in the Python
suite), so these are static-presence + regex-validity checks.

Frontend additions covered:
  1. Pipe-table support in md() renderer
  2. Fenced code-block (```) support in md() renderer
  3. .md-table CSS styling
  4. Clipboard image paste handler on textarea
  5. Ctrl/Cmd+K → new chat keyboard shortcut
  6. Ctrl/Cmd+/ → focus input + prefill /screen
  7. Input hint updated to mention the new shortcuts

Catches accidental revert / rebase loss of any of the additions.
"""
from __future__ import annotations

import re
from pathlib import Path

ARIA_HTML = Path(__file__).resolve().parents[2] / "public" / "aria.html"


def _read_html() -> str:
    return ARIA_HTML.read_text(encoding="utf-8")


def test_aria_html_exists():
    assert ARIA_HTML.exists(), f"aria.html missing at {ARIA_HTML}"


def test_md_table_regex_present():
    """R-F738: the pipe-table regex must be in md()."""
    src = _read_html()
    assert re.search(r"md-table", src), "R-F738: .md-table reference missing"
    # The detector for pipe tables: matches a header row + a separator
    # row with dashes. Search for the regex literal characters.
    assert "[:\\-\\s|]" in src or "[:\\-\\s\\|]" in src or ":\\-\\s|" in src, (
        "R-F738: md() pipe-table detector regex missing"
    )


def test_md_code_fence_regex_present():
    """R-F738: triple-backtick code-fence handling in md()."""
    src = _read_html()
    assert "md-code" in src
    assert "```" in src or "0-9_+-" in src or "lang" in src


def test_md_table_css_present():
    """R-F738: .md-table styling so the rendered table is readable."""
    src = _read_html()
    assert ".md-table" in src
    assert "border-collapse" in src


def test_md_code_css_present():
    """R-F738: .md-code styling for fenced code blocks."""
    src = _read_html()
    assert ".md-code" in src
    assert "JetBrains Mono" in src or "monospace" in src


def test_clipboard_paste_handler_present():
    """R-F738: textarea has a paste event listener that handles images."""
    src = _read_html()
    assert "addEventListener('paste'" in src or 'addEventListener("paste"' in src
    assert "clipboardData" in src
    assert "DataTransfer" in src
    assert "image/" in src
    # And the R-F738 marker is in the paste block
    paste_idx = src.find("addEventListener('paste'")
    if paste_idx < 0:
        paste_idx = src.find('addEventListener("paste"')
    surrounding = src[max(0, paste_idx - 500):paste_idx + 1500]
    assert "R-F738" in surrounding, (
        "R-F738: paste handler block missing the R-F738 marker"
    )


def test_keyboard_shortcuts_present():
    """R-F738: Ctrl+K (new) and Ctrl+/ (screen) shortcuts."""
    src = _read_html()
    # Ctrl+K → new conversation
    assert "startNewConversation" in src
    # Ctrl+/ should prefill /screen
    assert "/screen" in src
    # Both should be inside a keydown handler that checks ctrlKey/metaKey
    assert "ctrlKey" in src and "metaKey" in src

    # The shortcuts handler should be tagged with R-F738
    # Locate the marker and check it's near a keydown handler
    rf738_locations = [m.start() for m in re.finditer(r"R-F738", src)]
    assert any(
        "keyboard shortcuts" in src[loc:loc + 200].lower()
        for loc in rf738_locations
    ), "R-F738: keyboard shortcuts block missing the explanatory marker"


def test_input_hint_mentions_shortcuts():
    """R-F738: the input hint surface mentions the new shortcuts so
    users discover them."""
    src = _read_html()
    # Find the input-hint span
    m = re.search(r'input-hint["\']?\s*>([^<]+)<', src)
    assert m is not None, "R-F738: input-hint span missing"
    hint = m.group(1)
    assert "Ctrl+K" in hint
    assert "Ctrl+/" in hint
    assert "paste" in hint.lower()


def test_no_regression_in_existing_handlers():
    """R-F738 must NOT remove the existing Enter-to-send handler."""
    src = _read_html()
    assert "e.key === 'Enter'" in src
    assert "!e.shiftKey" in src
    # Enter handler still present
    assert "send(ta.value.trim())" in src


def test_rf738_markers_on_each_addition():
    """R-F738: every addition is tagged with the marker so accidental
    revert is detectable. At least 4 occurrences:
      - md() table regex comment
      - md() code-fence comment
      - paste handler comment
      - keyboard shortcuts comment
      - .md-table CSS
      - .md-code CSS
    """
    src = _read_html()
    count = src.count("R-F738")
    assert count >= 4, (
        f"R-F738: expected ≥4 markers for the four addition zones, "
        f"found {count}. Suggests partial revert."
    )
