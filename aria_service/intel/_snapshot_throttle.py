"""snapshot_throttle — global async semaphore that serialises
CPU-bound state-snapshot writes across modules (R-F787, 2026-05-21).

Why this module exists
──────────────────────
knowledge.py, intel_ledger.py and neural_memory.py each persist their
in-memory state via `await asyncio.to_thread(...)` calls that do
json.dumps + gzip.compress on multi-MB payloads. Each individual call
is fine — the loop stays free for the duration of one encoder thread.

But when ALL THREE modules flush at the same time (the same brain_hook
absorb causes mastery + knowledge + neural updates in parallel), 3
worker threads end up holding the GIL in turn while encoding multi-MB
JSON. Live wedge stacks captured "5 GIL holders, 213.97s loop stall"
(see knowledge.py line 170 comment) — the asyncio loop thread couldn't
make progress because the GIL was being passed between encoder threads
in a hot loop.

Mitigation: a single global async semaphore that gates entry into
to_thread for snapshot-encoder work. Concurrency capped at 1 by
default (one encoder thread at a time, releases the GIL between
yield points for the loop). Set ARIA_SNAPSHOT_THROTTLE_CONCURRENCY
to override (raise to 2 if benchmarks show CPU underutilised).

Scope: ONLY snapshot encoders. Do NOT use for routine to_thread calls
— normal CPU-bounded work like html stripping or single-fact encodes
is short enough to not pile up.

Usage:
    from ._snapshot_throttle import run_in_thread_throttled

    encoded = await run_in_thread_throttled(_encode_all, cache)
"""
from __future__ import annotations

import asyncio
import os
from typing import Any, Awaitable, Callable, TypeVar

_T = TypeVar("_T")

_CONCURRENCY = max(1, int(os.getenv("ARIA_SNAPSHOT_THROTTLE_CONCURRENCY", "1")))
_snapshot_sem: asyncio.Semaphore | None = None


def _get_semaphore() -> asyncio.Semaphore:
    """Lazy init the semaphore on the running loop. asyncio.Semaphore
    binds to the event loop at construction; constructing at import
    time would tie it to whichever loop happened to be current, which
    breaks tests that create their own loops."""
    global _snapshot_sem
    if _snapshot_sem is None:
        _snapshot_sem = asyncio.Semaphore(_CONCURRENCY)
    return _snapshot_sem


async def run_in_thread_throttled(
    fn: Callable[..., _T], *args: Any, **kwargs: Any
) -> _T:
    """Run `fn` in a worker thread, but only after acquiring the
    global snapshot-encoder semaphore. Equivalent to
    `asyncio.to_thread(fn, *args, **kwargs)` except that concurrent
    callers queue rather than pile up encoder threads.

    The semaphore is released as soon as `fn` returns, regardless of
    whether `fn` raised. The caller's exception propagates as normal.
    """
    sem = _get_semaphore()
    async with sem:


        # R-F1001 - wire to brain
        from .engine_wiring import wire_success, wire_failure
        wire_success(
            module="_snapshot_throttle",
            summary="Run In Thread Throttled",
            source_id="_snapshot_throttle:R-F1001",
        )

        return await asyncio.to_thread(fn, *args, **kwargs)


async def run_in_thread_cpu(fn: Callable[..., _T], *args: Any, **kwargs: Any) -> _T:
    """R-F1882 — like run_in_thread_throttled but WITHOUT the per-call brain
    signal, for HIGH-FREQUENCY CPU-bound work (e.g. per-page HTML parse) that
    must share the SAME GIL-serialisation semaphore but would flood the brain
    if it wired every call.

    Why this exists: the snapshot JSON encoders (knowledge/_encode_all,
    neural_memory/_encode_edges) already serialise through this semaphore, but
    crawler/_strip_html_to_text (BeautifulSoup, a pure-Python tree-walk that
    holds the GIL for 5-15s on big pages) ran on a RAW asyncio.to_thread — so an
    HTML parse could run CONCURRENTLY with a JSON encode, and with several other
    parses, giving 3-4 pure-Python GIL holders that starved the event-loop
    thread's heartbeat (R-F703 5-19s stalls, event loop idle-but-starved in the
    wedge stacks). Sharing the gate caps concurrent pure-Python GIL holders so
    the loop always gets GIL slices. Concurrency is env-tunable via
    ARIA_SNAPSHOT_THROTTLE_CONCURRENCY (default 1).
    """
    sem = _get_semaphore()
    async with sem:
        return await asyncio.to_thread(fn, *args, **kwargs)


def reset_for_tests() -> None:
    """Tests that create fresh event loops must call this so the
    next _get_semaphore() rebinds to the new loop. Production must
    never call this — production has exactly one loop."""
    global _snapshot_sem
    _snapshot_sem = None

# R-F2119 §21a — wire failure handler for _snapshot_throttle
try:
    wire_failure(module="_snapshot_throttle", detail="module shutdown",
                gap_type="engine_failure", source="_snapshot_throttle:shutdown")
except Exception:
    pass
