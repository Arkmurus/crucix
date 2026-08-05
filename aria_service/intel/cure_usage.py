"""R-F3730 — Cure Protocol Phase 0.3 runtime usage observation.

WHY THIS EXISTS
---------------
The Phase 0.2 census identified 109 DEAD-CANDIDATE modules. Not one of them is
deletable, because the Phase 4.1 three-proof rule needs static + runtime + test
and the runtime proof does not exist: a repo-wide search found that NONE of the
782 FastAPI or 536 Express routes records that it was called. `main.py` had
exactly one HTTP middleware and it was a body-size cap.

Phase 0.3 requires overlaying real production usage onto the census. That window
CANNOT be reconstructed retrospectively — Fly's log retention is short and is not
an access record — so the clock only starts once this is live.

DESIGN CONSTRAINTS (this runs on every request to a single-process brain)
------------------------------------------------------------------------
1. **No I/O on the request path.** `record_route()` is sync, in-memory, O(1).
   The durable write happens on a coalesced background flush.
2. **Never raise.** Observability must not be able to break a request. Every
   entry point is wrapped; failures are counted, not propagated.
3. **No new background task.** A flush is scheduled opportunistically from the
   middleware and guarded by an in-flight flag. Registering a respawning task
   for what is effectively a one-shot is the exact R-F2668 trap: a one-shot
   registered with respawn made its NORMAL completion look like a death, got
   re-spawned 5x, and its ERROR reset the gate-#3 clean-day streak every boot.
4. **Bounded cardinality.** Keyed on the ROUTE TEMPLATE (`/api/aria/dd/{id}`),
   never the raw path, so an unbounded id space cannot explode the key set.
5. **No TTL.** CLAUDE.md §7 — ARIA's memory does not expire, and a 14-day
   observation window must survive restarts and redeploys.

STORE DECLARATION (R-F3736 — ENGINEERING BRIEF invariant 10)
------------------------------------------------------------
Invariant 10: *"No new store without ownership, retention, erasure, backup, and
recovery rules."* R-F3730 created two keys and declared none of them; this block
is that declaration, added on a self-audit against the brief.

* **Keys.** ``crucix:cure:usage_routes`` (hash: ``"<METHOD> <template>" -> count``)
  and ``crucix:cure:usage_meta`` (json: flush bookkeeping).
* **Owner.** ``aria_service.intel.cure_usage`` — sole writer. `aria-intel` runs on
  a single attached volume and is single-writer by design (brief §11), so no
  cross-writer coordination is required.
* **Retention class.** Operational telemetry, no legal basis needed. Retained for
  the Cure Protocol Phase 0.3 window and its analysis; **deletable in full**
  once Phase 4 closes. No TTL by design — a TTL would silently truncate the
  observation window and corrupt the deletion evidence.
* **Personal data / erasure.** **NONE, structurally.** Only the ROUTE TEMPLATE is
  recorded (``/api/aria/dd/{id}``), never the resolved path, query string, body,
  headers, caller identity or IP. There is no data subject, so no subject-lookup
  or erasure path is required. **This property is load-bearing** — keying on the
  raw path would capture identifiers and turn a counter into a personal-data
  store with all of invariant 10's obligations. The `MAX_TRACKED` cap and the
  template-only key exist for that reason as much as for cardinality.
* **Backup / recovery.** Covered by the existing `/data` volume backup; no
  separate mechanism. **Loss is tolerable and fails safe in the honest
  direction**: losing counts makes modules look LESS observed, which blocks
  deletion rather than authorising it. Recovery is to restart the window.
* **Model context / training exports.** **Never.** This data is not eligible for
  RAG ingestion, prompt context, or a training export. It is deployment
  telemetry about ARIA's own routes and has no analytical value to a model.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections import Counter
from typing import Any

log = logging.getLogger(__name__)

# One hash per observation kind. `crucix:cure:*` is a new namespace — the census
# recorded 303 existing `crucix:<ns>:<name>` families and none used `cure`.
ROUTES_KEY = "crucix:cure:usage_routes"
META_KEY = "crucix:cure:usage_meta"

FLUSH_INTERVAL_S = 60.0
# A hard cap on distinct templates held in memory. Route templates are bounded
# (~1300), so exceeding this means something is keying on a raw path; drop
# rather than grow without limit, and record that we did.
MAX_TRACKED = 5000

_buffer: Counter[str] = Counter()
_last_flush = 0.0
_flush_in_flight = False
_dropped = 0
_flush_failures = 0


def record_route(template: str, method: str) -> None:
    """Record one request. Sync, in-memory, never raises, no I/O."""
    global _dropped
    try:
        if not template:
            return
        field = f"{method.upper()} {template}"
        if field not in _buffer and len(_buffer) >= MAX_TRACKED:
            _dropped += 1
            return
        _buffer[field] += 1
    except Exception:  # pragma: no cover — must never break a request
        pass


def should_flush(now: float | None = None) -> bool:
    """True when a flush is due and none is already running."""
    if _flush_in_flight or not _buffer:
        return False
    return (now if now is not None else time.monotonic()) - _last_flush >= FLUSH_INTERVAL_S


def maybe_schedule_flush() -> bool:
    """Schedule a coalesced background flush if one is due.

    Returns True if a flush was scheduled. Fire-and-forget by design: the
    request that triggers it does not wait for the store.
    """
    global _flush_in_flight
    try:
        if not should_flush():
            return False
        loop = asyncio.get_running_loop()
        _flush_in_flight = True
        task = loop.create_task(flush())
        # Keep a reference so the task is not garbage-collected mid-flight.
        _INFLIGHT.add(task)
        task.add_done_callback(_INFLIGHT.discard)
        return True
    except RuntimeError:
        return False  # no running loop (sync test context)
    except Exception:  # pragma: no cover
        return False


_INFLIGHT: set[asyncio.Task] = set()


async def flush() -> int:
    """Drain the buffer into the durable store. Returns fields written."""
    global _last_flush, _flush_in_flight, _flush_failures
    written = 0
    try:
        if not _buffer:
            return 0
        # Snapshot-and-clear FIRST so concurrent requests keep counting into a
        # fresh buffer; a failed write loses at most one interval rather than
        # blocking the counter.
        pending = dict(_buffer)
        _buffer.clear()

        from aria_service.intel import state_store

        for field, count in pending.items():
            try:
                await state_store.hincrby(ROUTES_KEY, field, count)
                written += 1
            except Exception:
                # Put it back so the count is not silently lost.
                _buffer[field] += count
                _flush_failures += 1
        try:
            await state_store.set_json(
                META_KEY,
                {
                    "last_flush_epoch": time.time(),
                    "flush_failures": _flush_failures,
                    "dropped_over_cap": _dropped,
                },
            )
        except Exception:
            _flush_failures += 1
        return written
    except Exception as e:  # pragma: no cover
        log.warning("[R-F3730] usage flush failed: %s", e)
        _flush_failures += 1
        return written
    finally:
        _last_flush = time.monotonic()
        _flush_in_flight = False


async def snapshot() -> dict[str, Any]:
    """Read the durable observation set — the Phase 0.3 overlay input."""
    from aria_service.intel import state_store

    observed: dict[str, int] = {}
    meta: dict[str, Any] = {}
    try:
        # MUST be hgetall, not get_json. flush() writes counts with hincrby,
        # which stores a HASH; get_json expects a JSON blob and returns None for
        # one. That mismatch shipped: live showed flush_failures=0 and a valid
        # last_flush_epoch (so writes were landing) while observed_routes stayed
        # 0 — the write worked and only the read was blind, which is the most
        # dangerous shape because it reads as "nothing was ever observed".
        raw = await state_store.hgetall(ROUTES_KEY)
        if isinstance(raw, dict):
            observed = {k: int(v) for k, v in raw.items() if str(v).lstrip("-").isdigit()}
    except Exception as e:
        return {
            "available": False,
            "reason": f"store read failed: {type(e).__name__}",
            "observed_routes": 0,
        }
    try:
        m = await state_store.get_json(META_KEY)
        if isinstance(m, dict):
            meta = m
    except Exception:
        pass
    return {
        "available": True,
        "observed_routes": len(observed),
        "total_requests": sum(observed.values()),
        "pending_in_buffer": sum(_buffer.values()),
        "routes": observed,
        "meta": meta,
    }


def _reset_for_tests() -> None:
    """Test hook — module state is process-global by design."""
    global _last_flush, _flush_in_flight, _dropped, _flush_failures
    _buffer.clear()
    _last_flush = 0.0
    _flush_in_flight = False
    _dropped = 0
    _flush_failures = 0
