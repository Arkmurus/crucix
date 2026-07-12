"""R-F2564 — N parallel brain-ingest drain workers.

Capability test: with N workers fanned out over ONE queue connection, every
enqueued row is applied EXACTLY once (dequeue_batch's atomic claim prevents
double-apply) and NONE is lost. This is the property that makes parallelizing
the drain safe (design-doc P1.1).
"""
from __future__ import annotations

import asyncio
import os

from aria_service.intel import brain_hook as bh
from aria_service.intel import brain_ingest_queue as biq


def test_n_workers_drain_each_row_exactly_once(monkeypatch, tmp_path):
    monkeypatch.setenv("ARIA_BRAIN_QUEUE_DB", str(tmp_path / "q.db"))
    applied: list[int] = []

    # Stub the heavy apply (absorb_tiers_bg) with a cheap recorder that marks done —
    # we are testing the claim/fan-out contract, not the absorb internals.
    async def _fake_drain_one(row: dict) -> None:
        await asyncio.sleep(0)                 # yield so workers actually interleave
        applied.append(int(row["id"]))
        await biq.mark_done([row["id"]])

    monkeypatch.setattr(bh, "_drain_one_queued", _fake_drain_one)

    N_ROWS = 60
    N_WORKERS = 4

    async def go() -> dict:
        await biq.connect()
        for i in range(N_ROWS):
            await biq.enqueue({"n": i}, priority=(i % 4))   # spread across priorities
        sem = asyncio.Semaphore(3)
        workers = [
            asyncio.create_task(bh._drain_worker_body(w, sem, 5))
            for w in range(N_WORKERS)
        ]
        # Drain until the queue is fully empty (pending + processing == 0).
        for _ in range(500):
            s = await biq.stats()
            if s["depth"] == 0 and s["processing"] == 0:
                break
            await asyncio.sleep(0.01)
        for t in workers:
            t.cancel()
        await asyncio.gather(*workers, return_exceptions=True)
        final = await biq.stats()
        await biq.close()
        return final

    final = asyncio.run(go())

    assert len(applied) == N_ROWS, f"expected {N_ROWS} applied, got {len(applied)}"
    assert len(set(applied)) == N_ROWS, "a row was applied more than once (double-claim!)"
    assert final["depth"] == 0 and final["processing"] == 0, "queue not fully drained"
    assert final["dead_letter"] == 0, "no row should have dead-lettered"


def test_single_worker_path_still_drains(monkeypatch, tmp_path):
    """Default ARIA_BRAIN_QUEUE_WORKERS=1 path (the unchanged single-worker branch)."""
    monkeypatch.setenv("ARIA_BRAIN_QUEUE_DB", str(tmp_path / "q1.db"))
    applied: list[int] = []

    async def _fake_drain_one(row: dict) -> None:
        applied.append(int(row["id"]))
        await biq.mark_done([row["id"]])

    monkeypatch.setattr(bh, "_drain_one_queued", _fake_drain_one)

    async def go() -> int:
        await biq.connect()
        for i in range(10):
            await biq.enqueue({"n": i})
        sem = asyncio.Semaphore(3)
        w = asyncio.create_task(bh._drain_worker_body(0, sem, 12))
        for _ in range(200):
            s = await biq.stats()
            if s["depth"] == 0 and s["processing"] == 0:
                break
            await asyncio.sleep(0.01)
        w.cancel()
        await asyncio.gather(w, return_exceptions=True)
        await biq.close()
        return len(applied)

    assert asyncio.run(go()) == 10


if __name__ == "__main__":
    import sys
    import pytest
    sys.exit(pytest.main([__file__, "-v"]))
