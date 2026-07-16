"""R-F2660 — Phase A gate #2 honest grader (kills the reading participation trophy).

The R-F1744 regional reading loop (``student._study_weak_regional_cells``, runs
~9.6x/day) credited regional MASTERY with a HARDCODED ``correct=True`` whenever it
stored grounded facts for a cell — a participation trophy that measured reading
VOLUME, not comprehension, and inflated the Phase A gate #2 heatmap floor
(CLAUDE.md §1: close gate #2 by grounded improvement, NEVER by crediting the act of
reading). R-F2660 replaces the hardcoded True with the SAME honest recall grade the
tasks.py research bridge already uses (autonomous/tasks.py::_grade_researched_cell):
mastery moves only if the local reasoning stack can actually ANSWER about the cell
and its answer overlaps what was just read.

These drive the REAL crediting path and assert the credit now FOLLOWS the grade.
Verified to FAIL against the pre-R-F2660 tree (hardcoded True credited regardless).
"""
from __future__ import annotations

import types

import pytest

from aria_service.intel import student


def _gulf_fact():
    # detect_regions() maps saudi/uae/gcc → "gulf" (same fixture the existing
    # gate-2 tests use), so _region_grounded confirms the region and _stored>0.
    return types.SimpleNamespace(
        value="Saudi Arabia and UAE GCC defence procurement tender 2026",
        context="Saudi Arabia, UAE and the wider GCC / Gulf defence market awarded a "
                "major procurement contract via EDGE Group under Vision 2030.",
        source_url="https://ex.test/gulf-defence",
    )


def _setup(monkeypatch, grade):
    """Drive the loop with grounded gulf content; the honest grader returns `grade`
    (a bool) or RAISES if `grade` is an Exception instance."""
    cell = {"topic": "technical", "region": "gulf", "score": 0.459}

    async def _hm():
        return {"floor_breach_cells": [cell], "weak_cells": [cell]}
    monkeypatch.setattr(student, "get_regional_heatmap", _hm)

    async def _explore(*a, **k):
        return types.SimpleNamespace(facts=[_gulf_fact()])

    async def _store(*a, **k):
        return {"action": "created"}
    monkeypatch.setattr(student.kb, "store_fact", _store)

    calls: list = []
    async def _spy(topics, regions, correct, weight=1.0):
        calls.append((list(topics), list(regions), correct))
    monkeypatch.setattr(student, "update_regional_mastery", _spy)

    async def _grader(topic, region, research_text):
        if isinstance(grade, Exception):
            raise grade
        # sanity: the loop must hand the grader the cell + the read facts
        assert region == "gulf" and topic == "technical"
        assert "Saudi" in (research_text or ""), "research_text must be the read facts"
        return grade
    monkeypatch.setattr(
        "aria_service.autonomous.tasks._grade_researched_cell", _grader)
    return cell, calls, _explore


@pytest.mark.asyncio
async def test_failed_grade_does_not_credit_true(monkeypatch):
    """THE fix: grounded reading + a FAILED recall grade must credit correct=False,
    never the old hardcoded True. This is the participation trophy, removed."""
    _cell, calls, explore = _setup(monkeypatch, grade=False)
    studied = await student._study_weak_regional_cells(explore=explore, max_cells=1)

    gulf = [(t, r, c) for (t, r, c) in calls if r == ["gulf"]]
    assert gulf, f"grounded cell must reach the crediting path; calls={calls}"
    assert all(c is False for (t, r, c) in gulf), (
        f"a cell ARIA cannot answer must be credited correct=False, not True; got {gulf}")
    # Still reported as studied (coverage rose from storing facts), with the honest
    # grade recorded so the result is auditable.
    assert any(x["region"] == "gulf" and x.get("graded_correct") is False for x in studied), studied


@pytest.mark.asyncio
async def test_passed_grade_credits_true(monkeypatch):
    """A cell ARIA CAN answer (grade passes) is honestly credited correct=True."""
    _cell, calls, explore = _setup(monkeypatch, grade=True)
    await student._study_weak_regional_cells(explore=explore, max_cells=1)

    gulf = [(t, r, c) for (t, r, c) in calls if r == ["gulf"]]
    assert gulf and all(c is True for (t, r, c) in gulf), (
        f"a passing honest grade must credit correct=True; got {gulf}")


@pytest.mark.asyncio
async def test_grader_failure_skips_credit_never_fabricates(monkeypatch):
    """If the grader (or its import) fails, the loop must SKIP the mastery move —
    never fall back to correct=True. An UNMEASURED cell is not credited (the exact
    class of bug this fixes: absence of a grade is not a pass)."""
    _cell, calls, explore = _setup(monkeypatch, grade=RuntimeError("reasoning down"))
    studied = await student._study_weak_regional_cells(explore=explore, max_cells=1)

    gulf = [(t, r, c) for (t, r, c) in calls if r == ["gulf"]]
    assert not gulf, f"a grader failure must NOT credit the cell at all; got {gulf}"
    assert any(x["region"] == "gulf" and x.get("graded_correct") is None for x in studied), studied


if __name__ == "__main__":
    import sys
    sys.exit(pytest.main([__file__, "-v"]))
