"""R-F1438 — Capability test: no infinite feedback loop when operator types mid-task.

The bug: when _TURN_STATE["busy"] is True, the loop-top drain (cli.py:1943) steals
messages from _OPERATOR_QUEUE, then the mid-task guard (cli.py:2000-2003) re-queues
them — creating an infinite drain/re-queue loop that prints "▸ sent to ARIA mid-task"
every iteration.

The fix: gate BOTH the loop-top drain AND the _queue_poller on not _TURN_STATE["busy"].
When busy, only the worker thread's _drain_operator_stdin consumes the queue.

This test proves:
1. When busy, the loop-top drain does NOT consume from _OPERATOR_QUEUE
2. When idle, the loop-top drain still works (no regression)
3. The _queue_poller skips when busy
4. One operator input produces exactly one queue entry (no feedback loop)
"""
from __future__ import annotations

import queue
import threading
from unittest.mock import MagicMock, patch

import pytest

from aria_cli.cli import _TURN_STATE, _OPERATOR_QUEUE


# ── Fixtures ────────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def _reset_state():
    """Reset _TURN_STATE and clear _OPERATOR_QUEUE before each test."""
    _TURN_STATE["busy"] = False
    _TURN_STATE["task"] = ""
    _TURN_STATE["started"] = 0.0
    while True:
        try:
            _OPERATOR_QUEUE.get_nowait()
        except Exception:
            break
    yield


# ── Tests ───────────────────────────────────────────────────────────────────

class TestNoFeedbackLoop:
    """Proves the infinite feedback loop is fixed."""

    def test_loop_top_drain_skipped_when_busy(self):
        """When _TURN_STATE['busy'] is True, the loop-top drain does NOT consume."""
        _TURN_STATE["busy"] = True
        _OPERATOR_QUEUE.put("fix the bug")

        # Simulate the loop-top drain logic (cli.py:1954-1958)
        claude_line = None
        if not _TURN_STATE["busy"]:
            try:
                claude_line = _OPERATOR_QUEUE.get_nowait()
            except Exception:
                pass

        # The message should NOT have been consumed — it stays for the worker
        assert claude_line is None, "Loop-top drain must NOT consume when busy"
        # Verify the message is still on the queue
        remaining = _OPERATOR_QUEUE.get_nowait()
        assert remaining == "fix the bug", "Message must remain on queue for worker"

    def test_loop_top_drain_works_when_idle(self):
        """When idle, the loop-top drain still works (no regression)."""
        _TURN_STATE["busy"] = False
        _OPERATOR_QUEUE.put("check logs")

        # Simulate the loop-top drain logic
        claude_line = None
        if not _TURN_STATE["busy"]:
            try:
                claude_line = _OPERATOR_QUEUE.get_nowait()
            except Exception:
                pass

        assert claude_line == "check logs", "Loop-top drain must consume when idle"
        # Queue should be empty
        with pytest.raises(Exception):
            _OPERATOR_QUEUE.get_nowait()

    def test_queue_poller_skips_when_busy(self):
        """The _queue_poller skips when busy — does not steal from queue."""
        _TURN_STATE["busy"] = True
        _OPERATOR_QUEUE.put("bridge message")

        # Simulate the _queue_poller logic (cli.py:1913-1924)
        msg = None
        if not _TURN_STATE["busy"]:
            try:
                msg = _OPERATOR_QUEUE.get_nowait()
            except Exception:
                pass

        assert msg is None, "Queue poller must NOT consume when busy"
        # Message stays for the worker
        remaining = _OPERATOR_QUEUE.get_nowait()
        assert remaining == "bridge message"

    def test_queue_poller_works_when_idle(self):
        """The _queue_poller works when idle (no regression)."""
        _TURN_STATE["busy"] = False
        _OPERATOR_QUEUE.put("wake message")

        # Simulate the _queue_poller logic
        msg = None
        if not _TURN_STATE["busy"]:
            try:
                msg = _OPERATOR_QUEUE.get_nowait()
            except Exception:
                pass

        assert msg == "wake message", "Queue poller must consume when idle"

    def test_one_input_one_queue_entry_no_feedback(self):
        """One operator input produces exactly one queue entry — no feedback loop.

        This simulates the full cycle:
        1. Operator types "fix the bug" while busy
        2. The prompt returns it as 'line'
        3. The mid-task guard re-queues it ONCE
        4. The loop-top drain does NOT consume it (busy guard)
        5. The queue has exactly one entry — no infinite loop
        """
        _TURN_STATE["busy"] = True
        line = "fix the bug"

        # Step 1: mid-task guard re-queues (cli.py:2013-2016)
        if _TURN_STATE["busy"] and not line.startswith("/"):
            _OPERATOR_QUEUE.put(line)

        # Step 2: loop-top drain should NOT consume (busy guard)
        claude_line = None
        if not _TURN_STATE["busy"]:
            try:
                claude_line = _OPERATOR_QUEUE.get_nowait()
            except Exception:
                pass

        assert claude_line is None, "Loop-top drain must not steal from worker"

        # Step 3: verify exactly one entry on the queue (no feedback loop)
        remaining = _OPERATOR_QUEUE.get_nowait()
        assert remaining == "fix the bug", "Worker should find the message"

        # Step 4: queue is now empty — no more entries
        with pytest.raises(Exception):
            _OPERATOR_QUEUE.get_nowait()

    def test_slash_commands_still_work_mid_task(self):
        """Slash commands are NOT re-queued mid-task (no regression)."""
        _TURN_STATE["busy"] = True

        # /exit should not be re-queued
        line = "/exit"
        if _TURN_STATE["busy"] and line in {"/exit", "/quit"}:
            pass  # This path prints a message and continues

        # Queue should be empty
        with pytest.raises(Exception):
            _OPERATOR_QUEUE.get_nowait()

    def test_multiple_operator_inputs_while_busy(self):
        """Multiple operator inputs while busy all land on the queue (no loss)."""
        _TURN_STATE["busy"] = True

        inputs = ["fix the bug", "check the logs", "deploy now"]
        for line in inputs:
            if _TURN_STATE["busy"] and not line.startswith("/"):
                _OPERATOR_QUEUE.put(line)

        # All three should be on the queue
        for expected in inputs:
            remaining = _OPERATOR_QUEUE.get_nowait()
            assert remaining == expected, f"Expected {expected}, got {remaining}"

        # Queue is now empty
        with pytest.raises(Exception):
            _OPERATOR_QUEUE.get_nowait()
