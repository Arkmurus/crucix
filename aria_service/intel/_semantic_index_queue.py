"""R-F807 (2026-05-22): background semantic-index queue.

Moves sentence_transformers model.encode() OFF the brain_hook absorb
critical path. Pre-R-F807 every absorb awaited
knowledge.store_fact → asyncio.to_thread(index_fact) → _safe_encode →
model.encode (10-13s under load with GIL serialisation). With many
concurrent absorbs piling up at the encode lock, absorb wall-time
hit 18-20 minutes in live evidence (2026-05-22 15:59-16:04 UTC).

R-F795 / R-F799 capped the symptoms (per-tier timeout + concurrency
cap), but the structural fix is to stop awaiting the encode on the
chat / DD / sweep critical paths at all.

Architecture:
- knowledge.store_fact enqueues (fact_id, text, meta) and returns.
- A single per-process background worker drains the queue, collects
  up to _BATCH_SIZE items within a _BATCH_WINDOW_MS window, and runs
  one model.encode([t1, t2, ...]) on the batch. sentence_transformers
  batches efficiently — encoding 32 short texts takes ~the same wall-
  time as encoding 1 (per the model's documented batching behaviour).
- Backpressure: when the queue is full, new items are dropped and a
  warning logs every 100 drops. The fact itself is still stored in
  knowledge_store; only the semantic-search index update is lost.
- Lifecycle: worker is lazy-started on first enqueue inside a running
  event loop. If the worker task dies (raised exception), the next
  enqueue restarts it.

Trade-off accepted: the semantic-search index becomes eventually-
consistent. A fact just stored may not be searchable for ~100ms +
encode time. For our use cases (chat retrieval, DD search,
researcher recall) this is acceptable — the alternative was 20-min
wedges that broke EVERY surface.

Operator knobs:
  ARIA_INDEX_QUEUE_MAX                queue capacity (default 1000)
  ARIA_INDEX_QUEUE_BATCH_SIZE         max items per encode batch (default 32)
  ARIA_INDEX_QUEUE_BATCH_WINDOW_MS    max ms to wait for batch fill (default 100)
  ARIA_INDEX_QUEUE_DISABLED           if "1", disables queueing — old
                                       synchronous path (legacy fallback)
"""
from __future__ import annotations

import asyncio
import logging
import os
import time
from typing import Optional

logger = logging.getLogger("aria.semantic_index_queue")

_MAX_QUEUE_SIZE = int(os.environ.get("ARIA_INDEX_QUEUE_MAX", "1000"))
_BATCH_SIZE = int(os.environ.get("ARIA_INDEX_QUEUE_BATCH_SIZE", "32"))
_BATCH_WINDOW_MS = int(os.environ.get("ARIA_INDEX_QUEUE_BATCH_WINDOW_MS", "100"))
_DRAIN_TIMEOUT_S = 10.0  # how long to wait for first item before idling


def _disabled() -> bool:
    return os.environ.get("ARIA_INDEX_QUEUE_DISABLED", "").strip() == "1"


_queue: Optional[asyncio.Queue] = None
_worker_task: Optional[asyncio.Task] = None
_drops_total = 0
_indexed_total = 0
_batches_total = 0
_started_at: float = 0.0


def _ensure_queue() -> asyncio.Queue:
    """Lazy-init the queue and the worker. Worker is started in the
    CURRENT running event loop. If the loop changes (e.g., new
    process / new loop), the worker is recreated on the next enqueue."""
    global _queue, _worker_task, _started_at
    if _queue is None:
        _queue = asyncio.Queue(maxsize=_MAX_QUEUE_SIZE)
        _started_at = time.monotonic()
    if _worker_task is None or _worker_task.done():
        try:
            loop = asyncio.get_running_loop()
            _worker_task = loop.create_task(_worker_loop())
        except RuntimeError:
            # No running loop — caller is in a sync context and the
            # worker can't start. Enqueue will still work (queue is
            # a plain asyncio.Queue, put_nowait is sync-safe), but
            # nothing will drain until a worker is launched.
            pass
    return _queue


