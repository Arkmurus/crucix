"""R-F4236 / C-216 — the THIRD reading trophy, and the only one with a motive.

R-F2660 removed a participation trophy from the R-F1744 regional loop. R-F2661
removed it from the R-F196 article->regional bridge. R-F2859 removed the topic
sibling one line above that. This is the last one, and it is the most explicit
about what it was doing::

    # Lift mastery on the exact starved tag (not the auto-detected ones — that
    # would re-write to lang:* or compliance, and we explicitly need the named
    # tag to move so the proactive alert stops repeating).
    await update_mastery([stag], correct=True, weight=0.2)

**Mastery was being lifted to switch off a warning light**, not to record
learning — inside the per-hit loop, so a tag could be credited twice for the mere
existence of two search results. Nothing about comprehension was tested.

## The justification no longer holds even on its own terms

`proactive.prepare_weak_topics` derives its alert from `student`'s `weak_topics`,
so R-F163's reasoning was: the tag can never be studied, so it stays weak, so the
alert repeats forever. **R-F211 — later than R-F163 — fixed that properly**, with
an announce-hash dedup carrying a 14-day TTL (`LAST_MASTERY_PREP_HASH_KEY`,
`ex=14*86400`). An unchanged weak set is now suppressed whether or not mastery
moves. And `starved_studied` already records that the tag WAS studied, which is
the honest artefact this branch produces.

## What replaces it

The same honest recall grade every other mastery mover uses. Starved tags
(`angola_procurement`, `uk_export_control`) are not topic x region cells — they
arrive from the proactive reading queue and sit outside TOPICS — so
`_grade_researched_cell` could not express them, which is why this site was
skipped when its two siblings were fixed. R-F4236 extracts the shared tail as
`_grade_researched_question` and adds `_grade_researched_tag`, so there is now ONE
grader with three callers. The existing cell question wording is unchanged.

Graded ONCE per tag on the combined text (not once per hit): cheaper, and
spreading one recall result across several hits would be a fresh fabrication.
Budget-bounded like R-F2661, and a tri-state `None` SKIPS the update — an
unmeasured tag is neither a pass nor a miss.

These are CAPABILITY tests (§3c): they drive the real `reading_session()`.
"""
from __future__ import annotations

import asyncio

import pytest

from aria_service.intel import student


class _Recorder:
    def __init__(self) -> None:
        self.topical: list[dict] = []
        self.regional: list[dict] = []

    async def update_regional_mastery(self, topics, regions, correct=None, weight=1.0):
        self.regional.append({"topics": list(topics), "regions": list(regions),
                              "correct": correct, "weight": weight})

    async def update_mastery(self, topics, correct=None, weight=1.0):
        self.topical.append({"topics": list(topics), "correct": correct,
                             "weight": weight})


_STARVED_TAG = "angola_procurement"


def _install(monkeypatch, *, grade, grade_calls: list, hits: int = 2,
             queued: tuple[str, ...] = (_STARVED_TAG,)):
    """Drive reading_session's STARVED-TAG branch with no live network.

    Mirrors test_rf2859's harness. Both search paths are stubbed because
    web_explorer.explore is tried first and researcher.web_search is its
    fallback — stubbing one leaves the other reachable (R-F3318).
    """
    from aria_service.intel import researcher
    from aria_service.autonomous import tasks as _tasks

    rec = _Recorder()
    monkeypatch.setattr(researcher, "RESEARCH_FEEDS",
                        [{"url": "https://example.invalid/feed", "name": "t"}],
                        raising=False)

    async def _fake_rss(url, timeout=10.0):
        return []          # no articles — isolate the starved-tag branch

    monkeypatch.setattr(researcher, "_fetch_rss", _fake_rss, raising=False)

    class _F:
        def __init__(self, i):
            self.source_url = f"https://example.invalid/{i}"
            self.value = f"Angola procurement award number {i}"
            self.context = ("Angola signed a defence procurement agreement "
                            "covering maintenance, spare parts and export "
                            "control compliance obligations.")

    class _ER:
        def __init__(self, n):
            self.facts = [_F(i) for i in range(n)]

    async def _explore(*a, **kw):
        return _ER(hits)

    from aria_service.intel import web_explorer as _we
    monkeypatch.setattr(_we, "explore", _explore, raising=False)

    async def _no_live_search(*a, **kw):
        return {"results": []}
    monkeypatch.setattr(researcher, "web_search", _no_live_search, raising=False)

    async def _noop(*_a, **_k):
        return None
    monkeypatch.setattr(student.kb, "store_fact", _noop, raising=False)
    monkeypatch.setattr(student.kb, "extract_facts_from_reading", _noop, raising=False)
    monkeypatch.setattr(student.neural_memory, "learn_from_text", _noop, raising=False)
    monkeypatch.setattr(student, "update_mastery", rec.update_mastery)
    monkeypatch.setattr(student, "update_regional_mastery", rec.update_regional_mastery)

    # The starved list is built inside reading_session from
    # `proactive.get_reading_queue()` — a queued topic that is NOT in the
    # mastery weak_pool (region-specific tags like angola_procurement) becomes
    # a STARVED tag. Patch the real source, not an invented helper (§3b).
    from aria_service.intel import proactive as _prc

    async def _queue(limit=10):
        return [{"topic": t} for t in queued]

    monkeypatch.setattr(_prc, "get_reading_queue", _queue, raising=True)

    async def _grader(tag, research_text):
        grade_calls.append((tag, research_text))
        if isinstance(grade, Exception):
            raise grade
        return grade

    monkeypatch.setattr(_tasks, "_grade_researched_tag", _grader, raising=False)
    return rec


def _starved_bumps(rec):
    """Mastery writes made by the starved-tag branch (weight 0.2)."""
    return [c for c in rec.topical if c["weight"] == 0.2]


