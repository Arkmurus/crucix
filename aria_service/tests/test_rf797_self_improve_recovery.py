"""R-F797 (2026-05-22): self_improve regex recovery handles unterminated
strings (truncated LLM responses).

Live evidence 2026-05-22 16:00:56 UTC: self_improve repeatedly failed
on aria_service/intel/researcher.py with `Unterminated string
starting at: line 4 column 17 (char 1451)` — the entire diagnosis
was dropped instead of recovered.

Pre-R-F797 the recovery regex required a closing `"` and a value
terminator. When the LLM response was truncated mid-string, neither
existed, so recovery failed and the whole diagnosis was lost.

R-F797 adds a lenient fallback that captures from the opening quote
to either the next "<word>": key or end-of-text, so partial responses
can still be salvaged.
"""
from __future__ import annotations

import re


# Mirror the regex pair from self_improve.py:1383 (strict) and
# self_improve.py R-F797 (lenient fallback). This file tests the
# regex behaviour in isolation so we don't have to spin up the whole
# self-improve pipeline.
_STRICT = re.compile(
    r'"fixed_code"\s*:\s*"((?:[^"\\]|\\.)*)"',
    re.DOTALL,
)
_LENIENT = re.compile(
    r'"fixed_code"\s*:\s*"(.*?)(?="\s*[,}]|"\s*\n\s*"[a-zA-Z_]+"\s*:|$)',
    re.DOTALL,
)


def _recover(text: str) -> str | None:
    """The exact recovery sequence R-F797 ships in self_improve."""
    m = _STRICT.search(text)
    if not m:
        m = _LENIENT.search(text)
    return m.group(1) if m else None


def test_rf797_strict_match_preserved():
    """Well-formed responses still match the strict regex first. No
    regression."""
    text = '{"fixed_code": "def foo():\\n    return 1", "fix_description": "stuff"}'
    got = _recover(text)
    assert got == "def foo():\\n    return 1"


def test_rf797_unterminated_string_recovers():
    """Live failure mode: response ends mid-string with no closing
    quote. Strict regex fails; lenient captures everything from the
    opening quote to end-of-text."""
    text = (
        '{\n'
        '    "diagnosis": "the function leaks file handles",\n'
        '    "fix_description": "use with statement",\n'
        '    "fixed_code": "def read_config(path):\\n    with open(path) as f:\\n        return f.read()'
        # ← truncated, no closing quote, no closing brace
    )
    got = _recover(text)
    assert got is not None, (
        "R-F797 must recover fixed_code from unterminated-string "
        "responses; pre-R-F797 the whole diagnosis was dropped"
    )
    assert "def read_config" in got
    assert "with open" in got


def test_rf797_lenient_stops_at_next_key():
    """If a later key exists in the JSON, the lenient capture stops
    before it so we don't include the next field's content as code."""
    text = (
        '{"fixed_code": "value with unescaped " inner quote",\n'
        '"fix_description": "use better quoting"}'
    )
    got = _recover(text)
    assert got is not None
    # Should NOT include the next key's content
    assert "fix_description" not in got
    assert "use better quoting" not in got


def test_rf797_no_fixed_code_returns_none():
    """No fixed_code key at all → both regexes fail → None."""
    text = '{"diagnosis": "no fix attempted", "fix_description": "skip"}'
    got = _recover(text)
    assert got is None
