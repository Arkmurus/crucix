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
from .engine_wiring import wire_success, wire_failure

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
# R-F3464 — floor on the sample base before a ">50% of samples" CPU-hotspot gap
# may be recorded. Roughly one second of sampling at the default 100ms interval.
_MIN_HOTSPOT_SAMPLES = int(os.getenv("ARIA_PROFILER_MIN_HOTSPOT_SAMPLES", "10"))

# Shared state between the sampler thread and the async reporter
_state: dict[str, Any] = {
    "running": False,
    # R-F3065 — idempotence guard. `running` alone was set but never checked,
    # so repeat start_profiler() calls each leaked a sampler thread (7 seen live).
    # Holds the live sampler Thread; loop-bound asyncio tasks are deliberately
    # NOT cached here (see start_profiler).
    "sampler_thread": None,
    "samples": Counter(),       # stack frame signature → count
    "total_samples": 0,
    "last_sample_at": 0.0,
    "stall_detected": False,
    "main_loop_heartbeat": 0.0,
    # R-F2519 (log-review F1) — count stall EVENTS as a metric, not only a log warning,
    # so the recurring event-loop pressure is trackable on /health/perf over time.
    "stall_count": 0,
    "last_stall_at": 0.0,
    "max_stall_s": 0.0,
    # R-F3464 — which OS thread runs the event loop. Without this the module
    # cannot attribute a loop stall at all, and printed a census of sleeping
    # threads instead. Set by _heartbeat_tick, which runs ON the loop.
    "loop_thread_id": None,
    # Evidence from the most recent stall, exposed via get_stall_stats() so the
    # discriminator survives past the log line.
    "last_stall_loop_stack": [],
    "last_stall_census": {},
}


def _frame_signature(frame) -> str:
    """Create a short signature for a stack frame."""
    if frame is None:
        return "<no frame>"
    code = frame.f_code
    return f"{code.co_filename}:{code.co_name}:{frame.f_lineno}"


# ── R-F3464: a stall must be attributed to the thread that stalled ──────────
#
# The old report printed `samples.most_common(5)`, a census over EVERY thread
# from sys._current_frames(). Threads parked in a blocking wait are on the stack
# 100% of the time, so they dominated that census unconditionally — the same five
# frames appeared whatever the cause, and on an IDLE box they appear most of all.
# Live, that read as "38.7% aiosqlite + 34% threadpool", which looks like I/O
# saturation and is really just a count of sleeping threads.
#
# main.py:1766 records what this costs: a 2026-07-27 R-F704 dump showed the main
# thread parked in a bare asyncio.runners.run with NO application frame — nothing
# was blocking a coroutine — and "two review cycles went looking for a blocking
# call that was never there". The real signature was 56 live aiosqlite connection
# worker threads (peak 140) against a design of ~6: GIL starvation.
#
# So: attribute to the LOOP thread, and report the thread census that main.py:1771
# names as the discriminator ("count the worker threads instead") but which was
# measured nowhere.

# (file basename, function name) pairs whose INNERMOST frame means "parked".
# Two shapes matter. Pure-Python waits leave a frame in queue.py/threading.py.
# A thread parked on a C-implemented primitive (aiosqlite's worker blocks on
# queue.SimpleQueue.get, which has no Python frame) leaves its OWN function as
# the innermost frame — which is exactly why `_connection_worker_thread:59`
# showed up as a "top frame" in production.
_PARKED_FRAMES: frozenset[tuple[str, str]] = frozenset({
    ("queue.py", "get"),
    ("queue.py", "_get"),
    ("threading.py", "wait"),
    ("threading.py", "acquire"),
    ("selectors.py", "select"),          # an IDLE event loop, not a busy one
    ("thread.py", "_worker"),            # concurrent.futures worker
    ("core.py", "_connection_worker_thread"),   # aiosqlite
    # R-F3968 (C-57) — main.py:1730 `_wedge_watchdog` lives in `_time.sleep(1.0)`.
    # `sleep` is a C function with no Python frame, so the innermost PYTHON frame
    # is the watchdog's own function: the identical shape to aiosqlite's worker
    # directly above, and the reason that one is here. Without it, one of ARIA's
    # own idle monitors was counted as RUNNING in the census R-F3464 introduced
    # AS the starvation discriminator — the reported `running: 5` was inflated by
    # at least two sleeping monitors against an honest 2-3.
    ("main.py", "_wedge_watchdog"),
    # NOTE: the sampler is NOT excluded here. Naming it never worked — see
    # `_collect_samples`, which excludes it by thread identity instead.
})

