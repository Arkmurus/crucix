"""R-F1080 — Continuous Profiler for ARIA.

Samples Python stack traces at regular intervals to detect CPU hotspots,
GIL contention, and event-loop stalls. Wires findings to brain_hook so
the coder can act on performance regressions.

Architecture:
  - A daemon thread samples sys._current_frames() every 100ms
  - Aggregates stack samples into a flamegraph-style report every 60s
  - On stall detection (event loop heartbeat stale >2s), captures
    immediate stack dump and emits a capability_gap signal

Usage:
  from aria_service.intel.continuous_profiler import start_profiler, stop_profiler
  profiler_task = start_profiler()
  # ... later ...
  stop_profiler(profiler_task)

Environment:
  ARIA_CONTINUOUS_PROFILER_ENABLED=1 (default: 1)
  ARIA_PROFILER_INTERVAL_MS=100 (sampling interval)
  ARIA_PROFILER_REPORT_INTERVAL_S=60 (aggregation window)
"""
from __future__ import annotations

import asyncio
import logging
import os
import sys
import threading
import time
from collections import Counter
from typing import Any

logger = logging.getLogger("aria.continuous_profiler")

_ENABLED = (os.getenv("ARIA_CONTINUOUS_PROFILER_ENABLED", "1") or "").strip().lower() in ("1", "true", "yes")
_SAMPLE_INTERVAL_S = float(os.getenv("ARIA_PROFILER_INTERVAL_MS", "100")) / 1000.0
_REPORT_INTERVAL_S = float(os.getenv("ARIA_PROFILER_REPORT_INTERVAL_S", "60"))
_STALL_THRESHOLD_S = float(os.getenv("ARIA_PROFILER_STALL_THRESHOLD_S", "2.0"))

# Shared state between the sampler thread and the async reporter
_state: dict[str, Any] = {
    "running": False,
    "samples": Counter(),       # stack frame signature → count
    "total_samples": 0,
    "last_sample_at": 0.0,
    "stall_detected": False,
    "main_loop_heartbeat": 0.0,
}


def _frame_signature(frame) -> str:
    """Create a short signature for a stack frame."""
    if frame is None:
        return "<no frame>"
    code = frame.f_code
    return f"{code.co_filename}:{code.co_name}:{frame.f_lineno}"


def _sample_thread() -> None:
    """Daemon thread: sample stack traces every _SAMPLE_INTERVAL_S."""
    while _state["running"]:
        time.sleep(_SAMPLE_INTERVAL_S)
        try:
            frames = sys._current_frames()
            for thread_id, frame in frames.items():
                # Walk the stack to find the outermost interesting frame
                sig = _frame_signature(frame)
                _state["samples"][sig] += 1
                _state["total_samples"] += 1
            _state["last_sample_at"] = time.time()

            # Stall detection: check if the main loop heartbeat is stale
            heartbeat = _state.get("main_loop_heartbeat", 0)
            if heartbeat > 0 and (time.time() - heartbeat) > _STALL_THRESHOLD_S:
                if not _state["stall_detected"]:
                    _state["stall_detected"] = True
                    logger.warning(
                        "[continuous_profiler] Main loop heartbeat stale for "
                        "%.1fs — possible event-loop stall. Top frames: %s",
                        time.time() - heartbeat,
                        _state["samples"].most_common(5),
                    )
        except Exception:
            pass  # Sampler must never crash


async def _heartbeat_tick() -> None:
    """Async task: bump the profiler's heartbeat every 1s so the sampler
    thread can detect genuine event-loop stalls. Runs in the event loop;
    if the loop stalls this task won't tick, and the sampler will correctly
    fire the stall warning."""
    while _state["running"]:
        await asyncio.sleep(1.0)
        _state["main_loop_heartbeat"] = time.time()


async def _report_loop() -> None:
    """Async task: every _REPORT_INTERVAL_S, emit a profile report."""
    while _state["running"]:
        await asyncio.sleep(_REPORT_INTERVAL_S)
        if _state["total_samples"] == 0:
            continue
        try:
            top_frames = _state["samples"].most_common(10)
            total = _state["total_samples"]
            _state["samples"].clear()
            _state["total_samples"] = 0
            _state["stall_detected"] = False

            # Log the profile summary
            logger.info(
                "[continuous_profiler] Profile snapshot (%d samples in %.0fs):",
                total, _REPORT_INTERVAL_S,
            )
            for sig, count in top_frames:
                pct = count / total * 100
                logger.info("  %5.1f%%  %s", pct, sig)

            # If a single frame dominates (>50%), emit a brain signal
            if top_frames and (top_frames[0][1] / total) > 0.5:
                try:
                    from . import capability_gaps as _cg
                    import asyncio as _aio
                    _aio.create_task(_cg.record_gap(
                        gap_type="performance",
                        severity=2,
                        title=f"CPU hotspot: {top_frames[0][0]}",
                        description=(
                            f"Frame {top_frames[0][0]} occupied "
                            f"{top_frames[0][1]/total*100:.0f}% of "
                            f"{total} samples in {_REPORT_INTERVAL_S}s"
                        ),
                        module="continuous_profiler",
                    ))
                except Exception:
                    pass
        except Exception:
            pass


def bump_heartbeat() -> None:
    """Called by the event loop stall detector to mark the loop alive."""
    _state["main_loop_heartbeat"] = time.time()


def start_profiler() -> list[asyncio.Task]:
    """Start the continuous profiler. Returns list of background tasks."""
    if not _ENABLED:
        logger.info("[continuous_profiler] DISABLED via ARIA_CONTINUOUS_PROFILER_ENABLED=0")
        return []

    _state["running"] = True
    _state["main_loop_heartbeat"] = time.time()

    # Start the sampling daemon thread
    thread = threading.Thread(target=_sample_thread, daemon=True, name="continuous-profiler")
    thread.start()

    # Start the async report loop
    loop = asyncio.get_event_loop()
    task = loop.create_task(_report_loop(), name="continuous-profiler-report")

    # Start the heartbeat tick (async — bumps main_loop_heartbeat every 1s)
    hb_task = loop.create_task(_heartbeat_tick(), name="continuous-profiler-hb")

    logger.info("[continuous_profiler] Started (interval=%.0fms, report=%.0fs)", _SAMPLE_INTERVAL_S * 1000, _REPORT_INTERVAL_S)
    return [task, hb_task]


def stop_profiler(tasks: list[asyncio.Task] | None = None) -> None:
    """Stop the continuous profiler."""
    _state["running"] = False
    if tasks:
        for t in tasks:
            t.cancel()
    logger.info("[continuous_profiler] Stopped")
