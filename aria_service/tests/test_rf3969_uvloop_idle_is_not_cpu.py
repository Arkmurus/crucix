"""R-F3969 / C-58 — an IDLE uvloop event loop was reported as sustained CPU on
the loop thread, and it is filing that gap in production right now.

Live gap read from `/api/aria/capability-gaps/summary` on 2026-08-13:

    Frame /usr/local/lib/python3.13/asyncio/runners.py:run:119 occupied 51% of
    1124 samples in 60.0s — sustained CPU on the event-loop thread.
    Fix: offload the CPU-bound call with asyncio.to_thread or a process pool.

There is no CPU-bound call. **uvloop is installed and active in the production
image** (verified in-machine: `importlib.util.find_spec("uvloop")` is not None,
Python 3.13.15). uvloop's `run_forever` is Cython — it leaves NO Python frame —
so while the loop waits on epoll the innermost PYTHON frame of the loop thread
is the last one before the C boundary: `asyncio/runners.py:run`.

That is precisely the shape `_PARKED_FRAMES` already documents for aiosqlite's
`_connection_worker_thread`: *"a thread parked on a C-implemented primitive
leaves its OWN function as the innermost frame"*. On stock asyncio the wait is
visible as `selectors.py:select`, which is in the list — under uvloop that frame
never appears, so the entry silently stopped covering the loop thread.

The cost is recorded in this repo already. `main.py:1766` describes a 2026-07-27
dump showing "the main thread parked in a bare asyncio.runners.run with NO
application frame — nothing was blocking a coroutine" and notes that **"two
review cycles went looking for a blocking call that was never there."** The
profiler is still generating exactly that gap, into a 500-slot ledger that is
currently 500/500 unresolved — so each false hotspot evicts a real defect.

**This cannot mask a genuine hotspot.** If application code is burning CPU on
the loop thread, that code's own frames are innermost, not `runners.py:run`;
`Runner.run` does no work itself. The entry removes a false positive without
removing any true one, which is why it is safe to add and why the test below
pins that a real application frame is still reported.
"""
from __future__ import annotations

import os

from aria_service.intel import continuous_profiler as CP


class _Code:
    def __init__(self, filename, name):
        self.co_filename, self.co_name = filename, name


class _Frame:
    def __init__(self, filename, name, lineno=1):
        self.f_code = _Code(filename, name)
        self.f_lineno = lineno


def _frame(path_parts, name):
    return _Frame(os.path.join(*path_parts), name)


# ── the defect ───────────────────────────────────────────────────────────────

def test_the_uvloop_idle_frame_is_parked():
    """The exact frame from the live gap."""
    f = _frame(("usr", "local", "lib", "python3.13", "asyncio", "runners.py"), "run")
    assert CP._is_parked_frame(f) is True, (
        "an idle uvloop loop thread is still counted as RUNNING — it will keep "
        "filing 'sustained CPU on the event-loop thread' gaps with no CPU-bound "
        "call anywhere, into a ledger that is already full"
    )


def test_it_is_registered_the_same_way_aiosqlite_is():
    """Same mechanism, same list — not a second bespoke rule."""
    assert ("runners.py", "run") in CP._PARKED_FRAMES
    assert ("core.py", "_connection_worker_thread") in CP._PARKED_FRAMES


# ── it must not mask a real hotspot ──────────────────────────────────────────

def test_application_frames_are_still_reported():
    for parts, name in (
        (("app", "aria_service", "intel", "dd_orchestrator.py"), "_run_digital"),
        (("app", "aria_service", "intel", "knowledge.py"), "_save"),
        (("app", "aria_service", "main.py"), "lifespan"),
    ):
        assert CP._is_parked_frame(_frame(parts, name)) is False, (parts, name)


def test_a_different_function_in_runners_is_not_parked():
    """Scope the entry to `run` — do not blanket-exclude the module."""
    f = _frame(("usr", "lib", "python3.13", "asyncio", "runners.py"), "_lazy_init")
    assert CP._is_parked_frame(f) is False


def test_the_stock_asyncio_wait_is_still_covered():
    """selectors.select is how the same wait looks WITHOUT uvloop."""
    assert CP._is_parked_frame(
        _frame(("usr", "lib", "python3.13", "selectors.py"), "select")) is True


def test_the_established_entries_survive():
    for expected in (
        ("queue.py", "get"),
        ("threading.py", "wait"),
        ("thread.py", "_worker"),
        ("main.py", "_wedge_watchdog"),
    ):
        assert expected in CP._PARKED_FRAMES, expected


# ── the census must still be able to see something ───────────────────────────

def test_the_filter_still_admits_a_busy_thread():
    """A filter that excludes everything certifies health by construction."""
    import sys
    import threading

    CP._state["samples"].clear()
    CP._state["total_samples"] = 0
    _prev = CP._state.get("sampler_tid")
    CP._state["sampler_tid"] = None

    stop = [False]
    started = threading.Event()

    def _busy():
        started.set()
        n = 0
        while not stop[0]:
            n = (n + 1) % 1_000_003

    t = threading.Thread(target=_busy, daemon=True)
    t.start()
    started.wait(timeout=5.0)
    try:
        CP._collect_samples()
    finally:
        stop[0] = True
        t.join(timeout=5.0)
        CP._state["sampler_tid"] = _prev

    assert CP._state["total_samples"] > 0
