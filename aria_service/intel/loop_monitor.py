"""R-F2849 — event-loop lag monitor: make loop starvation OBSERVABLE.

WHY. A controlled experiment (2026-07-22) proved that an I/O-bound web search is
4.6x slower when CPU-bound work runs on the same event loop:

    search alone      3.9-4.6s     loop lag  0.3 ms
    search contended  17.6-19.8s   loop lag  1029 ms

That contention is why adverse-media screening completes only ~2 of 34 templates in
its budget — the searches are fine, the loop is starved. But production had NO
loop-lag telemetry at all (grep confirmed zero), so this was invisible: the brain has
been suffering event-loop starvation with no gauge to show it.

You cannot robustly fix what you cannot measure. This is the foundation for the
cooperative-scheduling (Stage C) work — a live number for "how starved is the loop
right now", so a fix can be proven by the gauge dropping rather than by inference.

HOW. A background task sleeps for a fixed interval and measures how much LONGER than
that it actually took to be rescheduled. On an idle loop the overshoot is ~0; when a
non-yielding CPU task holds the loop, the overshoot is that task's uninterrupted run
length. A rolling window yields p50 / p95 / max.

The gauge is read by a SYNC health route, which stays responsive even when the loop
is wedged (the R-F2484 discriminator on /health/live) — so the number is retrievable
precisely when it matters most.

This module holds NO event-loop references and never blocks: it is a pure measurement
plus a plain-dict snapshot. Safe to import anywhere.
"""
from __future__ import annotations

import asyncio
import time
from collections import deque

# Process-global rolling window of lag samples (milliseconds). A deque with a bound
# so memory is fixed regardless of uptime.
_SAMPLES: deque[float] = deque(maxlen=600)   # ~10 min at a 1s interval
_STARTED_AT: float | None = None
_LAST_SAMPLE_AT: float | None = None
_INTERVAL_S = 1.0


def record_lag(lag_ms: float) -> None:
    """Record one lag sample. Pure; used by the loop and directly by tests."""
    global _LAST_SAMPLE_AT
    _SAMPLES.append(float(lag_ms))
    _LAST_SAMPLE_AT = time.time()


def _percentile(sorted_vals: list[float], p: float) -> float:
    if not sorted_vals:
        return 0.0
    k = max(0, min(len(sorted_vals) - 1, int(round((p / 100.0) * (len(sorted_vals) - 1)))))
    return sorted_vals[k]


def snapshot() -> dict:
    """A plain-dict view of loop health. Never touches the event loop.

    `status` is a coarse, honest band, NOT a health score:
      healthy  — p95 under a frame (well below a human-perceptible stall)
      busy     — p95 elevated; work is competing but the loop still turns
      starved  — p95 above ~1s; I/O callbacks are waiting behind CPU work, which is
                 exactly the condition that inflates searches 4-5x
      unknown  — no samples yet (monitor not started / just booted)
    """
    vals = list(_SAMPLES)
    if not vals:
        return {"status": "unknown", "samples": 0}
    s = sorted(vals)
    p95 = _percentile(s, 95)
    if p95 < 50:
        status = "healthy"
    elif p95 < 1000:
        status = "busy"
    else:
        status = "starved"
    return {
        "status": status,
        "samples": len(vals),
        "p50_ms": round(_percentile(s, 50), 1),
        "p95_ms": round(p95, 1),
        "max_ms": round(s[-1], 1),
        "interval_s": _INTERVAL_S,
        "last_sample_age_s": (round(time.time() - _LAST_SAMPLE_AT, 1)
                              if _LAST_SAMPLE_AT else None),
    }


async def loop_lag_monitor(interval_s: float = _INTERVAL_S) -> None:
    """Self-contained measure loop — NOT the production feed.

    In production the rolling window is fed by main.py's existing R-F703
    `_event_loop_stall_detector`, which already measures the per-second wake
    overshoot: one measurement loop, two consumers (that detector LOGS discrete
    stalls; snapshot() exposes the rolling gauge). Running a second loop here would
    duplicate that measurement.

    This coroutine is retained as a self-contained implementation of the same
    measurement, used by the capability tests to exercise record_lag()/snapshot()
    against a real running loop (including under a synthetic CPU hog). Trivial and
    non-blocking, so it is never itself a source of the lag it measures.
    """
    global _STARTED_AT, _INTERVAL_S
    _INTERVAL_S = interval_s
    _STARTED_AT = time.time()
    while True:
        t0 = time.monotonic()
        await asyncio.sleep(interval_s)
        overshoot_ms = (time.monotonic() - t0 - interval_s) * 1000.0
        # Clamp tiny negative jitter (monotonic granularity) to 0.
        record_lag(max(0.0, overshoot_ms))
