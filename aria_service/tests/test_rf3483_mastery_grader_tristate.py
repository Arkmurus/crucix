"""R-F3483 — the mastery grader punished "could not measure" as "wrong".

CLAUDE.md §1 states, of R-F2660:

    "a failed grade credits ``correct=False`` (does not lift), a grader error
     SKIPS the update (never fabricates a pass)"

The code does not do that. ``_grade_researched_cell`` (autonomous/tasks.py:2266)
returns a plain ``bool`` and answers **False** on all of:

    except Exception          around try_local_reasoning   (2287)
    not local.get("answered")                              (2289)
    empty response                                         (2292)
    except Exception          around _quick_similarity     (2296)

and the caller then calls ``update_regional_mastery(correct=graded_correct)``
unconditionally (2408). There is no skip path anywhere.

Why that is not a nitpick. ``reasoning_router.try_local_reasoning`` documents
``answered: False`` as a ROUTING SIGNAL meaning "no local source was confident,
escalate to the cloud" (reasoning_router.py:220-224) — it is explicitly not a
verdict on correctness, and it is also returned by two deliberate BYPASSES
(self-infra introspection at Stage 0, self-capability questions at Stage 0.5).
Every one of those was being recorded as ARIA getting the answer wrong.

The mastery EMA is ``score += alpha*(obs - score)`` with alpha = 0.1*weight
(student.py:2372-2382), so at weight=0.5 it takes roughly 100 consecutive
``correct=False`` to drive a cell from INITIAL_MASTERY 0.5 down to 0.003. Live
gate #2 floor on 2026-07-30: **0.003**, with `technical × central_africa` and
`compliance × latam_lusophone` at the bottom — while the same heatmap shows
`market_intel × southern_africa` at 0.965. So the grader clearly does pass
sometimes; the question is how much of the floor is starvation and how much is
the instrument, and today the two are indistinguishable because a measurement
failure and a wrong answer are recorded identically.

This is the SAME tri-state that R-F2639 already codified one layer up, for gate
reporting: ``pass`` is True/False when measured and **None when it could not be
measured**, rendered "unknown", never "open" — because "could not measure" is not
"measured and failed". This applies that rule to the mastery layer.

Deliberately NOT changed: a genuine wrong answer still credits correct=False.
This does not soften the gate — it stops counting non-measurements as evidence.
"""
from __future__ import annotations

import pytest

from aria_service.autonomous import tasks as _tasks

# R-F3757/§16 — NOT inspect.getsource: it slices at line numbers captured
# AT IMPORT, so an edit mid-run silently returns a DIFFERENT function's body.
from ._source_probe import function_source


class _Router:
    """Stand-in for reasoning_router with a scriptable outcome."""

    def __init__(self, outcome):
        self._outcome = outcome

    async def try_local_reasoning(self, _question, **_kw):
        if isinstance(self._outcome, Exception):
            raise self._outcome
        return self._outcome


@pytest.fixture
def _patch_router(monkeypatch):
    def _install(outcome, similarity=1.0):
        # `_grade_researched_cell` does `from ..intel import reasoning_router`,
        # which resolves from the PACKAGE ATTRIBUTE once the package is imported
        # — patching sys.modules alone leaves the real module in place and the
        # stub never fires (memory/order-dependent-tests-are-leaked-state.md:
        # "a stale parent-package attribute defeating sys.modules.pop"). These
        # tests passed in isolation and failed in a sweep for exactly that
        # reason, so patch the attribute monkeypatch can actually restore.
        from aria_service import intel as _intel
        from aria_service.intel import reasoning_router as _rr
        router = _Router(outcome)
        monkeypatch.setattr(_rr, "try_local_reasoning",
                            router.try_local_reasoning, raising=False)
        monkeypatch.setattr(_intel, "reasoning_router", _rr, raising=False)

        # R-F3971 (C-60) — the grader now scores ANSWER GROUNDING
        # (|answer n doc| / |answer|) instead of student._quick_similarity's
        # Jaccard, because Jaccard against a 4,000-char document capped a
        # PERFECT answer below the pass bar on length alone. The seam moved;
        # the contracts asserted below did not, and `_answer_grounding` is
        # still called inside the same try/except, so a scorer crash is still
        # UNMEASURED rather than WRONG.
        if isinstance(similarity, Exception):
            def _sim(_a, _b):
                raise similarity
        else:
            def _sim(_a, _b):
                return similarity
        monkeypatch.setattr(_tasks, "_answer_grounding", _sim, raising=False)
    return _install


