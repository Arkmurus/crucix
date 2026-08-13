"""R-F3971 / C-60 — the learning grader was mathematically incapable of passing.

Phase A gate #2's heatmap floor collapsed to 0.055. That LOOKS like the honest
re-grading CLAUDE.md §1 predicts after R-F2660 replaced the reading trophy. It is
not: the grader cannot return True for a correct answer.

    autonomous/tasks.py:2414
        return student._quick_similarity(resp, research_text) >= 0.4

    intel/student.py:1172
        return inter / union          # Jaccard

`resp` is a short answer; `research_text` is up to 4,000 characters of research.
Jaccard divides by the UNION, so a perfect answer's ceiling is its own length
over the document's. Measured against a real 4,000-char sample of 308 unique
tokens:

    answer  40 tokens, ALL correct -> 0.130   pass=False
    answer  80 tokens, ALL correct -> 0.260   pass=False
    answer 120 tokens, ALL correct -> 0.390   pass=False
    tokens required to pass:          124

This is the same asymmetry as C-52 in the sanctions matcher, one axis over:
**Jaccard is symmetric and the relationship is not.** The grader's own docstring
states the question it means to ask — *"its answer overlaps the research
findings"* — which is CONTAINMENT of the answer in the document, not set
similarity between them.

Each false negative then feeds an EWMA, so the cell decays toward zero without
ever having been wrong.

**What is deliberately NOT changed here:** the regional EWMA has no 0.50 floor
while the topic axis does. Adding one would raise gate #2's number without
measuring anything better, and CLAUDE.md §1 names that family explicitly —
"do not close this by... Each closes the gate by measuring less". Fixing the
grader makes the measurement honest; the floor would only make it flattering.

`_quick_similarity` itself is left alone. Its two other callers
(`student.py:1061`, `:2148`) compare a local response against a cloud response —
roughly equal lengths, where Jaccard is the right measure. Only this grader
compares short against long.
"""
from __future__ import annotations

import asyncio

import pytest

from aria_service.autonomous import tasks as T
from aria_service.intel import student as S


# ── the measurement that names the defect ────────────────────────────────────

def _doc(unique_tokens: int) -> str:
    return " ".join(f"tok{i}" for i in range(unique_tokens))


def test_jaccard_makes_a_perfect_answer_fail():
    """Pin the premise: every token correct, still under the 0.4 threshold."""
    doc = _doc(308)
    perfect_but_short = " ".join(f"tok{i}" for i in range(40))
    assert S._quick_similarity(perfect_but_short, doc) < 0.4
    # and it is not close — the ceiling is length-bound, not quality-bound
    assert S._quick_similarity(perfect_but_short, doc) < 0.15


def test_grounding_scores_a_perfect_short_answer_as_perfect():
    doc = _doc(308)
    perfect_but_short = " ".join(f"tok{i}" for i in range(40))
    assert T._answer_grounding(perfect_but_short, doc) == 1.0


def test_grounding_is_zero_for_an_unrelated_answer():
    assert T._answer_grounding("alpha beta gamma", _doc(308)) == 0.0


def test_grounding_is_partial_for_a_partly_grounded_answer():
    doc = _doc(100)
    half = " ".join(["tok1", "tok2", "unrelatedone", "unrelatedtwo"])
    assert T._answer_grounding(half, doc) == 0.5


def test_grounding_handles_empty_inputs():
    assert T._answer_grounding("", "abc") == 0.0
    assert T._answer_grounding("abc", "") == 0.0


# ── the grader must now be able to say YES ───────────────────────────────────

def _run_grader(monkeypatch, response: str, research: str):
    from aria_service.intel import reasoning_router

    async def _local(q):
        return {"answered": True, "response": response}

    monkeypatch.setattr(reasoning_router, "try_local_reasoning", _local)
    return asyncio.run(T._grade_researched_cell("defence_procurement", "europe", research))


def test_a_correct_short_answer_now_passes(monkeypatch):
    """The capability test. Pre-fix this returned False for a perfect answer."""
    doc = _doc(308)
    answer = " ".join(f"tok{i}" for i in range(40))
    assert _run_grader(monkeypatch, answer, doc) is True, (
        "a fully-grounded answer was graded WRONG because it was shorter than "
        "the document it was checked against"
    )


def test_an_ungrounded_answer_still_fails(monkeypatch):
    """The grader must still be able to say NO, or it is the old trophy again."""
    assert _run_grader(monkeypatch, "alpha beta gamma delta", _doc(308)) is False


def test_a_mostly_ungrounded_answer_still_fails(monkeypatch):
    doc = _doc(100)
    mostly_wrong = " ".join(["tok1"] + [f"wrong{i}" for i in range(9)])
    assert _run_grader(monkeypatch, mostly_wrong, doc) is False


# ── the tri-state contract (R-F3483) must survive ────────────────────────────

def test_no_research_text_is_unmeasured():
    assert asyncio.run(T._grade_researched_cell("t", "r", "")) is None


def test_escalate_signal_is_unmeasured(monkeypatch):
    from aria_service.intel import reasoning_router

    async def _local(q):
        return {"answered": False}

    monkeypatch.setattr(reasoning_router, "try_local_reasoning", _local)
    assert asyncio.run(T._grade_researched_cell("t", "r", "some research")) is None


def test_instrument_failure_is_unmeasured(monkeypatch):
    from aria_service.intel import reasoning_router

    async def _local(q):
        raise RuntimeError("router down")

    monkeypatch.setattr(reasoning_router, "try_local_reasoning", _local)
    assert asyncio.run(T._grade_researched_cell("t", "r", "some research")) is None


# ── the shared helper must not have been repurposed ──────────────────────────

def test_quick_similarity_is_still_jaccard_for_its_other_callers():
    """student.py:1061 and :2148 compare two RESPONSES of similar length, where
    symmetric similarity is correct. Changing the shared helper would have
    silently altered them."""
    a, b = "alpha beta gamma", "alpha beta delta"
    assert S._quick_similarity(a, b) == pytest.approx(2 / 4)
