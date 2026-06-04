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

R-F1332: dedicated ThreadPoolExecutor for the semantic index queue.
Pre-R-F1332, _process_batch used asyncio.to_thread() which uses the
PROCESS-WIDE default ThreadPoolExecutor. With _ABSORB_CONCURRENCY=16
and every absorb potentially triggering a knowledge.store_fact →
enqueue → worker batch → to_thread, the 8-worker pool (4-vCPU Fly
machine) saturated: 35% CPU in thread._worker, 23% in aiosqlite,
event loop GIL-starved, heartbeats went stale every 300s. The
dedicated executor has 2 workers — enough for one encode batch at a
time with headroom, but never enough to starve the rest of the
process.

Operator knobs:
  ARIA_INDEX_QUEUE_MAX                queue capacity (default 1000)
  ARIA_INDEX_QUEUE_BATCH_SIZE         max items per encode batch (default 32)
  ARIA_INDEX_QUEUE_BATCH_WINDOW_MS    max ms to wait for batch fill (default 100)
  ARIA_INDEX_QUEUE_DISABLED           if "1", disables queueing — old
                                       synchronous path (legacy fallback)
  ARIA_INDEX_QUEUE_WORKERS            dedicated thread pool workers (default 2)
"""
from __future__ import annotations

import asyncio
import logging
import os
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Optional

logger = logging.getLogger("aria.semantic_index_queue")

_MAX_QUEUE_SIZE = int(os.environ.get("ARIA_INDEX_QUEUE_MAX", "1000"))
_BATCH_SIZE = int(os.environ.get("ARIA_INDEX_QUEUE_BATCH_SIZE", "32"))
_BATCH_WINDOW_MS = int(os.environ.get("ARIA_INDEX_QUEUE_BATCH_WINDOW_MS", "100"))
_DRAIN_TIMEOUT_S = 10.0  # how long to wait for first item before idling
# R-F987 (2026-05-28) — yield the shared encode lock to interactive chats. A
# batch's model.encode contends the SAME process-wide _encode_lock as the chat
# RAG query (semantic_search._safe_encode); a 32-item batch can hold it for
# seconds, stalling an in-flight chat behind it (the encode-lock contention the
# 360 latency review surfaced). When a chat is interactive-active (R-F860
# brain_hook.mark_interactive, 8s window) the worker defers its already-assembled
# batch in short steps so the query encodes first. Items are HELD, never dropped;
# the deferral is capped so background indexing can't starve.
_INTERACTIVE_DEFER_MAX_S = float(os.environ.get("ARIA_INDEX_INTERACTIVE_DEFER_MAX_S", "5"))
_INTERACTIVE_DEFER_STEP_S = float(os.environ.get("ARIA_INDEX_INTERACTIVE_DEFER_STEP_S", "0.25"))

# R-F1332: dedicated thread pool for semantic index encodes. Prevents
# saturation of the process-wide default ThreadPoolExecutor (8 workers
# on 4-vCPU Fly machine) by encode batches. 2 workers is enough for
# one active batch + one queued; the queue itself provides backpressure.
_INDEX_EXECUTOR_WORKERS = int(os.environ.get("ARIA_INDEX_QUEUE_WORKERS", "2"))
_index_executor: Optional[ThreadPoolExecutor] = None


def _get_index_executor() -> ThreadPoolExecutor:
    """Lazy-init the dedicated thread pool for semantic index encodes."""
    global _index_executor
    if _index_executor is None:
        _index_executor = ThreadPoolExecutor(
            max_workers=_INDEX_EXECUTOR_WORKERS,
            thread_name_prefix="semantic_index",
        )
    return _index_executor


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

        # R-F987 — let an in-flight chat win the encode lock before this batch.
        await _yield_to_interactive()

        try:
            await _process_batch(batch)
            _indexed_total += len(batch)
            _batches_total += 1
        except Exception as e:
            logger.warning(
                "R-F807 batch process failed (%d items): %s",
                len(batch), e,
            )


async def _yield_to_interactive() -> float:
    """R-F987 — block while a chat is interactive-active (R-F860), up to
    _INTERACTIVE_DEFER_MAX_S, so the chat's RAG-query encode wins the shared
    encode lock before this background batch contends it. Returns seconds
    deferred. Best-effort: never raises (indexing must never break)."""
    deferred = 0.0
    try:
        from .brain_hook import _interactive_active
        while _interactive_active() and deferred < _INTERACTIVE_DEFER_MAX_S:
            await asyncio.sleep(_INTERACTIVE_DEFER_STEP_S)
            deferred += _INTERACTIVE_DEFER_STEP_S
    except Exception:
        pass
    return deferred


async def _process_batch(batch: list[tuple[str, str, dict]]) -> None:
    """Index a batch of facts. Imported lazily so this module loads
    without forcing semantic_search (which imports torch) on every
    boot of every other module.

    R-F1332: uses the dedicated _index_executor instead of the process-wide
    default ThreadPoolExecutor (asyncio.to_thread). Prevents encode batches
    from starving other to_thread callers (knowledge scans, RAG queries)."""
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

    # Use the dedicated executor so encode work doesn't compete with
    # other asyncio.to_thread callers (knowledge scans, RAG queries).
    loop = asyncio.get_running_loop()
    await loop.run_in_executor(_get_index_executor(), _persist_all)


def get_stats() -> dict:
    """Surface stats for /api/aria/brain/stats and operator dashboards."""
    # R-F996 — wire to brain
    from .engine_wiring import wire_success
    wire_success(
        module="_semantic_index_queue",
        summary="Get Stats",
        source_id="_semantic_index_queue:R-F996",
    )

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
        "dedicated_executor_workers": _INDEX_EXECUTOR_WORKERS,  # R-F1332
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

    # R-F1332: shutdown the dedicated executor
    global _index_executor
    if _index_executor is not None:
        _index_executor.shutdown(wait=False)
        _index_executor = None

    return {"drained": drained, "remaining": remaining}
