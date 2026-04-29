"""Regression test for F93 — parse_llm_json handles missing-comma between adjacent objects.

Live evidence 2026-04-29 15:08:55 + 15:09:25 + 15:09:55:
  WARNING aria.researcher | Compliance document analysis failed:
  Expecting ',' delimiter: line 188 column 6 (char 7919)
  ...
  Document read: email_linkedin_alert_The_Floating_Network → 0 facts, 0 hypotheses (91 sec wasted)

DeepSeek occasionally drops the comma between adjacent JSON values
inside the same array. None of the 5 prior repair strategies handle
this — truncation repair (Strategy 4) only closes tail-truncated
JSON, not in-the-middle missing punctuation. New Strategy 1b fills
the gap by walking the string and inserting a comma between any
`}{`, `}[`, `]{`, or `][` that appears outside a string value.
"""
from __future__ import annotations

import json


def test_missing_comma_between_objects_in_array():
    """The exact failure pattern from the live log."""
    from aria_service.intel.llm_json import parse_llm_json
    bad = '''{
      "facts": [
        {"topic": "Angola", "content": "Procurement signed."}
        {"topic": "Mozambique", "content": "Tender open."}
      ]
    }'''
    result = parse_llm_json(bad)
    assert result is not None, (
        "F93 regression: missing-comma JSON not repaired. The whole "
        "compliance-analysis path is back to losing facts."
    )
    assert len(result["facts"]) == 2
    assert result["facts"][0]["topic"] == "Angola"
    assert result["facts"][1]["topic"] == "Mozambique"


def test_missing_comma_between_arrays_in_array():
    from aria_service.intel.llm_json import parse_llm_json
    bad = '{"groups": [[1, 2] [3, 4]]}'
    result = parse_llm_json(bad)
    assert result == {"groups": [[1, 2], [3, 4]]}


def test_missing_comma_object_then_array():
    from aria_service.intel.llm_json import parse_llm_json
    bad = '[{"a": 1} ["b"]]'
    result = parse_llm_json(bad)
    assert result == [{"a": 1}, ["b"]]


def test_missing_comma_array_then_object():
    from aria_service.intel.llm_json import parse_llm_json
    bad = '[["x"] {"y": 2}]'
    result = parse_llm_json(bad)
    assert result == [["x"], {"y": 2}]


def test_string_containing_brackets_not_corrupted():
    """The repair walks character-by-character to skip patterns inside
    string values. A string like `"text with } and { in it"` must not
    be modified."""
    from aria_service.intel.llm_json import parse_llm_json
    good = '{"text": "value with } and { brackets"}'
    result = parse_llm_json(good)
    assert result == {"text": "value with } and { brackets"}


def test_valid_json_still_round_trips():
    """The new strategy must not break valid input. Strategy 0 (as-is)
    catches valid JSON before the comma-insertion ever runs."""
    from aria_service.intel.llm_json import parse_llm_json
    payload = {
        "facts": [
            {"topic": "Pakistan", "content": "Defence pact signed."},
            {"topic": "Libya", "content": "Embargo notice updated."},
        ]
    }
    valid = json.dumps(payload)
    assert parse_llm_json(valid) == payload


def test_missing_comma_at_realistic_offset():
    """Reproduce the live char-7919 failure with a realistically-sized
    compliance response."""
    from aria_service.intel.llm_json import parse_llm_json
    # Build a long valid JSON, then delete one comma between two
    # adjacent objects in the middle of an array.
    facts = [
        {"topic": f"Topic {i}", "content": "x" * 200, "confidence": "ASSESSED"}
        for i in range(40)
    ]
    payload = {"compliance_analysis": True, "facts": facts}
    serialised = json.dumps(payload, indent=2)
    # Strip the comma between objects 19 and 20 — well into the body.
    target = "}\n  {"  # adjacent objects in pretty-printed JSON
    first_idx = serialised.find(target)
    # Find the 19th occurrence
    for _ in range(19):
        first_idx = serialised.find(target, first_idx + 1)
    # Replace `},\n  {` with `}\n  {` (drop the comma)
    broken = serialised[:first_idx - 1] + serialised[first_idx:]
    # Sanity: the broken form must NOT parse with plain json.loads
    try:
        json.loads(broken)
        assert False, "broken JSON unexpectedly parses without repair"
    except json.JSONDecodeError:
        pass
    # parse_llm_json must repair it
    repaired = parse_llm_json(broken)
    assert repaired is not None
    assert len(repaired["facts"]) == 40