# Innermost-frame function names that identify an aiosqlite connection worker.
_AIOSQLITE_WORKER_FUNCS = frozenset({"_connection_worker_thread"})


def _is_parked_frame(frame) -> bool:
    """True when this thread's innermost frame is a blocking wait (i.e. asleep).

    A parked thread cannot stall an event loop and cannot be a CPU hotspot, so it
    must not enter either census.
    """
    if frame is None:
        return False
    code = frame.f_code
    basename = os.path.basename(code.co_filename)
    return (basename, code.co_name) in _PARKED_FRAMES


def _register_loop_thread() -> None:
    """Record which OS thread runs the event loop. Called from the heartbeat
    coroutine, which by definition runs on it."""
    _state["loop_thread_id"] = threading.get_ident()


def _loop_thread_stack(limit: int = 8) -> list[str]:
    """The event-loop thread's current stack, innermost first.

    Returns [] when the loop thread is unknown — never guess an attribution.
    This is the frame set that actually answers "what stalled the loop".
    """
    tid = _state.get("loop_thread_id")
    if not tid:
        return []
    frame = sys._current_frames().get(tid)
    out: list[str] = []
    while frame is not None and len(out) < limit:
        out.append(_frame_signature(frame))
        frame = frame.f_back
    return out


def _thread_census() -> dict[str, int]:
    """Count live threads by kind. R-F3252 named this as the discriminator
    between a BLOCKED loop and a STARVED one; it was never measured."""
    frames = sys._current_frames()
    census = {"total": len(frames), "parked": 0, "aiosqlite_workers": 0,
              "pool_workers": 0, "running": 0}
    for tid, frame in frames.items():
        if frame is None:
            continue
        name = frame.f_code.co_name
        basename = os.path.basename(frame.f_code.co_filename)
        if name in _AIOSQLITE_WORKER_FUNCS or "aiosqlite" in frame.f_code.co_filename:
            census["aiosqlite_workers"] += 1
        elif (basename, name) == ("thread.py", "_worker"):
            census["pool_workers"] += 1
        if _is_parked_frame(frame):
            census["parked"] += 1
        else:
            census["running"] += 1
    # Thread objects can be parked on a C primitive with no telling frame; the
    # threading registry is the authority on how many exist at all.
    try:
        census["total"] = max(census["total"], threading.active_count())
        census["aiosqlite_workers"] = max(
            census["aiosqlite_workers"],
            sum(1 for t in threading.enumerate()
                if "connection_worker" in (t.name or "")),
        )
    except Exception:
        pass
    return census


def _collect_samples() -> None:
    """One sampling pass. Parked threads are EXCLUDED: the aggregate feeds a
    'CPU hotspot' gap whose text asserts CPU on the event-loop thread, so a
    sleeping worker must not be able to win it."""
    # R-F3968 (C-57) — EXCLUDE THE SAMPLER BY THREAD IDENTITY, not by frame name.
    #
    # `sys._current_frames()` includes the CALLING thread, whose innermost frame
    # at this instant is `_collect_samples` — never `_sample_thread`, which is
    # what `_PARKED_FRAMES` names. That entry was therefore unreachable by
    # construction and the sampler counted itself on 100% of passes, showing up
    # as one of the largest frames in the very report used to diagnose stalls.
    # Measured real cost: ~0.04% of one core.
    #
    # Identity is exact and cannot rot: a rename, a refactor, or an added helper
    # all keep working, whereas the name pair broke the moment the innermost
    # frame was a different function in the same file.
    # Exclude the SAMPLER's registered thread specifically, not merely "whoever
    # called this". `_collect_samples` is also driven synchronously from the loop
    # thread in tests and diagnostics, and excluding the caller there would blind
    # the profiler to the one thread it exists to watch (R-F3464 pins that).
    _sampler_tid = _state.get("sampler_tid")
    frames = sys._current_frames()
    for thread_id, frame in frames.items():
        if _sampler_tid is not None and thread_id == _sampler_tid:
            continue
        if _is_parked_frame(frame):
            continue
        _state["samples"][_frame_signature(frame)] += 1
        _state["total_samples"] += 1
    _state["last_sample_at"] = time.time()


