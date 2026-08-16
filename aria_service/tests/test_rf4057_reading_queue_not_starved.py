"""R-F4057 (C-121) — the reading-queue backlog could be starved indefinitely.

`_collect_candidate_topics` fills its `max_topics` slots FSRS-first, then adds
reading-queue items only if room remains:

    for entry in await student.get_due_topics(limit=max_topics):   # fills 5
        ...
    for item in await reading_queue.pop_pending(limit=max_topics):
        ...
        if len(candidates) >= max_topics:
            break                                                  # never runs

Once FSRS has `max_topics` due topics — the steady state for a mature deck —
the reading queue contributes nothing, forever. It is a queue with no drain.

MEASURED LIVE on aria-intel 2026-08-16, straight from `/data/aria_reading_queue.db`:

    SELECT status, COUNT(*) FROM reading_queue GROUP BY status
    -> [('pending', 94)]

**94 pending, and not one row in `done`, `processing` or `skipped`.** The queue
had never drained a single item since it was created. That is also why
`reading_queue` was the one module of R-F4052's five that never emitted a brain
signal: `mark_processed` had genuinely never succeeded, so the silence was
honest — it was reporting a starved capability, not a broken wire.

THE RULE: no candidate source may be starved indefinitely by another. A bounded
reservation is enough — the queue only needs to make progress, not to win.

Deliberately NOT done: reordering the queue ahead of FSRS. FSRS is the proven
scheduler and its due topics are time-sensitive; demoting it to drain a backlog
would trade one starvation for another.
"""
from __future__ import annotations

import asyncio

from aria_service.learning import learning_controller as lc


def _fsrs(n: int):
    async def _f(limit=5):
        return [{"topic": f"fsrs-topic-{i}", "due_at": 0} for i in range(n)]
    return _f


def _queue(n: int):
    async def _q(limit=10):
        return [{"id": 100 + i, "topic": f"queued-topic-{i}"} for i in range(n)]
    return _q


def _collect(monkeypatch, *, fsrs_n: int, queue_n: int, max_topics: int = 5):
    from aria_service.intel import student
    from aria_service.learning import reading_queue

    monkeypatch.setattr(student, "get_due_topics", _fsrs(fsrs_n))
    monkeypatch.setattr(reading_queue, "pop_pending", _queue(queue_n))
    return asyncio.run(lc._collect_candidate_topics(max_topics))


def test_a_saturated_fsrs_deck_does_not_starve_the_queue(monkeypatch):
    """THE defect: FSRS fills every slot, so a 94-item backlog never drains."""
    cands = _collect(monkeypatch, fsrs_n=10, queue_n=94)

    sources = [c.get("source") for c in cands]
    assert "reading_queue" in sources, (
        "a saturated FSRS deck left ZERO slots for the reading queue — the "
        "backlog can never drain, which is what produced 94 pending / 0 "
        f"processed live. Got sources: {sources}"
    )


def test_the_reservation_is_bounded(monkeypatch):
    """The queue must not take over the cycle either."""
    cands = _collect(monkeypatch, fsrs_n=10, queue_n=94, max_topics=5)

    assert len(cands) <= 5, f"max_topics exceeded: {len(cands)}"
    n_queue = sum(1 for c in cands if c.get("source") == "reading_queue")
    assert n_queue <= 2, (
        f"the reservation must be a floor, not a takeover — FSRS is the proven "
        f"scheduler and its due topics are time-sensitive. Got {n_queue}/5"
    )
    assert any(c.get("source") == "fsrs_due" for c in cands), (
        "FSRS must keep the majority of slots"
    )


def test_no_queue_items_means_fsrs_keeps_every_slot(monkeypatch):
    """The reservation must not waste a slot when there is nothing to drain."""
    cands = _collect(monkeypatch, fsrs_n=10, queue_n=0, max_topics=5)
    assert len(cands) == 5
    assert all(c.get("source") == "fsrs_due" for c in cands)


def test_no_fsrs_topics_still_lets_the_queue_fill(monkeypatch):
    """The pre-existing behaviour when FSRS is empty must be unchanged."""
    cands = _collect(monkeypatch, fsrs_n=0, queue_n=94, max_topics=5)
    assert len(cands) == 5
    assert all(c.get("source") == "reading_queue" for c in cands)


def test_duplicate_topics_are_still_deduped(monkeypatch):
    """The reservation must not reintroduce duplicates across sources."""
    from aria_service.intel import student
    from aria_service.learning import reading_queue

    async def _same_fsrs(limit=5):
        return [{"topic": "shared-topic", "due_at": 0}]

    async def _same_queue(limit=10):
        return [{"id": 1, "topic": "shared-topic"}]

    monkeypatch.setattr(student, "get_due_topics", _same_fsrs)
    monkeypatch.setattr(reading_queue, "pop_pending", _same_queue)

    cands = asyncio.run(lc._collect_candidate_topics(5))
    topics = [c["topic"] for c in cands]
    assert topics.count("shared-topic") == 1, f"duplicate leaked: {topics}"
