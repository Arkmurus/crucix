"""R-F2859 — reading an article must not credit TOPIC mastery either.

R-F2661 removed the participation trophy from the REGIONAL bridge in
``student.reading_session``. The sibling one line above it survived::

    # Reading is reinforcement — small positive mastery bump
    await update_mastery(topics, correct=True, weight=0.3)

Same defect, different axis: it credited comprehension for the ACT of reading,
and it feeds Phase A gate #1 (composite) rather than gate #2. Live gate #1 is
0.546 with ``low_confidence`` — a number partly inflated by reading volume.

THE FIX COSTS NOTHING EXTRA. R-F2661 already computes an honest recall grade for
one topic x region cell in the same loop iteration. Topic mastery now moves on
THAT grade, for THAT single topic, so no additional grader call is made.

Two deliberate narrowings, both in the honest direction:
  * only the graded topic is credited — spreading one grade across every topic
    detected in the article would be a fresh fabrication;
  * when no grade was produced (grader error, budget spent, or no gradable
    cell) topic mastery does NOT move. An unmeasured topic is not a pass, and
    that includes articles with no detectable region.

These are CAPABILITY tests (§3c): they drive the real ``reading_session()``.
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
    def __init__(self) -> None:
        self.regional: list[dict] = []
        self.topical: list[dict] = []

    async def update_regional_mastery(self, topics, regions, correct=None, weight=1.0):
        self.regional.append({"topics": list(topics), "regions": list(regions),
                              "correct": correct, "weight": weight})

    async def update_mastery(self, topics, correct=None, weight=1.0):
        self.topical.append({"topics": list(topics), "correct": correct, "weight": weight})


def _install(monkeypatch, *, grade, grade_calls: list, topics=None, regions=None):
    from aria_service.intel import researcher
    from aria_service.autonomous import tasks as _tasks

    rec = _Recorder()
    feed = {"url": "https://example.invalid/feed", "name": "test_feed"}
    monkeypatch.setattr(researcher, "RESEARCH_FEEDS", [feed], raising=False)

    async def _fake_rss(url, timeout=10.0):
        return [{"title": "Angola defence procurement update",
                 "link": "https://example.invalid/a0",
                 "summary": "angola procurement"}]

    async def _fake_article(url, timeout=12.0):
        return _BODY

    monkeypatch.setattr(researcher, "_fetch_rss", _fake_rss, raising=False)
    monkeypatch.setattr(researcher, "_fetch_article_text", _fake_article, raising=False)

    # R-F3318 — close the LAST live-network hole in this test.
    #
    # reading_session's starved-tag branch (student.py:1839) awaits
    # researcher.web_search, which reaches web_search.py -> sources/academic.py
    # search_all -> search_semantic_scholar and opens a real HTTPS connection.
    # pytest-timeout's stack dump caught it blocked in ssl.create_default_context
    # loading the Windows certificate store:
    #
    #   sources/academic.py:142  search_semantic_scholar
    #     httpx ... create_ssl_context
    #     ssl.py:717  create_default_context   <-- BLOCKED
    #
    # web_search's own `timeout=15.0` CANNOT save it: the block is in synchronous
    # SSL-context construction while the client is being built, before any request
    # timeout applies.
    #
    # This is why the second suite wedge looked "cumulative" and was not. Whether
    # it blocks depends on network and cert-store state, not on how many files ran
    # first, so the same set was clean in one run and wedged in the next, the
    # victim moved between runs, and this file eventually hung ALONE. Fourth
    # instance of the class behind R-F2812 and R-F3298.
    #
    # Both branches are stubbed: web_explorer.explore is tried first
    # (student.py:1811) and web_search is its fallback, so stubbing only one
    # leaves the other reachable.
    async def _no_live_search(*a, **kw):
        return {"results": []}

    monkeypatch.setattr(researcher, "web_search", _no_live_search, raising=False)
    try:
        from aria_service.intel import web_explorer as _we_mod

        async def _no_live_explore(*a, **kw):
            return {"findings": [], "sources": []}

        monkeypatch.setattr(_we_mod, "explore", _no_live_explore, raising=False)
    except Exception:
        pass
    monkeypatch.setattr(student, "detect_topics",
                        lambda *_a, **_k: list(topics if topics is not None else ["procurement"]))
    monkeypatch.setattr(student, "detect_regions",
                        lambda *_a, **_k: list(regions if regions is not None else ["africa"]))

    async def _noop(*_a, **_k):
        return None

    monkeypatch.setattr(student.kb, "store_fact", _noop, raising=False)
    monkeypatch.setattr(student.kb, "extract_facts_from_reading", _noop, raising=False)
    monkeypatch.setattr(student.neural_memory, "learn_from_text", _noop, raising=False)
    monkeypatch.setattr(student, "update_regional_mastery", rec.update_regional_mastery)
    monkeypatch.setattr(student, "update_mastery", rec.update_mastery)

    async def _grader(topic, region, research_text):
        grade_calls.append((topic, region))
        if isinstance(grade, Exception):
            raise grade
        return grade

    monkeypatch.setattr(_tasks, "_grade_researched_cell", _grader, raising=False)
    return rec


def _reading_bumps(rec):
    """Topic-mastery writes made by the reading loop (weight 0.3)."""
    return [c for c in rec.topical if c["weight"] == 0.3]


def test_reading_alone_does_not_credit_topic_mastery(monkeypatch):
    """THE BUG: reading used to bump topic mastery correct=True unconditionally."""
    calls: list = []
    rec = _install(monkeypatch, grade=False, grade_calls=calls)

    asyncio.run(student.reading_session(llm=None, num_articles=1))

    trophies = [c for c in _reading_bumps(rec) if c["correct"] is True]
    assert not trophies, (
        f"R-F2859 REGRESSION: reading credited topic mastery correct=True: {trophies}"
    )
    assert any(c["correct"] is False for c in _reading_bumps(rec)), (
        "a failed recall grade must be recorded as correct=False, not dropped"
    )


def test_a_real_recall_pass_still_lifts_topic_mastery(monkeypatch):
    """The good path stays intact: genuine recall still earns correct=True."""
    calls: list = []
    rec = _install(monkeypatch, grade=True, grade_calls=calls)

    asyncio.run(student.reading_session(llm=None, num_articles=1))

    assert any(c["correct"] is True for c in _reading_bumps(rec)), (
        "an honest recall PASS must still credit the topic"
    )


def test_grader_error_does_not_move_topic_mastery(monkeypatch):
    """An unmeasured topic is not a pass — and not a fabricated fail either."""
    calls: list = []
    rec = _install(monkeypatch, grade=RuntimeError("grader down"), grade_calls=calls)

    asyncio.run(student.reading_session(llm=None, num_articles=1))

    assert not _reading_bumps(rec), (
        f"a grader error must skip the topic bump entirely, got {_reading_bumps(rec)}"
    )


def test_only_the_graded_topic_is_credited(monkeypatch):
    """NEGATIVE CONTROL: one grade must not be spread across every topic."""
    calls: list = []
    rec = _install(monkeypatch, grade=True, grade_calls=calls,
                   topics=["procurement", "compliance", "technical"])

    asyncio.run(student.reading_session(llm=None, num_articles=1))

    for c in _reading_bumps(rec):
        assert len(c["topics"]) == 1, (
            f"exactly one graded topic may be credited, got {c['topics']}"
        )


def test_no_gradable_region_means_no_topic_credit(monkeypatch):
    """NEGATIVE CONTROL: 'global' is the detect_regions fallback and is dropped
    from the heatmap (R-F1893), so nothing was graded — nothing may be credited."""
    calls: list = []
    rec = _install(monkeypatch, grade=True, grade_calls=calls, regions=["global"])

    asyncio.run(student.reading_session(llm=None, num_articles=1))

    assert not calls, "no grader call should happen for the 'global' fallback"
    assert not _reading_bumps(rec), (
        f"with nothing graded, topic mastery must not move, got {_reading_bumps(rec)}"
    )
