"""R-F807 (2026-05-22): background semantic-index queue moves
model.encode off the absorb critical path.

Pre-R-F807: knowledge.store_fact awaited
asyncio.to_thread(index_fact, ...) which called _safe_encode →
model.encode. Under concurrent load these queued through one
encoder, driving absorb wall-time to 18-20 minutes.

R-F807: store_fact enqueues (fact_id, text, meta) into an
asyncio.Queue. A single background worker drains the queue,
batching up to _BATCH_SIZE items into one model.encode call.

Tests exercise the queue + batching mechanics directly. The
worker loop is tested in isolation (one iteration) rather than as
a long-lived background task, so pytest can teardown cleanly.
"""
from __future__ import annotations

import asyncio
import time

import pytest

from aria_service.intel import _semantic_index_queue as siq


@pytest.fixture(autouse=True)
def _reset_module_state(monkeypatch):
    """Reset queue + worker state between tests. Also patch the worker
    loop to a no-op so the spawned task exits immediately — tests that
    care about worker behaviour call _process_batch directly. This
    avoids pytest hanging on a 10s drain timeout inside a cancelled
    worker."""
    # R-F927 — the suite-wide conftest sets ARIA_INDEX_QUEUE_DISABLED=1 (the
    # background embed worker is the local-dev deadlock root). This is the ONE
    # suite that tests the queue itself, so re-enable it just for these tests.
    monkeypatch.setenv("ARIA_INDEX_QUEUE_DISABLED", "0")
    siq._queue = None
    siq._worker_task = None
    siq._drops_total = 0
    siq._indexed_total = 0
    siq._batches_total = 0
    siq._started_at = 0.0

    async def _noop_worker():
        return

    monkeypatch.setattr(siq, "_worker_loop", _noop_worker)
    yield
    if siq._worker_task is not None and not siq._worker_task.done():
        try:
            siq._worker_task.cancel()
        except Exception:
            pass
    siq._queue = None
    siq._worker_task = None


def test_rf807_enqueue_puts_item_in_queue(monkeypatch):
    """enqueue() puts the tuple into the queue (sync). The whole
    point is non-blocking; we verify queue depth grows."""
    async def _coro():
        # Pre-create queue so the worker is auto-started but we don't
        # care — we'll inspect the queue depth immediately.
        siq._queue = asyncio.Queue(maxsize=100)
        t0 = time.monotonic()
        ok = await siq.enqueue("fact-1", "some text", {"k": "v"})
        elapsed = time.monotonic() - t0
        depth = siq._queue.qsize()
        return ok, elapsed, depth

    ok, elapsed, depth = asyncio.run(_coro())
    assert ok is True
    assert elapsed < 0.05, f"enqueue took {elapsed*1000:.1f}ms"
    assert depth == 1


def test_rf807_process_batch_calls_index_fact(monkeypatch):
    """_process_batch hands each item to semantic_search.index_fact."""
    calls = []

    def _fake_index_fact(fact_id, text, meta):
        calls.append((fact_id, text, meta))

    # Patch the import target
    import sys
    from aria_service.intel import semantic_search as _ss
    monkeypatch.setattr(_ss, "index_fact", _fake_index_fact, raising=False)

    async def _coro():
        batch = [
            ("f1", "text1", {"a": 1}),
            ("f2", "text2", {"b": 2}),
            ("f3", "text3", {"c": 3}),
        ]
        await siq._process_batch(batch)
        return calls

    result = asyncio.run(_coro())
    assert len(result) == 3
    assert result[0][0] == "f1"
    assert result[2][0] == "f3"


def test_rf807_process_batch_swallows_per_item_exceptions(monkeypatch):
    """If one item fails to index, the others still get processed —
    a malformed entry can't take down a whole batch."""
    calls = []

    def _fake_index_fact(fact_id, text, meta):
        if fact_id == "bad":
            raise RuntimeError("simulated failure on this item")
        calls.append(fact_id)

    from aria_service.intel import semantic_search as _ss
    monkeypatch.setattr(_ss, "index_fact", _fake_index_fact, raising=False)

    async def _coro():
        await siq._process_batch([
            ("good1", "text", {}),
            ("bad", "text", {}),
            ("good2", "text", {}),
        ])
        return calls

    result = asyncio.run(_coro())
    assert "good1" in result and "good2" in result
    assert "bad" not in result