def _sample_thread() -> None:
    """Daemon thread: sample stack traces every _SAMPLE_INTERVAL_S."""
    # R-F3968 (C-57) — publish OUR id so `_collect_samples` can exclude us by
    # identity. The old `("continuous_profiler.py", "_sample_thread")` entry in
    # _PARKED_FRAMES was unreachable: while sampling, this thread's innermost
    # frame is `_collect_samples`, not `_sample_thread`, so the sampler counted
    # itself on every pass.
    _state["sampler_tid"] = threading.get_ident()
    while _state["running"]:
        time.sleep(_SAMPLE_INTERVAL_S)
        try:
            _collect_samples()

            # Stall detection: check if the main loop heartbeat is stale
            heartbeat = _state.get("main_loop_heartbeat", 0)
            if heartbeat > 0 and (time.time() - heartbeat) > _STALL_THRESHOLD_S:
                if not _state["stall_detected"]:
                    _state["stall_detected"] = True
                    _stall_s = time.time() - heartbeat
                    # R-F2519 (F1) — record the stall EVENT as a metric.
                    _state["stall_count"] += 1
                    _state["last_stall_at"] = time.time()
                    if _stall_s > _state["max_stall_s"]:
                        _state["max_stall_s"] = _stall_s
                    _census = _thread_census()
                    _state["last_stall_census"] = _census
                    _loop_stack = _loop_thread_stack()
                    _state["last_stall_loop_stack"] = _loop_stack
                    # R-F3464 — report the LOOP thread's stack and the thread
                    # census, not a census of sleeping threads. Read it as
                    # main.py:1771 says: an application frame on the loop thread
                    # means something BLOCKED it; a bare asyncio.runners.run /
                    # selectors.select means it was STARVED, so look at the
                    # thread counts instead.
                    logger.warning(
                        "[continuous_profiler] Main loop heartbeat stale for "
                        "%.1fs — possible event-loop stall (stall #%d). "
                        "Loop-thread stack (innermost first): %s | threads: %s",
                        _stall_s,
                        _state["stall_count"],
                        _loop_stack or "<loop thread not registered>",
                        _census,
                    )
        except Exception:
            pass  # Sampler must never crash


async def _heartbeat_tick() -> None:
    """Async task: bump the profiler's heartbeat every 1s so the sampler
    thread can detect genuine event-loop stalls. Runs in the event loop;
    if the loop stalls this task won't tick, and the sampler will correctly
    fire the stall warning."""
    # R-F3464 — this coroutine runs ON the event loop, so its thread IS the loop
    # thread. Registering here is what makes stall attribution possible at all.
    _register_loop_thread()
    while _state["running"]:
        await asyncio.sleep(1.0)
        _state["main_loop_heartbeat"] = time.time()


