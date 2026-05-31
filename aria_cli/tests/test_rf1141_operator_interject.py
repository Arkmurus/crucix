"""R-F1141 — Capability tests for operator mid-task interject.

Tests that:
1. _drain_operator_stdin reads from the queue and injects into messages
2. Messages are tagged as [OPERATOR (mid-task)]
3. Empty lines are skipped
4. The drain is non-blocking (returns immediately if queue is empty)
5. The stdin reader thread starts correctly
"""
from __future__ import annotations

import queue
from unittest.mock import MagicMock, patch

import pytest

from aria_cli.agent import Agent
from aria_cli.llm import LLMClient


# ── Fixtures ────────────────────────────────────────────────────────────────

@pytest.fixture
def mock_agent() -> Agent:
    """Create an Agent with a mock LLM and toolbox."""
    llm = MagicMock(spec=LLMClient)
    llm.chat.return_value = ("Final answer.", {"input_tokens": 10, "output_tokens": 5})
    toolbox = MagicMock()
    toolbox.bridge_base = None  # No Claude bridge
    ui = MagicMock()
    agent = Agent(llm=llm, toolbox=toolbox, ui=ui, system_prompt="test")
    return agent


# ── Tests for _drain_operator_stdin ─────────────────────────────────────────

class TestDrainOperatorStdin:
    """Proves the operator stdin drain works correctly."""

    def test_injects_operator_message(self, mock_agent: Agent):
        """An operator line on the queue is injected into messages."""
        q = queue.Queue()
        q.put("Stop what you are doing and check the logs")

        with patch("aria_cli.cli._OPERATOR_QUEUE", q):
            mock_agent._drain_operator_stdin()

        # messages[0] is system prompt, messages[1] is the injected operator msg
        assert len(mock_agent.messages) == 2
        msg = mock_agent.messages[1]
        assert msg["role"] == "user"
        assert "OPERATOR" in msg["content"]
        assert "Stop what you are doing" in msg["content"]

    def test_injects_multiple_messages(self, mock_agent: Agent):
        """Multiple queued lines are all injected."""
        q = queue.Queue()
        q.put("First message")
        q.put("Second message")

        with patch("aria_cli.cli._OPERATOR_QUEUE", q):
            mock_agent._drain_operator_stdin()

        # messages[0] is system prompt, messages[1] and [2] are operator msgs
        assert len(mock_agent.messages) == 3

    def test_skips_empty_lines(self, mock_agent: Agent):
        """Empty lines on the queue are skipped."""
        q = queue.Queue()
        q.put("")
        q.put("  ")
        q.put("Real message")

        with patch("aria_cli.cli._OPERATOR_QUEUE", q):
            mock_agent._drain_operator_stdin()

        # messages[0] is system prompt, messages[1] is the real message
        assert len(mock_agent.messages) == 2
        assert "Real message" in mock_agent.messages[1]["content"]

    def test_non_blocking_on_empty_queue(self, mock_agent: Agent):
        """Drain returns immediately when queue is empty (non-blocking)."""
        q = queue.Queue()

        with patch("aria_cli.cli._OPERATOR_QUEUE", q):
            mock_agent._drain_operator_stdin()

        # Only the system prompt
        assert len(mock_agent.messages) == 1

    def test_never_breaks_on_missing_module(self, mock_agent: Agent):
        """Drain never breaks the agent loop if cli module is not available."""
        with patch("aria_cli.cli._OPERATOR_QUEUE", new_callable=MagicMock) as mock_q:
            mock_q.get_nowait.side_effect = AttributeError("No module")
            mock_agent._drain_operator_stdin()  # Should not raise

        # Only the system prompt
        assert len(mock_agent.messages) == 1

    def test_echoes_to_ui(self, mock_agent: Agent):
        """Operator messages are echoed to the UI via operator_message()."""
        q = queue.Queue()
        q.put("Check the logs")

        with patch("aria_cli.cli._OPERATOR_QUEUE", q):
            mock_agent._drain_operator_stdin()

        mock_agent.ui.operator_message.assert_called_once()
        call_arg = mock_agent.ui.operator_message.call_args[0][0]
        assert "Check the logs" in call_arg


# ── Tests for the stdin reader thread ───────────────────────────────────────

class TestStdinReader:
    """Proves the stdin reader thread starts correctly."""

    def test_start_stdin_reader(self):
        """_start_stdin_reader starts the daemon thread."""
        import aria_cli.cli as cli_mod
        cli_mod._STDIN_THREAD = None

        cli_mod._start_stdin_reader()
        assert cli_mod._STDIN_THREAD is not None
        assert cli_mod._STDIN_THREAD.daemon is True
        assert cli_mod._STDIN_THREAD.name == "stdin-reader"

    def test_start_stdin_reader_idempotent(self):
        """Starting the reader twice does not create two threads."""
        import aria_cli.cli as cli_mod
        cli_mod._STDIN_THREAD = None

        cli_mod._start_stdin_reader()
        t1 = cli_mod._STDIN_THREAD
        # Second call should return the same thread (or a new one if first died)
        cli_mod._start_stdin_reader()
        t2 = cli_mod._STDIN_THREAD

        # Either same thread, or t1 is a stopped daemon and t2 is new
        assert t1 is t2 or (not t1.is_alive() and t2 is not None)
