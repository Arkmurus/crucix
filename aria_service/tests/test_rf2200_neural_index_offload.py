"""R-F2200 — warmup off-load: incremental neural index rebuild.

The boot warmup rebuilt the neuron inverted-index synchronously
(_rebuild_neuron_index iterates ~1.2M neurons doing dict inserts) ON the event
loop — a GIL-bound burst that stalled the loop (live: 8.8s event-loop stalls
during warmup), which a to_thread wrap would NOT fix (the worker holds the GIL).

Fix: _rebuild_neuron_index_incremental() yields to the loop every `chunk`
neurons and swaps the globals atomically. These capability tests drive the REAL
rebuild and assert (a) the loop stays responsive during it, and (b) the index
is built correctly (atomic swap), matching the sync version.
"""
from __future__ import annotations

import asyncio
import time

import pytest

from aria_service.intel import neural_memory  # full name: R-F1958 hook mis-resolves short aliases


def _populate(n: int) -> None:
    neural_memory._neurons.clear()
    for i in range(n):
        neural_memory._neurons[f"n{i}"] = {"id": f"n{i}", "concept": f"concept word{i % 200} term{i}"}


async def _max_stall_during(coro) -> tuple[float, float]:
    """Run `coro` while a 5ms heartbeat records the largest gap between ticks
    (a proxy for event-loop stall). Returns (max_gap_s, total_s)."""
    max_gap = 0.0
    done = {"stop": False}

    async def heartbeat():
        last = time.monotonic()
        while not done["stop"]:
            await asyncio.sleep(0.005)
            now = time.monotonic()
            nonlocal max_gap
            max_gap = max(max_gap, now - last)
            last = now

    hb = asyncio.create_task(heartbeat())
    t0 = time.monotonic()
    await coro
    total = time.monotonic() - t0
    done["stop"] = True
    await asyncio.sleep(0.02)
    hb.cancel()
    return max_gap, total


def test_rf2200_incremental_rebuild_keeps_loop_responsive():
    """The core claim: indexing a large neuron set must NOT stall the loop."""
    _populate(120_000)

    async def run():
        return await _max_stall_during(neural_memory._rebuild_neuron_index_incremental(chunk=2000))

    max_gap, total = asyncio.run(run())
    assert neural_memory._concept_to_id, "index must be populated after the rebuild"
    assert max_gap < 0.25, (
        f"event loop stalled {max_gap*1000:.0f}ms during the incremental rebuild "
        f"(must be <250ms; total build {total*1000:.0f}ms)"
    )
    # Yields actually happened: the worst single stall is a small fraction of the
    # whole build (a non-yielding sync build would have max_gap ~= total).
    assert max_gap < total * 0.6 + 0.05, (
        f"rebuild does not appear to yield (max_gap {max_gap*1000:.0f}ms vs total "
        f"{total*1000:.0f}ms)"
    )


def test_rf2200_incremental_matches_sync_index():
    """Correctness: the incremental+swap result is identical to the sync rebuild."""
    _populate(5_000)
    neural_memory._rebuild_neuron_index()                       # sync
    sync_concepts = dict(neural_memory._concept_to_id)
    sync_words = {w: set(ids) for w, ids in neural_memory._word_to_ids.items()}

    asyncio.run(neural_memory._rebuild_neuron_index_incremental(chunk=500))   # incremental
    assert dict(neural_memory._concept_to_id) == sync_concepts, "concept index differs from sync"
    assert {w: set(ids) for w, ids in neural_memory._word_to_ids.items()} == sync_words, \
        "word index differs from sync"


def test_rf2200_atomic_swap_no_empty_window():
    """The swap must never expose an EMPTY index: a reader that runs concurrently
    with the rebuild always sees a fully-populated index (old until the swap),
    never the empty mid-rebuild state the sync .clear() exposed."""
    _populate(40_000)
    neural_memory._rebuild_neuron_index()                       # seed a full index
    seen_sizes = []

    async def reader():
        for _ in range(50):
            seen_sizes.append(len(neural_memory._concept_to_id))
            await asyncio.sleep(0.002)

    async def run():
        await asyncio.gather(
            neural_memory._rebuild_neuron_index_incremental(chunk=1000),
            reader(),
        )

    asyncio.run(run())
    assert seen_sizes, "reader did not run"
    assert min(seen_sizes) > 0, (
        f"reader observed an EMPTY index mid-rebuild (min size {min(seen_sizes)}) "
        "— the atomic swap must never expose an empty window"
    )