class TestTheOneGraderIsShared:
    """R-F4236's structural claim: one implementation, three callers."""

    def test_cell_and_tag_graders_delegate_to_the_same_function(self):
        from ._source_probe import function_source
        from aria_service.autonomous import tasks as _tasks

        for fn in ("_grade_researched_cell", "_grade_researched_tag"):
            src = function_source(_tasks, fn)
            assert "_grade_researched_question(" in src, (
                f"{fn} must delegate to the ONE grader — two grading "
                f"implementations is how the three trophies diverged in the "
                f"first place")

    def test_the_cell_question_wording_is_unchanged(self):
        """A refactor must not silently re-word what the local stack is asked."""
        from ._source_probe import function_source
        from aria_service.autonomous import tasks as _tasks

        src = function_source(_tasks, "_grade_researched_cell")
        assert "What are the most important" in src and "recent developments for" in src

    def test_every_failure_path_of_the_shared_grader_returns_none(self):
        """None, never False — an instrument that could not measure must not
        record ARIA getting the answer wrong (R-F3483)."""
        from ._source_probe import function_source
        from aria_service.autonomous import tasks as _tasks

        src = function_source(_tasks, "_grade_researched_question")
        assert "return False" not in src, (
            "a failure path returning False would drive mastery DOWN for an "
            "instrument problem")
        assert src.count("return None") >= 3


class TestTheTrophyIsGone:

    def test_a_search_hit_alone_no_longer_credits_the_tag(self, monkeypatch):
        """THE BUG: a title+snippet used to be worth correct=True."""
        calls: list = []
        rec = _install(monkeypatch, grade=False, grade_calls=calls)

        asyncio.run(student.reading_session(llm=None, num_articles=1))

        trophies = [c for c in _starved_bumps(rec) if c["correct"] is True]
        assert not trophies, (
            f"R-F4236 REGRESSION: a starved tag was credited correct=True for "
            f"finding text: {trophies}")
        assert any(c["correct"] is False for c in _starved_bumps(rec)), (
            "a failed recall grade must be recorded as correct=False, not dropped")

    def test_a_real_recall_pass_still_lifts_the_tag(self, monkeypatch):
        calls: list = []
        rec = _install(monkeypatch, grade=True, grade_calls=calls)

        asyncio.run(student.reading_session(llm=None, num_articles=1))

        got = [c for c in _starved_bumps(rec) if c["correct"] is True]
        assert got, "an honest recall PASS must still credit the starved tag"
        assert got[0]["topics"] == [_STARVED_TAG], (
            "only the NAMED starved tag may be credited — spreading the grade "
            "would re-write lang:*/compliance, which R-F163 was right to avoid")

    def test_an_unmeasured_tag_moves_nothing(self, monkeypatch):
        """Tri-state None: neither a pass nor a fabricated miss (R-F3694)."""
        calls: list = []
        rec = _install(monkeypatch, grade=None, grade_calls=calls)

        asyncio.run(student.reading_session(llm=None, num_articles=1))

        assert not _starved_bumps(rec), (
            f"an unmeasured tag must not move mastery either way, got "
            f"{_starved_bumps(rec)}")

    def test_a_grader_error_moves_nothing(self, monkeypatch):
        calls: list = []
        rec = _install(monkeypatch, grade=RuntimeError("grader down"),
                       grade_calls=calls)

        asyncio.run(student.reading_session(llm=None, num_articles=1))

        assert not _starved_bumps(rec)


class TestItIsGradedOncePerTagNotPerHit:

    def test_two_hits_produce_one_grade_and_one_write(self, monkeypatch):
        """The trophy fired INSIDE the hit loop, so a tag was credited twice."""
        calls: list = []
        rec = _install(monkeypatch, grade=True, grade_calls=calls, hits=2)

        asyncio.run(student.reading_session(llm=None, num_articles=1))

        assert len(calls) == 1, f"expected ONE grader call per tag, got {len(calls)}"
        assert len(_starved_bumps(rec)) == 1, (
            f"expected ONE mastery write per tag, got {_starved_bumps(rec)}")

    def test_the_grade_sees_the_combined_text_of_every_hit(self, monkeypatch):
        calls: list = []
        _install(monkeypatch, grade=True, grade_calls=calls, hits=2)

        asyncio.run(student.reading_session(llm=None, num_articles=1))

        assert calls, "the grader must be called"
        text = calls[0][1]
        assert "number 0" in text and "number 1" in text, (
            "grading one hit while crediting the tag for both would be the "
            "trophy in a smaller costume")

    def test_no_hits_means_no_grade_and_no_write(self, monkeypatch):
        calls: list = []
        rec = _install(monkeypatch, grade=True, grade_calls=calls, hits=0)

        asyncio.run(student.reading_session(llm=None, num_articles=1))

        assert not calls, "nothing was read — there is nothing to grade"
        assert not _starved_bumps(rec)


class TestTheBudgetBoundsTheCost:

    def test_tags_beyond_the_budget_are_skipped_not_credited(self, monkeypatch):
        """R-F2661's rule: beyond budget is SKIPPED, never credited."""
        monkeypatch.setenv("ARIA_STARVED_GRADE_BUDGET", "1")
        calls: list = []
        rec = _install(monkeypatch, grade=True, grade_calls=calls,
                       queued=("angola_procurement", "uk_export_control",
                               "brazil_compliance"))

        asyncio.run(student.reading_session(llm=None, num_articles=1))

        assert len(calls) <= 1, f"budget of 1 must bound grader calls: {calls}"
        assert len(_starved_bumps(rec)) <= 1, (
            f"an ungraded tag must not be credited: {_starved_bumps(rec)}")