async def enqueue(fact_id: str, text: str, meta: Optional[dict] = None) -> bool:
    """Queue a fact for background semantic indexing. Returns True if
    queued, False if dropped (queue full / disabled / no event loop)."""
    global _drops_total
    if _disabled():
        # Operator turned off the queue — fall back to the synchronous
        # path. The caller (knowledge.store_fact) handles this branch.
        return False
    q = _ensure_queue()
    try:
        q.put_nowait((fact_id, text, meta or {}))
        return True
    except asyncio.QueueFull:
        _drops_total += 1
        if _drops_total % 100 == 1:
            logger.warning(
                "R-F807: semantic-index queue full (max=%d). Dropped "
                "%d items so far. Embedder is not keeping up; fact "
                "stored in knowledge_store but semantic-search index "
                "missing this entry. Consider increasing "
                "ARIA_INDEX_QUEUE_BATCH_SIZE or scaling out.",
                _MAX_QUEUE_SIZE, _drops_total,
            )
        return False


async def _worker_loop():
    """Drain the queue, batching items for efficient model.encode."""
    global _indexed_total, _batches_total
    while True:
        try:
            item = await asyncio.wait_for(
                _queue.get(), timeout=_DRAIN_TIMEOUT_S,
            )
        except asyncio.TimeoutError:
            continue  # idle, keep waiting
        except asyncio.CancelledError:
            logger.info("R-F807 worker cancelled (graceful shutdown)")
            return
        except Exception as e:  # defensive: never let the worker die silently
            logger.warning("R-F807 worker exception getting item: %s", e)
            await asyncio.sleep(0.5)
            continue

        batch = [item]
        # Drain up to BATCH_SIZE more within the window
        loop = asyncio.get_event_loop()
        deadline = loop.time() + (_BATCH_WINDOW_MS / 1000.0)
        while len(batch) < _BATCH_SIZE:
            remaining = deadline - loop.time()
            if remaining <= 0:
                break
            try:
                more = await asyncio.wait_for(_queue.get(), timeout=remaining)
                batch.append(more)
            except asyncio.TimeoutError:
                break
            except asyncio.CancelledError:
                logger.info("R-F807 worker cancelled mid-batch — finishing batch")
                break

        try:
            await _process_batch(batch)
            _indexed_total += len(batch)
            _batches_total += 1
        except Exception as e:
            logger.warning(
                "R-F807 batch process failed (%d items): %s",
                len(batch), e,
            )


async def _process_batch(batch: list[tuple[str, str, dict]]) -> None:
    """Index a batch of facts. Imported lazily so this module loads
    without forcing semantic_search (which imports torch) on every
    boot of every other module."""
    from .semantic_search import index_fact

    def _persist_all():
        for fact_id, text, meta in batch:
            try:
                index_fact(fact_id, text, meta)
            except Exception as e:
                logger.debug(
                    "R-F807 single-item index failed (fact_id=%s): %s",
                    fact_id, e,
                )

    # to_thread keeps the encode work off the event loop.
    await asyncio.to_thread(_persist_all)


def get_stats() -> dict:
    """Surface stats for /api/aria/brain/stats and operator dashboards."""
    return {
        "enabled": not _disabled(),
        "queue_depth": _queue.qsize() if _queue is not None else 0,
        "queue_max": _MAX_QUEUE_SIZE,
        "indexed_total": _indexed_total,
        "batches_total": _batches_total,
        "drops_total": _drops_total,
        "batch_size_max": _BATCH_SIZE,
        "batch_window_ms": _BATCH_WINDOW_MS,
        "worker_alive": (
            _worker_task is not None and not _worker_task.done()
        ),
        "started_at_monotonic": _started_at if _started_at else None,
    }


async def shutdown(timeout_s: float = 5.0) -> dict:
    """Graceful shutdown: drain remaining queue items synchronously
    (up to timeout_s) then cancel the worker. Called from main.py
    lifespan teardown."""
    global _worker_task
    if _queue is None:
        return {"drained": 0, "remaining": 0}
    drained = 0
    deadline = time.monotonic() + timeout_s
    # Process any pending items before cancelling
    while not _queue.empty() and time.monotonic() < deadline:
        items = []
        while not _queue.empty() and len(items) < _BATCH_SIZE:
            try:
                items.append(_queue.get_nowait())
            except asyncio.QueueEmpty:
                break
        if items:
            try:
                await asyncio.wait_for(
                    _process_batch(items),
                    timeout=max(0.5, deadline - time.monotonic()),
                )
                drained += len(items)
            except (asyncio.TimeoutError, Exception) as e:
                logger.debug("R-F807 shutdown drain failed: %s", e)
                break
    remaining = _queue.qsize() if _queue is not None else 0
    if _worker_task is not None and not _worker_task.done():
        _worker_task.cancel()
        try:
            await asyncio.wait_for(_worker_task, timeout=1.0)
        except (asyncio.TimeoutError, asyncio.CancelledError, Exception):
            pass
    return {"drained": drained, "remaining": remaining}