class TestGraderIsTriState:
    """UNMEASURED must be distinguishable from WRONG."""

    @pytest.mark.asyncio
    async def test_correct_answer_grades_true(self, _patch_router):
        _patch_router({"answered": True, "response": "a real answer"}, similarity=0.9)
        assert await _tasks._grade_researched_cell("technical", "europe", "research") is True

    @pytest.mark.asyncio
    async def test_wrong_answer_grades_false(self, _patch_router):
        """A real miss must still count against mastery — the gate is not softened."""
        _patch_router({"answered": True, "response": "unrelated"}, similarity=0.05)
        assert await _tasks._grade_researched_cell("technical", "europe", "research") is False

    @pytest.mark.asyncio
    async def test_escalate_signal_is_unmeasured_not_wrong(self, _patch_router):
        """answered=False means 'no local source was confident, escalate to the
        cloud' (reasoning_router.py:220) — it is not a wrong answer."""
        _patch_router({"answered": False, "reason": "escalate"})
        assert await _tasks._grade_researched_cell("technical", "europe", "research") is None

    @pytest.mark.asyncio
    async def test_reasoning_crash_is_unmeasured(self, _patch_router):
        _patch_router(RuntimeError("local stack exploded"))
        assert await _tasks._grade_researched_cell("technical", "europe", "research") is None

    @pytest.mark.asyncio
    async def test_similarity_crash_is_unmeasured(self, _patch_router):
        _patch_router({"answered": True, "response": "text"},
                      similarity=ValueError("scorer exploded"))
        assert await _tasks._grade_researched_cell("technical", "europe", "research") is None

    @pytest.mark.asyncio
    async def test_empty_research_text_is_unmeasured(self):
        """Nothing to compare against is not evidence that ARIA is wrong."""
        assert await _tasks._grade_researched_cell("technical", "europe", "") is None

    @pytest.mark.asyncio
    async def test_empty_response_is_unmeasured(self, _patch_router):
        _patch_router({"answered": True, "response": ""})
        assert await _tasks._grade_researched_cell("technical", "europe", "research") is None


class TestUnmeasuredNeverTouchesMastery:
    """The caller must SKIP the update, not pass None through as falsey."""

    def test_caller_skips_the_update_when_grade_is_none(self):
        """Guards the wiring: `if graded is not None` must gate the update.

        A None flowing into update_regional_mastery(correct=None) would be
        treated as False by the EMA (`obs = 1.0 if correct else 0.0`) — the exact
        bug this change exists to remove, reintroduced silently.
        """
        import ast, inspect, textwrap
        tree = ast.parse(textwrap.dedent(function_source(_tasks, "fill_knowledge_gaps")))

        # Find the update call and prove it sits inside a None-guard, rather
        # than grepping for one exact phrasing (the wording could change while
        # the defect returns).
        guarded = False
        for node in ast.walk(tree):
            if not isinstance(node, ast.If):
                continue
            test_src = ast.dump(node.test)
            if "graded_correct" not in test_src or "NameConstant" not in test_src.replace(
                    "Constant(value=None)", "NameConstant"):
                continue
            for branch in (node.body, node.orelse):
                for sub in branch:
                    for call in ast.walk(sub):
                        if (isinstance(call, ast.Call)
                                and getattr(call.func, "attr", "") == "update_regional_mastery"):
                            guarded = True
        assert guarded, (
            "update_regional_mastery is not inside a `graded_correct is/is not "
            "None` guard — an unmeasured grade would be coerced to a wrong "
            "answer by the EMA"
        )

    def test_mastery_ema_would_treat_none_as_wrong(self):
        """Documents WHY the guard above is load-bearing."""
        from aria_service.intel import student
        import inspect
        src = function_source(student, "update_regional_mastery")
        assert "1.0 if correct else 0.0" in src
