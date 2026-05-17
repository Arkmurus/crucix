"""R-F649 — parse_llm_json strategy 1c covers value-value missing commas.

Live evidence 2026-05-17 07:23:17 UTC:
  [llm_json] R-F472 all 5 repair strategies failed (source=researcher,
  total_fails=6): Expecting ',' delimiter: line 1 column 4736
  Preview: {"facts": [{"topic": "Study subject and organisation", "content":
            "Academic study assesses the influence of logistics management
            practices on supply chain performance in Rwanda's ...

The pre-R-F649 strategy 1b (_insert_missing_object_commas) only handles
`}{`, `}[`, `]{`, `][`. Researcher payloads with missing commas between
adjacent STRING values, or string-then-number, etc., all failed through
to the nuclear-clean strategy which cannot insert structural commas.

R-F649 adds strategy 1c (_insert_missing_value_commas) covering all the
value-value adjacencies an LLM is likely to drop.
"""
from __future__ import annotations

import json


def test_rf649_missing_comma_string_string_in_array():
    """Two quoted strings adjacent inside an array — the most common
    LLM missing-comma pattern after a soft-wrap."""
    from aria_service.intel.llm_json import parse_llm_json
    bad = '{"tags": ["sanctions" "embargo" "OFAC"]}'
    result = parse_llm_json(bad)
    assert result == {"tags": ["sanctions", "embargo", "OFAC"]}


def test_rf649_missing_comma_string_number():
    from aria_service.intel.llm_json import parse_llm_json
    bad = '{"items": ["weight" 42 "count" 7]}'
    result = parse_llm_json(bad)
    assert result == {"items": ["weight", 42, "count", 7]}


def test_rf649_missing_comma_number_string():
    from aria_service.intel.llm_json import parse_llm_json
    bad = '{"items": [1 "one" 2 "two"]}'
    result = parse_llm_json(bad)
    assert result == {"items": [1, "one", 2, "two"]}


def test_rf649_missing_comma_string_object():
    from aria_service.intel.llm_json import parse_llm_json
    bad = '{"items": ["header" {"key": "value"}]}'
    result = parse_llm_json(bad)
    assert result == {"items": ["header", {"key": "value"}]}


def test_rf649_missing_comma_string_array():
    from aria_service.intel.llm_json import parse_llm_json
    bad = '{"items": ["header" ["nested"]]}'
    result = parse_llm_json(bad)
    assert result == {"items": ["header", ["nested"]]}


def test_rf649_missing_comma_object_string():
    from aria_service.intel.llm_json import parse_llm_json
    bad = '{"items": [{"a": 1} "after-object"]}'
    result = parse_llm_json(bad)
    assert result == {"items": [{"a": 1}, "after-object"]}


def test_rf649_missing_comma_bool_string():
    from aria_service.intel.llm_json import parse_llm_json
    bad = '{"items": [true "after-true"]}'
    result = parse_llm_json(bad)
    assert result == {"items": [True, "after-true"]}


def test_rf649_missing_comma_null_string():
    from aria_service.intel.llm_json import parse_llm_json
    bad = '{"items": [null "after-null"]}'
    result = parse_llm_json(bad)
    assert result == {"items": [None, "after-null"]}


def test_rf649_string_with_internal_punctuation_not_corrupted():
    """The strategy walks character-by-character to skip patterns inside
    string values. A string like `"value, with internal: punctuation"`
    must not be modified."""
    from aria_service.intel.llm_json import parse_llm_json
    good = '{"text": "value, with : and { } in it"}'
    result = parse_llm_json(good)
    assert result == {"text": "value, with : and { } in it"}


def test_rf649_string_with_apostrophe_not_corrupted():
    """The Rwanda failure preview shows apostrophes inside strings.
    Those are valid JSON content (a single quote, not a delimiter) and
    must survive the strategy unchanged."""
    from aria_service.intel.llm_json import parse_llm_json
    good = (
        '{"content": "Academic study assesses Rwanda\'s supply chain '
        'performance — Rwanda\'s logistics has improved."}'
    )
    result = parse_llm_json(good)
    assert result is not None
    assert "Rwanda's" in result["content"]


def test_rf649_valid_json_still_round_trips():
    """Strategy 1c must never break VALID JSON. Strategy 0 (as-is)
    catches valid input before strategy 1c ever runs, but if it ever
    DOES run on valid input it must be idempotent."""
    from aria_service.intel.llm_json import parse_llm_json
    payload = {
        "facts": [
            {"topic": "X", "content": "Y" * 100},
            {"topic": "A", "content": "B" * 100},
        ],
        "tags": ["sanctions", "embargo"],
        "score": 0.85,
        "active": True,
    }
    valid = json.dumps(payload)
    assert parse_llm_json(valid) == payload


def test_rf649_realistic_researcher_payload_shape():
    """The Rwanda payload had a long string content. Reproduce that shape
    with missing commas at a realistic offset and verify repair works."""
    from aria_service.intel.llm_json import parse_llm_json
    long_content = (
        "Academic study assesses the influence of logistics management "
        "practices on supply chain performance in Rwanda's manufacturing "
        "sector. The study evaluated key dimensions including transport "
        "management, warehousing, demand forecasting, and inventory control."
    ) * 3
    # Build a payload where two facts are adjacent without a comma.
    bad = (
        '{"facts": ['
        f'{{"topic": "Study subject", "content": "{long_content}"}}'
        " "  # missing comma
        f'{{"topic": "Methodology", "content": "{long_content}"}}'
        ']}'
    )
    # Sanity: this must NOT parse with plain json.loads
    try:
        json.loads(bad)
        assert False, "broken payload unexpectedly parsed without repair"
    except json.JSONDecodeError:
        pass
    repaired = parse_llm_json(bad, source="researcher")
    assert repaired is not None, (
        "R-F649 regression: realistic researcher payload still failing"
    )
    assert len(repaired["facts"]) == 2
    assert repaired["facts"][0]["topic"] == "Study subject"
    assert repaired["facts"][1]["topic"] == "Methodology"
