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


# ── consistency_suite fix tests ───────────────────────────────────────


def test_rf703_consistency_similarity_wraps_encode_in_to_thread():
    """The encode() call inside consistency_suite._similarity must run
    via asyncio.to_thread + _safe_encode, not sync on the event loop."""
    import inspect
    from aria_service.intel import consistency_suite as cs
    src = inspect.getsource(cs._similarity)
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
    src = inspect.getsource(main.lifespan)
    assert "_event_loop_stall_detector" in src, (
        "R-F703 expected _event_loop_stall_detector function in lifespan"
    )
    # Must be scheduled
    assert "asyncio.create_task(_event_loop_stall_detector())" in src, (
        "R-F703 expected _event_loop_stall_detector to be scheduled as a task"
    )


def test_rf703_stall_detector_threshold_and_settle_constants():
    """Threshold is 5s (catches real wedges, ignores GC blips); settle
    window is 120s (lets RAG / knowledge / OCR cold-load finish before
    arming)."""
    import inspect
    from aria_service import main
    src = inspect.getsource(main.lifespan)
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
    src = inspect.getsource(main.lifespan)
    assert "_time.monotonic()" in src, (
        "R-F703 expected monotonic clock measurement"
    )


def test_rf703_stall_detector_handles_cancellation():
    """Lifespan shutdown should cancel the detector cleanly — it must
    catch asyncio.CancelledError to avoid leaving a stale traceback in
    the shutdown logs."""
    import inspect
    from aria_service import main
    src = inspect.getsource(main.lifespan)
    assert "CancelledError" in src, (
        "R-F703 expected CancelledError handling in stall detector"
    )


def test_rf703_stall_detector_logs_at_warning_level():
    """Detector findings must be WARNING-level so they hit the error
    ledger + dashboard recent-errors panel (R-F381)."""
    import inspect
    from aria_service import main
    src = inspect.getsource(main.lifespan)
    # The actual log line invocation
    assert "logger.warning(" in src, (
        "R-F703 expected logger.warning() call for stall events"
    )
    assert "event loop stalled for" in src, (
        "R-F703 expected the 'event loop stalled for' marker in the log"
    )


def test_rf703_smoke_main_imports():
    """Lifespan smoke: main module imports cleanly after the edits."""
    from aria_service import main  # noqa: F401
    assert hasattr(main, "lifespan")


def test_rf703_smoke_consistency_suite_imports():
    from aria_service.intel import consistency_suite as cs  # noqa: F401
    assert callable(cs._similarity)
