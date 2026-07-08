"""R-F2507 — durable SQLite brain-ingest priority queue.

These tests pin the queue's contract (see aria_service/intel/brain_ingest_queue.py):
  1. FIFO within the same priority.
  2. Lower priority NUMBER drains first (0 = highest).
  3. mark_done removes claimed rows.
  4. mark_failed retries with exponential backoff, then DLQs at max_attempts.
  5. recover_stuck resets stranded 'processing' rows back to 'pending'.
  6. enqueue over the depth cap evicts the oldest-lowest-priority pending row.
  7. stats() reports accurate depth / by_priority / processing / dead_letter.

Style mirrors test_rf2504_hotcold_reclaim.py: point the module's DB env var at
a tmp file, reconnect the module, and drive it via asyncio.run() inside sync
test functions (no external anyio/pytest-asyncio dependency).
"""
from __future__ import annotations

import asyncio
import importlib

import pytest


def _fresh_module(tmp_path, monkeypatch, **env):
    """Point the queue at a fresh tmp DB, (re)import + connect the module."""
    db = tmp_path / "biq.db"
    monkeypatch.setenv("ARIA_BRAIN_QUEUE_DB", str(db))
    for k, v in env.items():
        monkeypatch.setenv(k, str(v))
    from aria_service.intel import brain_ingest_queue as _biq
    # Reload so module-level env-derived constants (e.g. ARIA_BRAIN_QUEUE_MAX)
    # pick up the monkeypatched values for this test.
    _biq = importlib.reload(_biq)
    return _biq


def _run(coro):
    return asyncio.run(coro)


def test_fifo_within_same_priority(tmp_path, monkeypatch):
    biq = _fresh_module(tmp_path, monkeypatch)

    async def scenario():
        await biq.connect()
        try:
            for n in (10, 11, 12):
                await biq.enqueue({"n": n}, priority=2)
            batch = await biq.dequeue_batch(limit=10)
            return [b["payload"]["n"] for b in batch]
        finally:
            await biq.close()

    assert _run(scenario()) == [10, 11, 12]


def test_lower_priority_number_drains_first(tmp_path, monkeypatch):
    biq = _fresh_module(tmp_path, monkeypatch)

    async def scenario():
        await biq.connect()
        try:
            await biq.enqueue({"n": "a"}, priority=2)
            await biq.enqueue({"n": "b"}, priority=0)
            await biq.enqueue({"n": "c"}, priority=1)
            await biq.enqueue({"n": "d"}, priority=0)
            batch = await biq.dequeue_batch(limit=10)
            return [(b["priority"], b["payload"]["n"]) for b in batch]
        finally:
            await biq.close()

    # priority 0 (FIFO: b then d), then 1 (c), then 2 (a)
    assert _run(scenario()) == [(0, "b"), (0, "d"), (1, "c"), (2, "a")]


def test_mark_done_removes(tmp_path, monkeypatch):
    biq = _fresh_module(tmp_path, monkeypatch)

    async def scenario():
        await biq.connect()
        try:
            await biq.enqueue({"n": 1})
            await biq.enqueue({"n": 2})
            batch = await biq.dequeue_batch(limit=10)
            await biq.mark_done([batch[0]["id"]])
            return await biq.stats()
        finally:
            await biq.close()

    s = _run(scenario())
    # one done (row gone), one still processing, nothing pending
    assert s["processing"] == 1
    assert s["depth"] == 0
    assert s["dead_letter"] == 0