def get_stall_stats() -> dict:
    """R-F2519 (log-review F1) — heartbeat-stall metric for /health/perf. Reports the
    count of event-loop stall EVENTS (heartbeat stale beyond the threshold), the worst
    stall seen, and how long ago the last one was — so recurring pressure is trackable
    over time, not just visible as scattered log warnings."""
    _last = _state.get("last_stall_at", 0.0)
    return {
        "stall_count": int(_state.get("stall_count", 0)),
        "max_stall_s": round(float(_state.get("max_stall_s", 0.0)), 2),
        "last_stall_age_s": round(time.time() - _last, 1) if _last else None,
        "threshold_s": _STALL_THRESHOLD_S,
        # R-F3464 — the attribution evidence, so an operator can tell a BLOCKED
        # loop from a STARVED one without catching the log line live. An
        # application frame in loop_stack => something blocked the loop. A bare
        # asyncio.runners.run / selectors.select => starvation; read threads.
        "last_stall_loop_stack": list(_state.get("last_stall_loop_stack") or []),
        "last_stall_threads": dict(_state.get("last_stall_census") or {}),
        "threads_now": _thread_census(),
    }


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
            # R-F3464 — require a real sample base before accusing a frame.
            # Excluding parked threads (correctly) shrank the denominator: where
            # ~50 sleeping threads used to dilute every count, a single active
            # frame can now clear 50% of a handful of samples and emit a bogus
            # "CPU hotspot" gap. A hotspot claim needs evidence, not a ratio over
            # noise. _MIN_HOTSPOT_SAMPLES is ~1s of sampling at the default rate.
            if (top_frames and total >= _MIN_HOTSPOT_SAMPLES
                    and (top_frames[0][1] / total) > 0.5):
                try:
                    from . import capability_gaps as _cg
                    import asyncio as _aio
                    # R-F2177: record_gap takes `detail` and `source`, NOT
                    # `description`/`module`. The old kwargs raised TypeError on
                    # EVERY call, swallowed by the except below → the CPU-hotspot
                    # gap was SILENTLY DROPPED (DARK §21a). That dropped the exact
                    # signal that names a loop hog (e.g. neural_memory._decode_edges),
                    # so the autonomous coder never saw — or could fix — its own CPU
                    # hotspots. Use the real param names so the gap actually lands.
                    _aio.create_task(_cg.record_gap(
                        gap_type="performance",
                        severity=2,
                        title=f"CPU hotspot: {top_frames[0][0]}",
                        detail=(
                            f"Frame {top_frames[0][0]} occupied "
                            f"{top_frames[0][1]/total*100:.0f}% of "
                            f"{total} samples in {_REPORT_INTERVAL_S}s — sustained "
                            f"CPU on the event-loop thread. Fix: offload the "
                            f"CPU-bound call with asyncio.to_thread or a process pool."
                        ),
                        source="continuous_profiler",
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

    # R-F3065 (2026-07-25) — IDEMPOTENT. This set _state["running"]=True but
    # never CHECKED it, so every call spawned another daemon sampler thread and
    # two more asyncio tasks. Measured live in a wedge dump: SEVEN
    # `continuous_profiler.py:_sample_thread` threads where exactly one is
    # correct — each one sampling sys._current_frames() every 100ms, so the
    # profiler was multiplying the very overhead it exists to measure.
    #
    # The caller is coder_entrypoint (R-F1080), which starts the profiler each
    # time the coder lane starts — so a restarting lane leaked a thread per
    # start. That same dump showed 203 threads total; at that count GIL
    # contention alone stalls the loop, which is what the R-F703 detector was
    # firing on (~3-6 stalls/hour, 526 wedge files).
    #
    # A profiler must never be able to make the process it profiles worse.
    #
    # The guard is on the THREAD only. Caching the asyncio tasks and handing
    # them back was the obvious next step and it is WRONG: tasks are bound to
    # the loop that created them, so a later caller on a different loop would
    # receive tasks from a dead one — which hung the test suite outright. The
    # leak being fixed is the sampler THREAD (a real OS thread, loop-independent);
    # the two async tasks are cheap and are always created for the CURRENT loop.
    _existing = _state.get("sampler_thread")
    _sampler_running = bool(
        _state.get("running") and _existing is not None and _existing.is_alive()
    )
    if _sampler_running:
        logger.info(
            "[continuous_profiler] sampler thread already alive — not spawning "
            "a second one (R-F3065)"
        )
    else:
        _state["running"] = True
        thread = threading.Thread(
            target=_sample_thread, daemon=True, name="continuous-profiler")
        thread.start()
        _state["sampler_thread"] = thread

    _state["running"] = True
    _state["main_loop_heartbeat"] = time.time()

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
    # R-F3065 — the sampler loop exits on `running=False`; clear the guard so a
    # later start_profiler() may legitimately spawn a fresh sampler. Without
    # this, stop→start would leave the profiler permanently dead.
    # The sampler loop exits on running=False; drop the handle so a later
    # start_profiler() spawns a fresh one.
    _state["sampler_thread"] = None
    for t in (tasks or []):
        t.cancel()
    logger.info("[continuous_profiler] Stopped")

    # R-F2118/R-F2119 §21a — wire module active
    try:
        wire_success(module="continuous_profiler",
                     summary="continuous_profiler module active",
                     source_id="continuous_profiler:init")
    except Exception:
        try:
            wire_failure(module="continuous_profiler", detail="module init failed",
                        gap_type="engine_failure", source="continuous_profiler:init")
        except Exception:
            pass
