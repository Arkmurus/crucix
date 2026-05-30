"""R-F1149 — Capability test for DeadlockDetector.

Verifies that the detector:
1. Can watch/unwatch threads
2. Tracks activity timestamps
3. scan() detects idle threads past the timeout
4. Does not flag active threads
5. Captures stack traces for stuck threads
"""
from __future__ import annotations

import threading
import time
import pytest


class TestDeadlockDetector:
    """Capability test: DeadlockDetector must track threads and detect stalls."""

    @pytest.mark.asyncio
    async def test_watch_and_unwatch(self) -> None:
        """watch_thread and unwatch_thread must work."""
        from aria_service.intel.deadlock_detector import DeadlockDetector

        detector = DeadlockDetector(timeout_seconds=30)
        tid = threading.current_thread().ident
        assert tid is not None

        detector.watch_thread(tid, "test_thread", "testing")
        assert tid in detector._threads
        assert detector._threads[tid]["name"] == "test_thread"
        assert detector._threads[tid]["task"] == "testing"

        detector.unwatch_thread(tid)
        assert tid not in detector._threads

    @pytest.mark.asyncio
    async def test_update_activity(self) -> None:
        """update_activity must refresh the last_activity timestamp."""
        from aria_service.intel.deadlock_detector import DeadlockDetector

        detector = DeadlockDetector(timeout_seconds=30)
        tid = threading.current_thread().ident
        assert tid is not None

        detector.watch_thread(tid, "test_thread")
        original = detector._threads[tid]["last_activity"]

        time.sleep(0.01)
        detector.update_activity(tid)
        assert detector._threads[tid]["last_activity"] > original

    @pytest.mark.asyncio
    async def test_scan_detects_stuck_thread(self) -> None:
        """scan() must detect threads idle past the timeout."""
        from aria_service.intel.deadlock_detector import DeadlockDetector

        # Very short timeout so we can trigger detection
        detector = DeadlockDetector(timeout_seconds=0.05)
        tid = threading.current_thread().ident
        assert tid is not None

        detector.watch_thread(tid, "stuck_thread", "stuck_task")
        # Don't update activity — let it go stale
        time.sleep(0.1)

        deadlocks = detector.scan()
        assert len(deadlocks) >= 1
        assert deadlocks[0]["name"] == "stuck_thread"
        assert deadlocks[0]["task"] == "stuck_task"
        assert deadlocks[0]["idle_seconds"] >= 0.05
        assert isinstance(deadlocks[0]["stack"], list)

    @pytest.mark.asyncio
    async def test_scan_does_not_flag_active_thread(self) -> None:
        """scan() must not flag threads that are actively updating."""
        from aria_service.intel.deadlock_detector import DeadlockDetector

        detector = DeadlockDetector(timeout_seconds=30)
        tid = threading.current_thread().ident
        assert tid is not None

        detector.watch_thread(tid, "active_thread", "working")
        detector.update_activity(tid)  # Mark as active now

        deadlocks = detector.scan()
        matching = [d for d in deadlocks if d["thread_id"] == tid]
        assert len(matching) == 0, "Active thread must not be flagged"

    @pytest.mark.asyncio
    async def test_get_status(self) -> None:
        """get_status() must return the expected structure."""
        from aria_service.intel.deadlock_detector import DeadlockDetector

        detector = DeadlockDetector()
        status = detector.get_status()

        assert isinstance(status, dict)
        assert "running" in status
        assert "watched_threads" in status
        assert "threads" in status
        assert status["running"] is False
        assert status["watched_threads"] == 0

    @pytest.mark.asyncio
    async def test_run_forever_start_stop(self) -> None:
        """run_forever must start and stop cleanly."""
        from aria_service.intel.deadlock_detector import DeadlockDetector

        detector = DeadlockDetector(timeout_seconds=30)
        import asyncio

        task = asyncio.create_task(detector.run_forever())
        await asyncio.sleep(0.1)
        assert detector._running

        detector.stop()
        task.cancel()
        try:
            await task
        except (asyncio.CancelledError, Exception):
            pass

        assert not detector._running
