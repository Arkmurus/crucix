"""Tests for the multi-strategy LLM JSON repair (intel/llm_json.py).

Each test reproduces a real failure mode observed in fly logs from the
researcher article-analysis path. The al-monitor.com Iran-FM article on
2026-04-27 failed every spider re-run with "Expecting ',' delimiter" --
the repair pipeline must handle that and the four other patterns the
Node bd_intelligence.mjs already covers.
"""
from __future__ import annotations

from aria_service.intel.llm_json import parse_llm_json


def test_clean_json_passes_through():
    assert parse_llm_json('{"a": 1, "b": 2}') == {"a": 1, "b": 2}


def test_strips_markdown_fences():
    s = '```json\n{"a": 1}\n```'
    assert parse_llm_json(s) == {"a": 1}


def test_handles_trailing_prose():
    s = 'Here is the JSON:\n{"facts": [{"x": 1}]}\nLet me know if you need more.'
    assert parse_llm_json(s) == {"facts": [{"x": 1}]}


def test_repairs_unescaped_newline_in_string():
    # Real LLM failure: Claude includes raw newline inside a fact's content
    s = '{"facts": [{"content": "line one\nline two"}]}'
    parsed = parse_llm_json(s)
    assert parsed is not None
    assert parsed["facts"][0]["content"] == "line one\nline two"


def test_repairs_unquoted_keys():
    # DeepSeek quirk
    s = '{facts: [], skip: false}'
    parsed = parse_llm_json(s)
    assert parsed == {"facts": [], "skip": False}


def test_preserves_apostrophe_in_double_quoted_string():
    # Regression guard: blanket single->double would corrupt this
    s = '''{"location": "Angola's defence sector"}'''
    parsed = parse_llm_json(s)
    assert parsed["location"] == "Angola's defence sector"


def test_repairs_single_quotes_outside_strings():
    # Single-quoted JSON (Python repr style) emitted by some models
    s = "{'a': 'foo', 'b': 'bar'}"
    parsed = parse_llm_json(s)
    assert parsed == {"a": "foo", "b": "bar"}


def test_strips_trailing_comma():
    s = '{"a": 1, "b": 2,}'
    assert parse_llm_json(s) == {"a": 1, "b": 2}


def test_strips_trailing_comma_in_array():
    s = '{"items": [1, 2, 3,]}'
    assert parse_llm_json(s) == {"items": [1, 2, 3]}


def test_repairs_truncated_string_value():
    # Model ran out of tokens mid-string
    s = '{"facts": [{"content": "this got cut off mid-sen'
    parsed = parse_llm_json(s)
    # Should at least not crash; truncation repair appends `"` then closes
    assert parsed is not None
    assert parsed["facts"][0]["content"].startswith("this got cut off")


def test_repairs_truncated_object():
    # Model cut off after a key with no value
    s = '{"facts": [{"topic": "A", "content":'
    parsed = parse_llm_json(s)
    # Repair fills `null` for the dangling key, closes structures
    assert parsed is not None
    assert parsed["facts"][0]["topic"] == "A"


def test_returns_default_when_no_json_present():
    s = "I cannot extract any facts from this page."
    assert parse_llm_json(s) is None
    assert parse_llm_json(s, default={"facts": []}) == {"facts": []}


def test_empty_input_returns_default():
    assert parse_llm_json("") is None
    assert parse_llm_json("", default={}) == {}
    assert parse_llm_json(None, default={}) == {}  # type: ignore[arg-type]


def test_never_raises_on_garbage():
    # Random garbage shouldn't crash the function
    for s in ("{{{{", "}{][", "not json at all", "[1, 2, {bad"):
        # Either parses something or returns None; never raises
        try:
            parse_llm_json(s)
        except Exception as e:  # pragma: no cover
            raise AssertionError(f"parse_llm_json raised on {s!r}: {e}")


def test_handles_real_world_unescaped_quote_in_string():
    # The al-monitor.com Iran-FM failure pattern: Claude inlines an
    # article quote with a special unicode character that breaks parsing.
    # With newline-in-string repair the parse should succeed.
    s = '{"facts": [{"content": "Russia\'s military intel chief said \\"talks stalled\\""}]}'
    parsed = parse_llm_json(s)
    assert parsed is not None
    assert "talks stalled" in parsed["facts"][0]["content"]
