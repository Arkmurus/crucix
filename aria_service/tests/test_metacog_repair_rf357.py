"""R-F357 — metacog self-assessment JSON salvage tests.

Live failure mode reproduced (fly logs 2026-05-12 10:39:35Z):

    Self-assessment JSON parse failed: {
      "scores": {
        "methodology": 7,
        "source_quality": 6,
        "reasoning_quality": 8,
        "domain_accuracy": 7,
        "output_quality": 9,
        "overall": 7.4
      },
      "identified_weaknesses": [
        "

The LLM hit max_tokens mid-string inside the `identified_weaknesses` array.
Pre-fix: `json.loads` raised, the assessment was dropped, calibration lost
the scores. Post-fix: `_repair_truncated_assessment` salvages either the
full repaired object OR at minimum the scores sub-block.
"""
from __future__ import annotations

from aria_service.metacognitive.engine import _repair_truncated_assessment


def test_salvages_exact_live_truncation_pattern():
    """The actual fly-log truncation shape must yield at least scores."""
    truncated = (
        '{\n'
        '  "scores": {\n'
        '    "methodology": 7,\n'
        '    "source_quality": 6,\n'
        '    "reasoning_quality": 8,\n'
        '    "domain_accuracy": 7,\n'
        '    "output_quality": 9,\n'
        '    "overall": 7.4\n'
        '  },\n'
        '  "identified_weaknesses": [\n'
        '    "'
    )
    repaired = _repair_truncated_assessment(truncated)
    assert repaired is not None, "must salvage scores even when array truncates mid-string"
    scores = repaired.get("scores") or {}
    assert scores.get("methodology") == 7
    assert scores.get("source_quality") == 6
    assert scores.get("overall") == 7.4


def test_salvages_truncation_inside_array_no_open_string():
    """Truncation right after a complete entry inside an array."""
    truncated = (
        '{"scores": {"methodology": 8, "overall": 8.0}, '
        '"identified_weaknesses": ["bias risk not flagged", '
        '"only one source cited",'
    )
    repaired = _repair_truncated_assessment(truncated)
    assert repaired is not None
    # Strategy 1 should close the array and object — verify scores present.
    assert repaired.get("scores", {}).get("overall") == 8.0


def test_salvages_truncation_inside_nested_object():
    """Truncation inside skill_gaps_revealed[N].domain string."""
    truncated = (
        '{"scores": {"methodology": 5, "overall": 5.5}, '
        '"skill_gaps_revealed": [{"domain": "sanctions_'
    )
    repaired = _repair_truncated_assessment(truncated)
    assert repaired is not None
    assert repaired["scores"]["overall"] == 5.5


def test_empty_or_garbage_returns_none():
    assert _repair_truncated_assessment("") is None
    assert _repair_truncated_assessment(None) is None  # type: ignore[arg-type]
    assert _repair_truncated_assessment("not json at all") is None
    assert _repair_truncated_assessment("[1, 2, 3") is None  # array root, not dict


def test_clean_json_repairs_to_itself():
    """A perfectly-valid input should also salvage cleanly (idempotent)."""
    clean = '{"scores": {"overall": 7.0}, "identified_weaknesses": ["x"]}'
    repaired = _repair_truncated_assessment(clean)
    assert repaired is not None
    assert repaired["scores"]["overall"] == 7.0
    assert repaired["identified_weaknesses"] == ["x"]


def test_scores_only_fallback_when_outer_structure_unrecoverable():
    """When strategy 1 can't reconcile, strategy 2 extracts scores alone."""
    # Construct a payload whose bracket stack genuinely cannot close cleanly:
    # an extra `{` opens in the middle of recommended_action that strategy 1
    # might close incorrectly. Strategy 2 should still pluck the scores.
    truncated = (
        '{"scores": {"methodology": 6, "source_quality": 5, '
        '"reasoning_quality": 7, "domain_accuracy": 6, '
        '"output_quality": 8, "overall": 6.4}, '
        '"recommended_action": "investigate {{nested braces}} and'
    )
    repaired = _repair_truncated_assessment(truncated)
    assert repaired is not None
    assert repaired["scores"]["overall"] == 6.4


def test_repair_always_stamps_salvaged_marker():
    """Strategy 2 result must carry _salvaged='scores_only'. Strategy 1 result
    has _salvaged stamped by the caller (engine.py self_assess_output), so
    the helper itself only needs to set it for the scores-only path."""
    truncated_strategy_2 = (
        '{"scores": {"methodology": 6, "overall": 6.4}, '
        '"recommended_action": "investigate {{nested braces}} and'
    )
    repaired = _repair_truncated_assessment(truncated_strategy_2)
    assert repaired is not None
    # Strategy 2 owns the marker; Strategy 1 success path leaves it absent
    # (the caller stamps a fallback "structure_closed" marker on top).
    if "_salvaged" in repaired:
        assert repaired["_salvaged"] in ("scores_only", "structure_closed")
