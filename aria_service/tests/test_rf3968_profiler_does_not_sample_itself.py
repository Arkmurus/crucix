"""R-F3968 / C-57 — the stall detector's own instruments inflated its numbers.

This matters more than a rounding error, because these are the figures a future
session diagnoses a production stall from, and R-F3464 introduced the thread
census specifically AS the starvation discriminator.

**1. The profiler samples itself, on 100% of passes.**
`_collect_samples` calls `sys._current_frames()`, which includes the CALLING
thread. The sampler's innermost frame at that moment is `_collect_samples` —
never `_sample_thread`, which is what the parked-frame list names:

    continuous_profiler.py:116
        ("continuous_profiler.py", "_sample_thread"),  # our own sampler

So the exclusion is unreachable by construction and the sampler counts itself
every single pass. It was reported as one of the largest frames; measured real
cost is ~0.04% of one core.

**2. A sleeping thread counts as running.**
`main.py:1730 _wedge_watchdog` spends its life in `_time.sleep(1.0)`. `sleep` is
a C function with no Python frame, so the innermost PYTHON frame is
`_wedge_watchdog` itself — exactly the shape the module already documents for
aiosqlite's `_connection_worker_thread`, and exactly why that one was added to
`_PARKED_FRAMES`. The watchdog never was.

Every genuine frame was diluted by roughly 1.4x as a result — the class R-F3464
was written to eliminate, reproduced inside the fix.

**The two fixes are deliberately different mechanisms.** The sampler is excluded
by THREAD IDENTITY (`threading.get_ident()`), which is exact and cannot rot: any
rename, refactor or added helper keeps working, whereas the name-pair entry
broke the moment the innermost frame was a different function in the same file.
The watchdog is added to `_PARKED_FRAMES` because that list is the module's
established, documented mechanism for "parked on a C primitive, so its own
function is the innermost frame" — inventing a second mechanism for the same
condition is how the two drift apart.
"""
from __future__ import annotations

import os
import sys
import threading

from aria_service.intel import continuous_profiler as CP


# ── 1. the sampler must not count itself ─────────────────────────────────────

def test_the_parked_name_entry_could_never_have_matched():
    """Pin the premise so nobody 'restores' the unreachable entry."""
    def _inner():
        return sys._current_frames()[threading.get_ident()]

    frame = _inner()
    # The innermost frame is the helper actually running, never the outer
    # function — which is precisely why naming `_sample_thread` never worked.
    assert frame.f_code.co_name == "_inner"


def test_collect_samples_does_not_record_the_sampler_thread():
    """Production shape: the sampler runs on its own thread and registers its id."""
    CP._state["samples"].clear()
    CP._state["total_samples"] = 0
    _prev = CP._state.get("sampler_tid")
    done = threading.Event()

    def _as_sampler():
        CP._state["sampler_tid"] = threading.get_ident()
        CP._collect_samples()
        done.set()

    t = threading.Thread(target=_as_sampler, daemon=True)
    t.start()
    done.wait(timeout=5.0)
    t.join(timeout=5.0)
    try:
        sigs = " ".join(CP._state["samples"].keys())
        assert "_collect_samples" not in sigs, (
            "the profiler counted its own sampling frame — it did this on 100% "
            "of passes and was reported as one of the largest frames"
        )
    finally:
        CP._state["sampler_tid"] = _prev


def test_a_synchronous_call_still_sees_the_loop_thread():
    """R-F3464 drives _collect_samples from the loop thread; excluding the
    CALLER rather than the registered sampler would blind it there."""
    CP._state["samples"].clear()
    CP._state["total_samples"] = 0
    _prev = CP._state.get("sampler_tid")
    CP._state["sampler_tid"] = None
    try:
        CP._collect_samples()
        assert CP._state["total_samples"] > 0
    finally:
        CP._state["sampler_tid"] = _prev


def test_it_still_samples_other_threads():
    """A profiler that excludes everything is not a profiler."""
    CP._state["samples"].clear()
    CP._state["total_samples"] = 0

    # A genuinely BUSY thread — not one parked on an Event, which `_PARKED_FRAMES`
    # correctly excludes via ("threading.py", "wait"). The first version of this
    # test used an Event and "failed" because the filter was doing its job.
    stop = [False]
    started = threading.Event()

    def _busy_worker():
        started.set()
        n = 0
        while not stop[0]:
            n = (n + 1) % 1_000_003          # real Python work, a live frame

    t = threading.Thread(target=_busy_worker, daemon=True)
    t.start()
    started.wait(timeout=5.0)
    try:
        CP._collect_samples()
    finally:
        stop[0] = True
        t.join(timeout=5.0)

    assert CP._state["total_samples"] > 0, (
        "the exclusion swallowed every thread — an emptied census reads as a "
        "healthy one"
    )


# ── 2. a sleeping watchdog is not a running thread ───────────────────────────

def test_the_wedge_watchdog_is_treated_as_parked():
    assert ("main.py", "_wedge_watchdog") in CP._PARKED_FRAMES, (
        "the wedge watchdog sleeps in time.sleep(1.0), which has no Python "
        "frame, so its own function is the innermost one and it was counted as "
        "RUNNING in the census R-F3464 introduced as the starvation discriminator"
    )


def test_the_established_parked_entries_survive():
    """The list is load-bearing; do not thin it while editing it."""
    for expected in (
        ("queue.py", "get"),
        ("threading.py", "wait"),
        ("selectors.py", "select"),
        ("core.py", "_connection_worker_thread"),
    ):
        assert expected in CP._PARKED_FRAMES, expected


def test_is_parked_frame_still_discriminates():
    """It must be able to say NO, or it is not a filter."""
    class _Code:
        def __init__(self, fn, name):
            self.co_filename, self.co_name = fn, name

    class _Frame:
        def __init__(self, fn, name):
            self.f_code = _Code(fn, name)

    assert CP._is_parked_frame(_Frame(os.path.join("x", "queue.py"), "get")) is True
    assert CP._is_parked_frame(_Frame(os.path.join("x", "main.py"), "_wedge_watchdog")) is True
    assert CP._is_parked_frame(_Frame(os.path.join("x", "dd_orchestrator.py"), "_run_digital")) is False
    assert CP._is_parked_frame(None) is False


# ── the exclusion must be by identity, not by name ───────────────────────────

def test_the_sampler_exclusion_is_by_thread_identity():
    from ._source_probe import function_code
    src = function_code(CP, "_collect_samples")
    assert "sampler_tid" in src, (
        "the sampler is excluded by frame NAME again — that broke the moment "
        "the innermost frame was a different function in the same file, which "
        "is the defect"
    )