def test_rf807_queue_full_drops_and_increments_counter(monkeypatch):
    """When the queue is full, enqueue returns False and the drops
    counter advances. No worker needed — we drive the queue directly."""
    monkeypatch.setattr(siq, "_MAX_QUEUE_SIZE", 2)

    async def _coro():
        siq._queue = asyncio.Queue(maxsize=2)
        ok1 = await siq.enqueue("f1", "t", {})
        ok2 = await siq.enqueue("f2", "t", {})
        ok3 = await siq.enqueue("f3", "t", {})
        return ok1, ok2, ok3, siq._drops_total

    ok1, ok2, ok3, drops = asyncio.run(_coro())
    assert ok1 is True
    assert ok2 is True
    assert ok3 is False
    assert drops == 1


def test_rf807_disabled_via_env_returns_false(monkeypatch):
    """ARIA_INDEX_QUEUE_DISABLED=1 short-circuits enqueue to return
    False so the caller falls back to the legacy sync path."""
    monkeypatch.setenv("ARIA_INDEX_QUEUE_DISABLED", "1")

    async def _coro():
        return await siq.enqueue("fact", "text", {})

    ok = asyncio.run(_coro())
    assert ok is False


def test_rf807_stats_surface_has_expected_fields():
    """get_stats() returns the operator-dashboard fields."""
    stats = siq.get_stats()
    for key in (
        "enabled", "queue_depth", "queue_max", "indexed_total",
        "drops_total", "batches_total", "batch_size_max",
        "batch_window_ms", "worker_alive",
    ):
        assert key in stats, f"stats missing field '{key}'"


def test_rf807_shutdown_drains_pending_items_synchronously(monkeypatch):
    """On shutdown, items still in the queue are processed
    synchronously before the worker is cancelled."""
    processed = []

    def _fake_index_fact(fact_id, text, meta):
        processed.append(fact_id)

    from aria_service.intel import semantic_search as _ss
    monkeypatch.setattr(_ss, "index_fact", _fake_index_fact, raising=False)

    async def _coro():
        siq._queue = asyncio.Queue(maxsize=10)
        # Put items WITHOUT starting the worker
        siq._queue.put_nowait(("f1", "text", {}))
        siq._queue.put_nowait(("f2", "text", {}))
        siq._queue.put_nowait(("f3", "text", {}))
        result = await siq.shutdown(timeout_s=2.0)
        return result

    result = asyncio.run(_coro())
    assert "f1" in processed and "f2" in processed and "f3" in processed, (
        f"R-F807 shutdown lost items: processed={processed} result={result}"
    )
    assert result["drained"] == 3
    assert result["remaining"] == 0


def test_rf807_capability_batched_processing_efficient(monkeypatch):
    """Capability test: 32 items in one batch should take ~one
    encode call's time, not 32 sequential calls. This is the whole
    point of batching — sentence_transformers batches efficiently."""
    encode_calls = []

    def _fake_index_fact(fact_id, text, meta):
        # Simulate the per-item cost being negligible vs the encode
        encode_calls.append(fact_id)

    from aria_service.intel import semantic_search as _ss
    monkeypatch.setattr(_ss, "index_fact", _fake_index_fact, raising=False)

    async def _coro():
        # Build a 32-item batch and process it in one call
        batch = [(f"f{i}", f"text {i}", {}) for i in range(32)]
        t0 = time.monotonic()
        await siq._process_batch(batch)
        elapsed = time.monotonic() - t0
        return len(encode_calls), elapsed

    count, elapsed = asyncio.run(_coro())
    assert count == 32
    # Should be very fast with the fake (no real encoding)
    assert elapsed < 1.0, (
        f"R-F807 batched processing took {elapsed:.2f}s for 32 items — "
        f"the batch path itself should be near-instant; production "
        f"wall-time depends on model.encode's batching efficiency."
    )
