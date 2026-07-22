"""R-F2661 — the SECOND reading participation trophy must not inflate gate #2.

CLAUDE.md §1 records this as the open follow-up to R-F2660: the R-F196
article->regional bridge inside ``student.reading_session`` credited
``update_regional_mastery(..., correct=True)`` whenever an article merely
mentioned a topic AND a region. That measures reading VOLUME, not
comprehension — the exact participation-trophy shape R-F2660 removed from the
higher-volume R-F1744 loop.

These are CAPABILITY tests (§3c): they drive the real broken entry point,
``student.reading_session()``, not a helper. Each asserts the user-visible
outcome — what ``update_regional_mastery`` is actually called with.

The honesty contract being locked down:
  * grader says False  -> the cell is credited correct=False (does NOT lift)
  * grader raises      -> the mastery update is SKIPPED (never fabricated True)
  * grader says True   -> the cell is credited correct=True (good path intact)
  * over budget        -> SKIPPED, never credited (unmeasured != correct)
"""
from __future__ import annotations

import asyncio

import pytest

from aria_service.intel import student


_BODY = (
    "Angola signed a defence procurement agreement covering maintenance and "
    "logistics support. The contract covers spare parts, training and export "
    "control compliance obligations for the supplier over a five year term. "
    "Officials described the award as a significant milestone for the region."
)


class _Recorder:
    """Captures every regional-mastery call the reading session makes."""

    def __init__(self) -> None:
        self.regional: list[dict] = []
        self.topical: list[dict] = []

    async def update_regional_mastery(self, topics, regions, correct=None, weight=1.0):
        self.regional.append(
            {"topics": list(topics), "regions": list(regions),
             "correct": correct, "weight": weight}
        )

    async def update_mastery(self, topics, correct=None, weight=1.0):
        self.topical.append({"topics": list(topics), "correct": correct})


def _install(monkeypatch, *, grade, grade_calls: list, articles: int = 1):
    """Neutralise every side-effecting dependency of reading_session.

    Leaves exactly one thing live: the regional-mastery decision under test.
    """
    from aria_service.intel import researcher
    from aria_service.autonomous import tasks as _tasks

    rec = _Recorder()

    feed = {"url": "https://example.invalid/feed", "name": "test_feed"}
    monkeypatch.setattr(researcher, "RESEARCH_FEEDS", [feed], raising=False)

    async def _fake_rss(url, timeout=10.0):
        return [
            {"title": f"Angola defence procurement update {i}",
             "link": f"https://example.invalid/a{i}",
             "summary": "angola procurement"}
            for i in range(articles)
        ]

    async def _fake_article(url, timeout=12.0):
        return _BODY

    monkeypatch.setattr(researcher, "_fetch_rss", _fake_rss, raising=False)
    monkeypatch.setattr(researcher, "_fetch_article_text", _fake_article, raising=False)

    # Deterministic topic/region detection — the article DOES mention both,
    # which is precisely the condition that used to hand out the trophy.
    monkeypatch.setattr(student, "detect_topics", lambda *_a, **_k: ["procurement"])
    monkeypatch.setattr(student, "detect_regions", lambda *_a, **_k: ["africa"])

    # Silence the storage side-effects; they are not what this test measures.
    async def _noop(*_a, **_k):
        return None

    monkeypatch.setattr(student.kb, "store_fact", _noop, raising=False)
    monkeypatch.setattr(student.kb, "extract_facts_from_reading", _noop, raising=False)
    monkeypatch.setattr(
        student.neural_memory, "learn_from_text", _noop, raising=False
    )

    monkeypatch.setattr(student, "update_regional_mastery", rec.update_regional_mastery)
    monkeypatch.setattr(student, "update_mastery", rec.update_mastery)

    async def _grader(topic, region, research_text):
        grade_calls.append((topic, region))
        if isinstance(grade, Exception):
            raise grade
        return grade

    monkeypatch.setattr(_tasks, "_grade_researched_cell", _grader, raising=False)
    return rec


