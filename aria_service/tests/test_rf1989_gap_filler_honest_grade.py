"""R-F1989 (Claude review of R-F1986) — gap-filler mastery is HONESTLY graded.

The original R-F1986 bridge credited regional mastery with correct=True whenever
research returned >200 chars — a participation trophy that would inflate Phase A
gate #2 without proving comprehension (CLAUDE.md §1: no clamping). The fix grades
mastery via the LOCAL reasoning stack, exactly like self_quiz: it only counts as
correct when ARIA can actually answer a question about the cell AND the answer
overlaps the research findings. These tests pin that real-grade contract.
"""
import asyncio

import aria_service.autonomous.tasks as tasks
from aria_service.intel import reasoning_router, student


def _restore(originals):
    for obj, attr, val in originals:
        setattr(obj, attr, val)


def test_grade_false_when_local_cannot_answer():
    async def run():
        orig = [(reasoning_router, "try_local_reasoning", reasoning_router.try_local_reasoning)]
        async def _no_answer(question, **kw):
            return {"answered": False}
        reasoning_router.try_local_reasoning = _no_answer
        try:
            ok = await tasks._grade_researched_cell(
                "compliance", "west_africa", "rich research findings text " * 20)
            assert ok is False, "no local answer must NOT credit mastery"
        finally:
            _restore(orig)
    asyncio.run(run())


def test_grade_false_when_answer_is_irrelevant():
    async def run():
        orig = [(reasoning_router, "try_local_reasoning", reasoning_router.try_local_reasoning)]
        async def _bad(question, **kw):
            return {"answered": True, "response": "completely unrelated zzz qqq"}
        reasoning_router.try_local_reasoning = _bad
        try:
            ok = await tasks._grade_researched_cell(
                "compliance", "west_africa",
                "Nigeria defence procurement export control sanctions framework 2026")
            assert ok is False, "low-similarity answer must NOT credit mastery"
        finally:
            _restore(orig)
    asyncio.run(run())


def test_grade_true_only_when_answered_and_similar():
    async def run():
        research = "Nigeria defence procurement export control sanctions framework 2026 EUC"
        orig = [(reasoning_router, "try_local_reasoning", reasoning_router.try_local_reasoning)]
        async def _good(question, **kw):
            return {"answered": True, "response": research}   # reproduces the findings
        reasoning_router.try_local_reasoning = _good
        try:
            ok = await tasks._grade_researched_cell("compliance", "west_africa", research)
            assert ok is True, "a real, relevant local answer credits mastery"
        finally:
            _restore(orig)
    asyncio.run(run())


def test_grade_false_on_empty_research():
    async def run():
        ok = await tasks._grade_researched_cell("compliance", "west_africa", "")
        assert ok is False
    asyncio.run(run())
