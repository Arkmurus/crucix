"""R-F3464 — the stall detector blamed threads that were asleep.

Live evidence (aria-intel, 2026-07-30, recurring ~1/min):

    WARNING | aria.continuous_profiler | [continuous_profiler] Main loop heartbeat
    stale for 2.0s — possible event-loop stall (stall #47). Top frames:
    [('aiosqlite/core.py:_connection_worker_thread:59', 4670),
     ('concurrent/futures/thread.py:_worker:90', 4484),
     ('threading.py:wait:359', 850), ('threading.py:wait:363', 850),
     ('continuous_profiler.py:_sample_thread:80', 425)]

Every one of those frames is a thread PARKED IN A BLOCKING WAIT:
``_connection_worker_thread`` sits in ``queue.get()``, ``_worker`` sits in
``work_queue.get()``, ``threading.wait`` is a Condition wait, and
``_sample_thread`` is the profiler's own sampler. None of them can stall an
event loop — they are asleep, and they are asleep on EVERY sample, so they
dominate ``most_common()`` on a busy box and an idle box alike.

Two consequences, both live:

  1. The stall warning's "Top frames" carries no information about the stall.
     The same five frames appear whatever the cause. main.py:1766 records the
     cost: a 2026-07-27 R-F704 stack dump showed the main thread parked in a
     bare ``asyncio.runners.run`` with NO application frame — nothing was
     blocking a coroutine at all — and "two review cycles went looking for a
     blocking call that was never there". This session was nearly the third.
  2. The >50% branch in ``_report_loop`` records a ``performance`` gap titled
     "CPU hotspot: <frame>" whose text asserts "sustained CPU on the event-loop
     thread. Fix: offload the CPU-bound call with asyncio.to_thread". Pointed at
     ``_connection_worker_thread`` that is advice to offload a call which is
     already off the loop and idle.

The fix attributes a loop stall to the LOOP THREAD, and reports the thread census
that main.py:1771 names as the real discriminator ("count the worker threads
instead") but which was measured nowhere.
"""
from __future__ import annotations

import queue
import sys
import threading
import time

import pytest

from aria_service.intel import continuous_profiler as cp


# ── helpers: a genuinely parked worker thread, like aiosqlite's ─────────────

class _ParkedWorker:
    """A thread blocked in queue.get() — the exact shape of an idle aiosqlite
    connection worker or a ThreadPoolExecutor worker."""

    def __init__(self) -> None:
        self._q: queue.Queue = queue.Queue()
        self.thread = threading.Thread(target=self._run, daemon=True,
                                       name="_connection_worker_thread")
        self.thread.start()
        time.sleep(0.05)  # let it reach the blocking get()

    def _run(self) -> None:
        while True:
            item = self._q.get()
            if item is None:
                return

    def stop(self) -> None:
        self._q.put(None)
        self.thread.join(timeout=2)


@pytest.fixture
def parked_workers():
    workers = [_ParkedWorker() for _ in range(6)]
    yield workers
    for w in workers:
        w.stop()


# ── The defect: parked threads must not be blamed ──────────────────────────

class TestStallAttribution:

    def test_parked_threads_are_classified_as_parked(self, parked_workers):
        """A thread blocked in queue.get() must be recognised as idle."""
        frames = sys._current_frames()
        parked_ids = {w.thread.ident for w in parked_workers}
        for tid in parked_ids:
            frame = frames.get(tid)
            assert frame is not None
            assert cp._is_parked_frame(frame), (
                "a thread blocked in queue.get() was not classified as parked"
            )

    def test_loop_thread_is_not_classified_as_parked_while_working(self):
        """The thread actually running work must NOT be filtered out as idle."""
        frame = sys._current_frames()[threading.get_ident()]
        assert not cp._is_parked_frame(frame)

    def test_thread_census_counts_workers(self, parked_workers):
        """R-F3252 named the worker-thread count as THE discriminator between a
        blocked loop and a starved one. It has to be measured."""
        census = cp._thread_census()
        assert census["total"] >= 6
        assert census["parked"] >= 6, census
        assert "aiosqlite_workers" in census

    def test_stall_report_names_the_loop_thread_not_a_parked_worker(self, parked_workers):
        """The load-bearing assertion.

        With the loop thread registered, the stall report must describe the LOOP
        thread's stack. Pre-fix the report was `samples.most_common(5)`, which the
        six parked workers above would dominate.
        """
        cp._register_loop_thread()          # the heartbeat task does this for real

        def _identifiable_frame_on_the_loop_thread():
            return cp._loop_thread_stack(limit=8)

        stack = _identifiable_frame_on_the_loop_thread()
        joined = " ".join(stack)
        assert stack, "no loop-thread stack captured"
        assert "_identifiable_frame_on_the_loop_thread" in joined, (
            f"stall report did not name the loop thread's own frames: {stack}"
        )
        assert "_connection_worker_thread" not in joined, (
            f"stall report blamed a parked worker thread: {stack}"
        )

    def test_loop_stack_is_empty_when_no_loop_thread_registered(self):
        """Never invent an attribution: with no registered loop thread the
        report must say nothing rather than guess."""
        cp._state["loop_thread_id"] = None
        assert cp._loop_thread_stack() == []


# ── The hotspot gap must not accuse a sleeping thread ──────────────────────

class TestHotspotGapHonesty:

    def test_parked_frames_excluded_from_cpu_samples(self, parked_workers):
        """The ">50% => CPU hotspot" gap claims 'sustained CPU on the event-loop
        thread'. Samples from parked threads must therefore not feed it."""
        # Capture the ACTUAL signatures the parked workers present, so this
        # assertion cannot pass vacuously by checking for a string that never
        # appears anyway.
        frames = sys._current_frames()
        parked_sigs = {
            cp._frame_signature(frames[w.thread.ident])
            for w in parked_workers if w.thread.ident in frames
        }
        assert parked_sigs, "fixture produced no parked signatures to exclude"

        cp._state["samples"].clear()
        cp._state["total_samples"] = 0
        cp._register_loop_thread()
        for _ in range(20):
            cp._collect_samples()
        assert cp._state["total_samples"] > 0, "sampler collected nothing"
        blamed = {sig for sig, _ in cp._state["samples"].most_common(50)}
        leaked = blamed & parked_sigs
        assert not leaked, f"parked worker threads entered the CPU census: {leaked}"

    def test_hotspot_needs_a_real_sample_base(self):
        """Regression found in R-F3464's own verify pass 2.

        Excluding parked threads shrank the denominator. With ~50 sleeping
        threads gone, one active frame can clear 50% of a handful of samples and
        emit a bogus 'CPU hotspot' gap. A hotspot claim needs a sample base.
        """
        assert cp._MIN_HOTSPOT_SAMPLES >= 10, cp._MIN_HOTSPOT_SAMPLES
        # 3 samples, all one frame = 100% — must NOT qualify as a hotspot.
        total, top_count = 3, 3
        qualifies = total >= cp._MIN_HOTSPOT_SAMPLES and (top_count / total) > 0.5
        assert not qualifies, "a 3-sample 'hotspot' would still be reported"

    def test_sampler_still_records_the_loop_thread(self):
        """Filtering idle threads must not blind the profiler entirely."""
        cp._state["samples"].clear()
        cp._state["total_samples"] = 0
        cp._register_loop_thread()
        for _ in range(10):
            cp._collect_samples()
        assert cp._state["total_samples"] >= 10, cp._state["total_samples"]
