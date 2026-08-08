"""R-F703 (2026-05-18) — event-loop stall detector + consistency_suite
to_thread wrap.

Live fly logs 2026-05-18 19:52:34 showed autonomy_surface's 4 parallel
asyncio.wait_for(..., timeout=8.0) sub-tasks expiring at the same
wall-clock instant — only possible if the event loop was wall-clock-
stuck for ≥8 seconds. /health/live then couldn't respond within its
20s timeout, fly's LB marked the machine unhealthy, and PR04 cascade.

R-F703 ships two defensive moves:

1. consistency_suite._similarity now wraps sentence_transformers
   encode() in asyncio.to_thread (was a sync call inside an async
   function — guaranteed event-loop block during run_all()).

2. main.py lifespan starts an event-loop stall detector that wakes
   every 1s and logs WARNING when the actual elapsed wall-clock
   exceeds 5s. Pre-detector we had no on-line signal of *what* was
   blocking the loop; the next wedge will now be timestamped in the
   logs so we can correlate against what was running.
"""
from __future__ import annotations

import asyncio
import re        # R-F3774 — assert scheduling, not a byte-for-byte call string

# R-F3773/§16 — NOT inspect.getsource: it slices at line numbers captured AT
# IMPORT, so a mid-run edit silently returns a DIFFERENT function's body. A CLASS
# target scopes the lookup to that class's own body (R-F3771).
from ._source_probe import function_source


# ── consistency_suite fix tests ───────────────────────────────────────


def test_rf703_consistency_similarity_wraps_encode_in_to_thread():
    """The encode() call inside consistency_suite._similarity must run
    via asyncio.to_thread + _safe_encode, not sync on the event loop."""
    import inspect
    from aria_service.intel import consistency_suite as cs
    src = function_source(cs, "_similarity")
    # Must contain the to_thread wrapper
    assert "to_thread" in src, (
        "R-F703 expected to_thread wrapping in _similarity; not found"
    )
    # Must call _safe_encode (R-F530 lock-aware wrapper)
    assert "_safe_encode" in src, (
        "R-F703 expected _safe_encode call in _similarity (R-F530 lock)"
    )
    # Must NOT have a bare sync `embedder.encode([` call
    # (matches the line we removed)
    assert "embedder.encode([" not in src, (
        "R-F703 expected the bare sync embedder.encode([ ... ]) to be gone"
    )


# ── stall detector tests ──────────────────────────────────────────────


def test_rf703_stall_detector_function_present_in_main():
    """The lifespan must register the event-loop stall detector."""
    import inspect
    from aria_service import main
    src = function_source(main, "lifespan")
    assert "_event_loop_stall_detector" in src, (
        "R-F703 expected _event_loop_stall_detector function in lifespan"
    )
    # ── R-F3774 — this asserted the EXACT string
    # "asyncio.create_task(_event_loop_stall_detector())", with empty args.
    #
    # It broke the moment the task gained a NAME:
    #   _bg_task(asyncio.create_task(_event_loop_stall_detector(),
    #                                name="stall_detector"))   # main.py:1933
    # which is an IMPROVEMENT — a named task is identifiable in a task dump, and
    # naming background tasks is how a stalled or dead one gets diagnosed at all.
    #
    # So the test failed because the code got BETTER, and the one-line "fix" it
    # invites is to delete `name="stall_detector"` — removing a diagnostic to
    # satisfy a string match. The watchdog was never missing; only the literal
    # was. Same shape as R-F3772 (test_rf522), where the tempting fix would have
    # reintroduced an SSRF hole.
    #
    # Assert the BEHAVIOUR the test cares about — the detector is scheduled as a
    # task — not the byte-for-byte call. Argument order and kwargs are free to
    # change; scheduling is not.
    assert re.search(r"create_task\(\s*_event_loop_stall_detector\(\)", src), (
        "R-F703 expected _event_loop_stall_detector to be scheduled as a task"
    )


def test_rf703_stall_detector_threshold_and_settle_constants():
    """Threshold is 5s (catches real wedges, ignores GC blips); settle
    window is 120s (lets RAG / knowledge / OCR cold-load finish before
    arming)."""
    import inspect
    from aria_service import main
    src = function_source(main, "lifespan")
    assert "_STALL_WARN_THRESHOLD_S = 5.0" in src, (
        "R-F703 expected 5.0s stall threshold"
    )
    # Settle window: 120s sleep before arming
    assert "await _aio.sleep(120)" in src, (
        "R-F703 expected a 120s settle window before arming the detector"
    )


def test_rf703_stall_detector_uses_monotonic_clock():
    """Wall-clock can jump (NTP, container migration); monotonic clock
    is the only reliable measurement primitive for stall detection."""
    import inspect
    from aria_service import main
    src = function_source(main, "lifespan")
    assert "_time.monotonic()" in src, (
        "R-F703 expected monotonic clock measurement"
    )


def test_rf703_stall_detector_handles_cancellation():
    """Lifespan shutdown should cancel the detector cleanly — it must
    catch asyncio.CancelledError to avoid leaving a stale traceback in
    the shutdown logs."""
    import inspect
    from aria_service import main
    src = function_source(main, "lifespan")
    assert "CancelledError" in src, (
        "R-F703 expected CancelledError handling in stall detector"
    )


def test_rf703_stall_detector_logs_at_warning_level():
    """Detector findings must be WARNING-level so they hit the error
    ledger + dashboard recent-errors panel (R-F381)."""
    import inspect
    from aria_service import main
    src = function_source(main, "lifespan")
    # The actual log line invocation
    assert "logger.warning(" in src, (
        "R-F703 expected logger.warning() call for stall events"
    )
    # R-F3252 — the marker moved because the old one asserted a CAUSE the
    # detector never measured ("synchronous CPU work blocked the loop"). A live
    # R-F704 stack taken during one of these showed the main thread parked in a
    # bare asyncio.runners.run with no application frame: nothing was blocking a
    # coroutine, the loop was starved by 56 aiosqlite worker threads. The
    # detector measures loop latency and nothing else, so that is all it may say.
    assert "event loop heartbeat did not tick for" in src, (
        "R-F703 expected the stall marker in the log"
    )
    assert "CAUSE NOT ESTABLISHED" in src, (
        "R-F3252 — the stall log must not assert a cause it did not measure"
    )
    assert "synchronous CPU work blocked the loop." not in src, (
        "R-F3252 — the asserted-cause wording is back; it sent two review "
        "cycles hunting a blocking call that was never there"
    )


def test_rf703_smoke_main_imports():
    """Lifespan smoke: main module imports cleanly after the edits."""
    from aria_service import main  # noqa: F401
    assert hasattr(main, "lifespan")


def test_rf703_smoke_consistency_suite_imports():
    from aria_service.intel import consistency_suite as cs  # noqa: F401
    assert callable(cs._similarity)