def test_mark_failed_retries_then_dlqs(tmp_path, monkeypatch):
    biq = _fresh_module(tmp_path, monkeypatch)

    async def scenario():
        await biq.connect()
        try:
            await biq.enqueue({"n": 1})
            batch = await biq.dequeue_batch(limit=10)
            row = batch[0]
            rid = row["id"]

            # First failure: attempts 0 -> 1, backoff min(60, 2**1)=2s,
            # status flips back to pending (deferred).
            await biq.mark_failed(rid, row["payload"], row["attempts"], "boom",
                                  max_attempts=3)
            async with biq._conn.execute(
                "SELECT attempts, status, next_attempt_at FROM queue WHERE id=?",
                (rid,)) as cur:
                attempts1, status1, next1 = await cur.fetchone()

            # It is deferred: a due-only dequeue should NOT return it yet.
            due_now = await biq.dequeue_batch(limit=10)

            # Second failure (attempts=1 -> 2, still < 3): pending again.
            await biq.mark_failed(rid, row["payload"], attempts1, "boom",
                                  max_attempts=3)
            async with biq._conn.execute(
                "SELECT attempts FROM queue WHERE id=?", (rid,)) as cur:
                attempts2 = (await cur.fetchone())[0]

            # Third failure (attempts=2 -> 3, new==max): dead-lettered.
            await biq.mark_failed(rid, row["payload"], attempts2, "final boom",
                                  max_attempts=3)
            s = await biq.stats()
            async with biq._conn.execute(
                "SELECT COUNT(*) FROM queue WHERE id=?", (rid,)) as cur:
                still_in_queue = (await cur.fetchone())[0]
            return {
                "attempts1": attempts1, "status1": status1, "next1": next1,
                "due_now": due_now, "attempts2": attempts2,
                "dlq": s["dead_letter"], "still_in_queue": still_in_queue,
            }
        finally:
            await biq.close()

    r = _run(scenario())
    assert r["attempts1"] == 1
    assert r["status1"] == "pending"
    assert r["next1"] > 0  # backoff scheduled into the future
    assert r["due_now"] == []  # deferred row is not yet due
    assert r["attempts2"] == 2
    assert r["dlq"] == 1  # dead-lettered at max_attempts
    assert r["still_in_queue"] == 0  # removed from the live queue


def test_recover_stuck_resets_processing(tmp_path, monkeypatch):
    biq = _fresh_module(tmp_path, monkeypatch)

    async def scenario():
        await biq.connect()
        try:
            await biq.enqueue({"n": 1})
            await biq.enqueue({"n": 2})
            await biq.dequeue_batch(limit=10)  # both -> processing
            s_before = await biq.stats()
            recovered = await biq.recover_stuck()
            s_after = await biq.stats()
            return s_before, recovered, s_after
        finally:
            await biq.close()

    s_before, recovered, s_after = _run(scenario())
    assert s_before["processing"] == 2
    assert s_before["depth"] == 0
    assert recovered == 2
    assert s_after["processing"] == 0
    assert s_after["depth"] == 2


def test_enqueue_over_cap_evicts_oldest_lowest_priority(tmp_path, monkeypatch):
    # Cap the queue at 3 so we can drive the eviction path deterministically.
    biq = _fresh_module(tmp_path, monkeypatch, ARIA_BRAIN_QUEUE_MAX=3)
    assert biq._QUEUE_MAX == 3  # reload picked up the env cap

    async def scenario():
        await biq.connect()
        try:
            # Fill to cap with mixed priorities.
            await biq.enqueue({"tag": "p2-old"}, priority=2)   # lowest importance, oldest
            await biq.enqueue({"tag": "p1"}, priority=1)
            await biq.enqueue({"tag": "p0"}, priority=0)       # highest importance
            # Now at cap (3 pending). Next enqueue must evict the oldest-lowest-
            # priority pending row, which is "p2-old".
            await biq.enqueue({"tag": "p2-new"}, priority=2)
            batch = await biq.dequeue_batch(limit=10)
            return [b["payload"]["tag"] for b in batch]
        finally:
            await biq.close()

    tags = _run(scenario())
    assert "p2-old" not in tags  # evicted
    assert set(tags) == {"p0", "p1", "p2-new"}
    # still ordered by priority
    assert tags == ["p0", "p1", "p2-new"]


def test_stats_accurate(tmp_path, monkeypatch):
    biq = _fresh_module(tmp_path, monkeypatch)

    async def scenario():
        await biq.connect()
        try:
            await biq.enqueue({"n": 1}, priority=0)
            await biq.enqueue({"n": 2}, priority=2)
            await biq.enqueue({"n": 3}, priority=2)
            await biq.enqueue({"n": 4}, priority=3)
            s = await biq.stats()
            return s
        finally:
            await biq.close()

    s = _run(scenario())
    assert s["depth"] == 4
    assert s["by_priority"] == {0: 1, 1: 0, 2: 2, 3: 1}
    assert s["processing"] == 0
    assert s["dead_letter"] == 0
    assert s["oldest_age_s"] >= 0.0


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