def test_reading_alone_does_not_credit_regional_mastery(monkeypatch):
    """THE BUG: an article that mentions a topic+region used to score correct=True.

    With the local reasoning stack unable to answer (grade False), reading must
    NOT lift the cell. Pre-fix this asserts False because the call was
    hardcoded correct=True.
    """
    calls: list = []
    rec = _install(monkeypatch, grade=False, grade_calls=calls)

    asyncio.run(student.reading_session(llm=None, num_articles=1))

    assert rec.regional, "the R-F196 regional bridge did not run at all"
    trophies = [c for c in rec.regional if c["correct"] is True]
    assert not trophies, (
        "R-F2661 REGRESSION: reading credited regional mastery correct=True "
        f"without an honest recall grade: {trophies}"
    )
    assert any(c["correct"] is False for c in rec.regional), (
        "a failed recall grade must be recorded as correct=False, not dropped"
    )
    assert calls, "the honest grader was never consulted"


def test_grader_failure_skips_the_update_and_never_fabricates_a_pass(monkeypatch):
    """A grader error must SKIP the cell — an unmeasured cell is not a pass."""
    calls: list = []
    rec = _install(monkeypatch, grade=RuntimeError("grader down"), grade_calls=calls)

    asyncio.run(student.reading_session(llm=None, num_articles=1))

    assert calls, "the honest grader was never consulted"
    assert not rec.regional, (
        "a grader error must skip the regional-mastery update entirely, "
        f"but it still wrote: {rec.regional}"
    )


def test_a_real_recall_pass_still_lifts_the_cell(monkeypatch):
    """The good path stays intact: genuine recall still earns correct=True."""
    calls: list = []
    rec = _install(monkeypatch, grade=True, grade_calls=calls)

    asyncio.run(student.reading_session(llm=None, num_articles=1))

    assert any(c["correct"] is True for c in rec.regional), (
        "an honest recall PASS must still credit the cell"
    )


def test_budget_is_not_spent_on_a_topic_outside_TOPICS(monkeypatch):
    """NEGATIVE CONTROL: update_regional_mastery skips topics outside TOPICS,
    so grading one would burn a bounded grader call on a guaranteed no-op."""
    calls: list = []
    rec = _install(monkeypatch, grade=True, grade_calls=calls)
    monkeypatch.setattr(student, "detect_topics", lambda *_a, **_k: ["not_a_real_topic"])

    asyncio.run(student.reading_session(llm=None, num_articles=1))

    assert not calls, f"grader must not run for a non-TOPICS topic, got {calls}"
    assert not rec.regional, f"no regional write should occur, got {rec.regional}"


def test_budget_is_not_spent_on_the_global_fallback_region(monkeypatch):
    """NEGATIVE CONTROL: detect_regions falls back to ['global'] when nothing
    matched, and R-F1893 drops 'global' from the heatmap — never grade it."""
    calls: list = []
    rec = _install(monkeypatch, grade=True, grade_calls=calls)
    monkeypatch.setattr(student, "detect_regions", lambda *_a, **_k: ["global"])

    asyncio.run(student.reading_session(llm=None, num_articles=1))

    assert not calls, f"grader must not run for the 'global' fallback, got {calls}"
    assert not rec.regional, f"no regional write should occur, got {rec.regional}"


def test_grading_is_cost_guarded_per_session(monkeypatch):
    """CLAUDE.md §1 requires this be cost-guarded — it runs per ARTICLE.

    Beyond the budget the cell must be SKIPPED, never credited.
    """
    monkeypatch.setenv("ARIA_READING_GRADE_BUDGET", "1")
    calls: list = []
    rec = _install(monkeypatch, grade=True, grade_calls=calls, articles=4)

    asyncio.run(student.reading_session(llm=None, num_articles=4))

    assert len(calls) <= 1, (
        f"the honest grader must respect the per-session budget, got {len(calls)} calls"
    )
    assert not [c for c in rec.regional if c["correct"] is True][1:], (
        "articles beyond the grading budget must not be credited correct=True"
    )
