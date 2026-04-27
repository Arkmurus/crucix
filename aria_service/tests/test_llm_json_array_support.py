"""Regression guard for parse_llm_json top-level array support.

Migration 2026-04-27: active_challenge_engine and other LLM sites parse
JSON arrays at the top level. The previous {-only regex in
_extract_json_object silently dropped array responses into the default,
producing zero challenges from valid LLM output.

Fix: _extract_json_object now matches whichever shape ({...} or [...])
appears first.
"""
from __future__ import annotations

from aria_service.intel.llm_json import parse_llm_json


def test_top_level_array_parses():
    text = '[{"type":"DEVIL_ADVOCATE","text":"x"}, {"type":"ASSUMPTION","text":"y"}]'
    result = parse_llm_json(text)
    assert isinstance(result, list)
    assert len(result) == 2
    assert result[0]["type"] == "DEVIL_ADVOCATE"


def test_top_level_array_with_fences():
    text = "```json\n[1, 2, 3]\n```"
    assert parse_llm_json(text) == [1, 2, 3]


def test_top_level_array_with_trailing_comma():
    """Strategy 1 cleans trailing commas — must apply to arrays too."""
    text = "[1, 2, 3,]"
    assert parse_llm_json(text) == [1, 2, 3]


def test_top_level_array_with_prose_before():
    text = "Here are the items: [1, 2, 3]\nSome trailing prose."
    assert parse_llm_json(text) == [1, 2, 3]


def test_object_still_wins_when_first():
    """Whichever opener appears first wins. Object before array."""
    text = '{"a": 1} (we also computed [4,5,6] separately)'
    result = parse_llm_json(text)
    assert isinstance(result, dict)
    assert result == {"a": 1}


def test_array_wins_when_first():
    """Array before object."""
    text = '[1, 2, 3] then later {"x": 99}'
    result = parse_llm_json(text)
    assert isinstance(result, list)
    assert result == [1, 2, 3]


def test_empty_array_parses():
    assert parse_llm_json("[]") == []


def test_array_default_returned_on_garbage():
    """Active challenge engine passes default=[] — must round-trip."""
    assert parse_llm_json("totally not json", default=[]) == []


def test_array_repair_with_unquoted_keys_inside():
    """Strategy 2 (quote unquoted keys) must apply to objects nested in arrays."""
    text = '[{name: "alice"}, {name: "bob"}]'
    result = parse_llm_json(text)
    assert isinstance(result, list)
    assert result[0]["name"] == "alice"
