"""R-F1149 — Deadlock Detector for ARIA.

Detects threads that have stopped making progress (stuck for longer than a
configurable timeout) and reports them to the brain. Unlike the continuous
profiler (which samples stack traces for CPU hotspots), this detector tracks
per-thread activity heartbeats and flags threads whose last activity is stale.

Architecture:
  - Threads register themselves with a name and activity callback
  - A periodic scan checks each thread's last-activity timestamp
  - Threads idle longer than the timeout are flagged with their stack trace
  - Findings are wired to brain_hook via capability_gaps

Usage:
  from aria_service.intel.deadlock_detector import DeadlockDetector
  detector = DeadlockDetector(timeout_seconds=30)
  detector.watch_thread(threading.current_thread().ident, "main", "event_loop")
  # In each thread's loop:
  detector.update_activity(threading.current_thread().ident)
  # Periodic scan:
  deadlocks = detector.scan()

Environment:
  ARIA_DEADLOCK_TIMEOUT_S=30  (seconds before a thread is considered stuck)
  ARIA_DEADLOCK_SCAN_INTERVAL_S=15  (how often to scan, default 15)
"""
from __future__ import annotations
from .engine_wiring import wire_success, wire_failure

import asyncio
import logging
import os
import sys
import threading
import time
import traceback
from typing import Any, Optional

logger = logging.getLogger("aria.deadlock_detector")

_TIMEOUT_S = int(os.getenv("ARIA_DEADLOCK_TIMEOUT_S", "30"))
_SCAN_INTERVAL_S = int(os.getenv("ARIA_DEADLOCK_SCAN_INTERVAL_S", "15"))


class DeadlockDetector:
    """Detects threads that have stopped making progress.

    Threads register with watch_thread() and call update_activity() periodically.
    The detector scans all watched threads and flags any whose last activity
    is older than the timeout.
    """

    def __init__(self, timeout_seconds: int = _TIMEOUT_S) -> None:
        self.timeout = timeout_seconds
        self._threads: dict[int, dict[str, Any]] = {}
        self._lock = threading.Lock()
        self._running = False

    def watch_thread(self, thread_id: int, name: str, task: str = "") -> None:
        """Register a thread for deadlock monitoring.

        Args:
            thread_id: threading.current_thread().ident
            name: Human-readable name for the thread
            task: Description of what the thread is doing
        """
        with self._lock:
            self._threads[thread_id] = {
                "name": name,
                "task": task,
                "started_at": time.time(),
                "last_activity": time.time(),
                "stack": None,
            }

    def unwatch_thread(self, thread_id: int) -> None:
        """Remove a thread from monitoring (e.g. on clean shutdown)."""
        with self._lock:
            self._threads.pop(thread_id, None)

    def update_activity(self, thread_id: int) -> None:
        """Mark a thread as active now.

        Called periodically by the watched thread itself.
        """
        with self._lock:
            info = self._threads.get(thread_id)
            if info is not None:
                info["last_activity"] = time.time()

    def scan(self) -> list[dict[str, Any]]:
        """Scan all watched threads for potential deadlocks.

        Returns a list of deadlock records, one per stuck thread.
        Each record contains:
          - thread_id: the thread identifier
          - name: the thread's registered name
          - task: what the thread was doing
          - idle_seconds: how long since last activity
          - stack: captured stack trace (list of strings)
        """
        deadlocks: list[dict[str, Any]] = []
        now = time.time()

        with self._lock:
            for tid, info in list(self._threads.items()):
                idle_time = now - info["last_activity"]
                if idle_time > self.timeout:
                    # Capture stack trace for this thread
                    stack_lines: list[str] = []
                    try:
                        frames = sys._current_frames()
                        frame = frames.get(tid)
                        if frame is not None:
                            stack_lines = traceback.format_stack(frame)
                    except Exception:
                        stack_lines = ["<could not capture stack>"]

                    info["stack"] = stack_lines

                    deadlocks.append({
                        "thread_id": tid,
                        "name": info["name"],
                        "task": info["task"],
                        "idle_seconds": idle_time,
                        "stack": stack_lines,
                    })

        return deadlocks

    async def run_forever(self) -> None:
        """Periodically scan for deadlocks and report findings."""
        self._running = True
        logger.info(
            "[deadlock_detector] Started (timeout=%ds, scan=%ds)",
            self.timeout, _SCAN_INTERVAL_S,
        )

        while self._running:
            try:
                await asyncio.sleep(_SCAN_INTERVAL_S)
                if not self._running:
                    break

                deadlocks = self.scan()
                if deadlocks:
                    for dl in deadlocks:
                        logger.warning(
                            "[deadlock_detector] Thread '%s' (tid=%d, task=%s) "
                            "idle for %.0fs — possible deadlock",
                            dl["name"], dl["thread_id"], dl["task"],
                            dl["idle_seconds"],
                        )
                        # Log top of stack for debugging
                        if dl["stack"]:
                            for line in dl["stack"][-5:]:
                                logger.warning("[deadlock_detector]   %s", line.strip())

                    self._emit_deadlock_signal(deadlocks)

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("[deadlock_detector] Error: %s", e)

    def stop(self) -> None:
        """Stop the monitoring loop."""
        self._running = False

    def _emit_deadlock_signal(self, deadlocks: list[dict[str, Any]]) -> None:
        """Fire-and-forget a brain signal about detected deadlocks."""
        try:
            from . import capability_gaps as _cg
            import asyncio as _aio
            names = ", ".join(dl["name"] for dl in deadlocks)
            _aio.create_task(_cg.record_gap(
                gap_type="performance",
                severity=4,
                title=f"Deadlock detected: {len(deadlocks)} thread(s) stuck",
                detail=(
                    f"Thread(s) idle beyond {self.timeout}s timeout: {names}. "
                    f"Longest idle: {max(dl['idle_seconds'] for dl in deadlocks):.0f}s"
                ),
                source="deadlock_detector",
            ))
        except Exception:
            pass

    def get_status(self) -> dict[str, Any]:
        """Return current status for the orchestrator."""
        with self._lock:
            watched = {
                str(tid): {
                    "name": info["name"],
                    "task": info["task"],
                    "idle_seconds": time.time() - info["last_activity"],
                }
                for tid, info in self._threads.items()
            }
        return {
            "running": self._running,
            "watched_threads": len(self._threads),
            "threads": watched,
        }

    # R-F2118/R-F2119 §21a — wire module active
    try:
        wire_success(module="deadlock_detector",
                     summary="deadlock_detector module active",
                     source_id="deadlock_detector:init")
    except Exception:
        try:
            wire_failure(module="deadlock_detector", detail="module init failed",
                        gap_type="engine_failure", source="deadlock_detector:init")
        except Exception:
            pass
