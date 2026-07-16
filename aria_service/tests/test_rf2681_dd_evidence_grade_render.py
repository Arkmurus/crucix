"""R-F2681 — the DD narrative report must render the evidence grade + blockers.

The evidence-depth grade (A/B/C/D/INCOMPLETE), its blocking reasons, and the
Grade-A requirements were computed by `_dd_quality_assessment` and exposed only
in the `structured_view()` JSON contract — the human-facing markdown report
(`ARKDDReport.render_markdown`, persisted at dd_orchestrator.py and delivered to
chat/WhatsApp) never surfaced them. So an operator reading the report could not
see "this is a Grade B report because X — to reach A you need Z". This adds a
prominent Evidence Grade block right after the BLUF/recommendation.

Capability test (§3c): drives the REAL `render_markdown` on real ARKDDReport
instances and asserts the grade line + blockers (or the Grade-A "meets all
thresholds" line) appear, plus the concise cap. Fails before the fix (no
"Evidence Grade" text), passes after.
"""
from __future__ import annotations

from unittest.mock import patch

from aria_service.intel.dd_schema import ARKDDReport, _dd_quality_assessment


def _render(report: ARKDDReport, *, concise: bool = False) -> str:
    md = report.render_markdown(concise=concise)
    assert isinstance(md, str) and md, "render_markdown must return non-empty markdown"
    return md


def test_render_surfaces_evidence_grade_and_blockers():
    # A default/thin report grades below A with blocking reasons.
    r = ARKDDReport()
    grade = _dd_quality_assessment(r.as_dict())["grade"]
    assert grade != "A", "a bare report should not grade A (test premise)"

    md = _render(r)
    assert "Evidence Grade:" in md, "grade must appear in the narrative report"
    assert f"Evidence Grade: {grade}" in md, (
        f"rendered grade must match the computed grade {grade!r}"
    )
    # A non-A report must show WHY (the blockers) so the grade is honest.
    assert ("To reach Grade A:" in md) or ("Grade withheld —" in md), (
        "a non-A grade must render its blockers / withheld reason"
    )
    # The grade is evidence-depth, explicitly not the risk colour.
    assert "evidence depth, not risk" in md


def test_render_grade_a_shows_meets_thresholds():
    # Drive the render A-branch by returning a Grade-A assessment. This tests the
    # RENDER (what R-F2681 changed); the grade COMPUTATION for A is covered by
    # test_rf2656 / test_rf2383. render_markdown resolves the module-global
    # _dd_quality_assessment, so patching it here reaches the call inside.
    r = ARKDDReport()
    grade_a = {"grade": "A", "score": 95, "blocking_reasons": [],
               "grade_a_requirements": {}}
    with patch("aria_service.intel.dd_schema._dd_quality_assessment",
               return_value=grade_a):
        md = _render(r)
    assert "Evidence Grade: A" in md
    assert "meets all Grade-A thresholds" in md
    assert "To reach Grade A:" not in md, "an A report has no blockers to list"


def test_concise_caps_blockers():
    r = ARKDDReport()
    blockers = _dd_quality_assessment(r.as_dict())["blocking_reasons"]
    if len(blockers) <= 2:
        # Not enough blockers to exercise the cap on this fixture — nothing to assert.
        return
    md = _render(r, concise=True)
    assert "more (see full report)" in md, "concise render must cap the blocker list"
