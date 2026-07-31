"""R-F3549 — the evidence grade GATES CLEARANCE and was never defined for the reader.

THE DEFECT, from the operator's review of four delivered reports. Each printed a line
like "Reliance threshold: Evidence Grade C (not met)" and nothing anywhere said what C
means. That grade is load-bearing: R-F3173 refuses reliance below Grade A, so the report
tells a customer their decision cannot be relied upon **on a scale they have no way to
check**. They cannot tell whether C is "nearly there" or "almost nothing", nor what would
move it.

A gate whose scale is undisclosed is unfalsifiable to the person it constrains. That is
the same class as an unlabelled GREEN badge (R-F3544) and an unexplained coverage figure:
the report states a conclusion without the means to audit it.

WHY THE THRESHOLDS ARE NAMED ONCE. A printed definition that RESTATES the numbers in prose
drifts the first time a threshold moves, and then the report explains a scale it is no
longer using — a fresh misrepresentation introduced by the fix for one. `_quality_grade`
and `evidence_grade_explained` read the SAME `_GRADE_MIN_SCORE` constants.

THIS CHANGES NO GRADE. It is a disclosure, not a re-scoring — asserted below at every
boundary, because a "definition" change that silently moved a threshold would be the worst
possible version of this fix.
"""
from __future__ import annotations

import pytest

from aria_service.intel.dd_schema import (
    _GRADE_MEANING,
    _GRADE_MIN_SCORE,
    ARKDDReport,
    _quality_grade,
    evidence_grade_explained,
)


# ── the grading itself must be UNCHANGED ────────────────────────────────────

@pytest.mark.parametrize("score,expected", [
    (100, "A"), (90, "A"), (85, "A"),          # A boundary
    (84, "B"), (70, "B"),                       # B boundary
    (69, "C"), (50, "C"),                       # C boundary
    (49, "D"), (0, "D"),                        # D
])
def test_grading_behaviour_is_untouched(score, expected):
    """A disclosure must not re-score anything."""
    assert _quality_grade(score, []) == expected


def test_grade_A_still_requires_no_blockers():
    assert _quality_grade(95, []) == "A"
    assert _quality_grade(95, ["a blocker"]) == "B", (
        "the no-blockers condition on Grade A was lost")


# ── the disclosure ──────────────────────────────────────────────────────────

@pytest.mark.parametrize("grade", ["A", "B", "C", "D", "INCOMPLETE"])
def test_every_grade_has_a_reader_facing_meaning(grade):
    text = evidence_grade_explained(grade)
    assert f"Grade {grade}" in text
    assert _GRADE_MEANING[grade] in text
    assert "unrecognised" not in text


def test_the_explanation_states_the_reliance_rule():
    """The reader must be told WHY the grade matters, not just what it is."""
    assert "Grade A is required to rely on this report" in evidence_grade_explained("C")


def test_the_scale_is_DERIVED_not_restated(monkeypatch):
    """THE DRIFT GUARD. If a threshold moves, the printed definition must move with it —
    otherwise the report explains a scale the grader has stopped using."""
    monkeypatch.setitem(_GRADE_MIN_SCORE, "C", 55)
    assert "C ≥55" in evidence_grade_explained("C"), (
        "the printed scale is hardcoded prose and has drifted from the grader")


def test_an_unknown_grade_does_not_invent_a_meaning():
    assert "unrecognised grade" in evidence_grade_explained("Z")


def test_a_missing_grade_reads_as_not_graded_not_as_a_low_grade():
    """None must not silently render as D — 'not measured' is not 'measured badly'."""
    for empty in (None, "", "   "):
        assert "INCOMPLETE" in evidence_grade_explained(empty)


# ── it reaches the reader ───────────────────────────────────────────────────

def test_capability_the_definition_appears_on_the_rendered_report():
    r = ARKDDReport()
    r.identity.entity_name = "Test Ltd"
    md = r.render_markdown()
    assert "Scale:" in md, "the grade definition is not on the document face"
    assert "Grade A is required to rely on this report" in md


def test_it_sits_with_the_reliance_line():
    r = ARKDDReport()
    r.identity.entity_name = "Test Ltd"
    lines = r.render_markdown().splitlines()
    rel = next(i for i, l in enumerate(lines) if "Reliance threshold:" in l)
    defn = next(i for i, l in enumerate(lines) if "Scale:" in l)
    assert defn - rel == 1, "the definition is not adjacent to the grade it explains"
