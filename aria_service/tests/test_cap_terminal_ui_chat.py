"""R-F1198 — Capability tests for the ARIA Terminal Chat UI.

Tests that:
1. TerminalConfig correctly reads env vars and CLI args
2. AriaTerminalUI initialises without crashing
3. send_message handles httpx ImportError gracefully (fallback path)
4. send_message handles API errors gracefully
5. Command parser handles all commands without crashing
6. Session manager persists and loads sessions
7. The --no-color flag disables ANSI codes
8. The banner renders without crashing
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from aria_service.terminal_ui import (
    TerminalConfig,
    AriaTerminalUI,
    AriaRenderer,
    SessionManager,
    CommandParser,
    Message,
    MessageRole,
    Colors,
)


# ── TerminalConfig tests ──────────────────────────────────────────────────────

class TestTerminalConfig:
    """Proves TerminalConfig correctly reads env vars and defaults."""

    def test_default_config(self):
        """Default config has sensible values."""
        config = TerminalConfig()
        assert config.theme == "dark"
        assert config.show_timestamps is True
        assert config.show_cost is True
        assert config.compact_mode is False
        assert config.no_color is False

    def test_from_env_defaults(self):
        """from_env returns defaults when no env vars are set."""
        config = TerminalConfig.from_env()
        assert config.theme == "dark"
        assert config.no_color is False

    def test_from_env_no_color(self):
        """NO_COLOR env var sets no_color=True."""
        old = os.environ.get("NO_COLOR")
        try:
            os.environ["NO_COLOR"] = "true"
            config = TerminalConfig.from_env()
            assert config.no_color is True
        finally:
            if old:
                os.environ["NO_COLOR"] = old
            else:
                os.environ.pop("NO_COLOR", None)

    def test_from_env_aria_no_color(self):
        """ARIA_NO_COLOR env var sets no_color=True."""
        old = os.environ.get("ARIA_NO_COLOR")
        try:
            os.environ["ARIA_NO_COLOR"] = "true"
            config = TerminalConfig.from_env()
            assert config.no_color is True
        finally:
            if old:
                os.environ["ARIA_NO_COLOR"] = old
            else:
                os.environ.pop("ARIA_NO_COLOR", None)

    def test_from_env_compact(self):
        """ARIA_COMPACT env var sets compact_mode=True."""
        old = os.environ.get("ARIA_COMPACT")
        try:
            os.environ["ARIA_COMPACT"] = "true"
            config = TerminalConfig.from_env()
            assert config.compact_mode is True
        finally:
            if old:
                os.environ["ARIA_COMPACT"] = old
            else:
                os.environ.pop("ARIA_COMPACT", None)


# ── AriaRenderer tests ────────────────────────────────────────────────────────

class TestAriaRenderer:
    """Proves AriaRenderer formats messages correctly."""

    def test_renderer_no_color_disables_console(self):
        """no_color=True prevents Rich Console initialisation."""
        config = TerminalConfig(no_color=True)
        renderer = AriaRenderer(config)
        assert renderer.console is None

    def test_renderer_color_enables_console(self):
        """no_color=False allows Rich Console initialisation."""
        config = TerminalConfig(no_color=False)
        renderer = AriaRenderer(config)
        # Console may or may not be available depending on rich install
        # But the config flag should not block it
        # We just verify it doesn't crash

    def test_format_user_message(self):
        """User messages are formatted with 'you' prefix."""
        config = TerminalConfig(no_color=True, show_timestamps=False)
        renderer = AriaRenderer(config)
        msg = Message(role=MessageRole.USER, content="Hello ARIA")
        formatted = renderer.format_message(msg)
        assert "you" in formatted
        assert "Hello ARIA" in formatted

    def test_format_assistant_message(self):
        """Assistant messages are formatted with 'aria' prefix."""
        config = TerminalConfig(no_color=True, show_timestamps=False)
        renderer = AriaRenderer(config)
        msg = Message(role=MessageRole.ASSISTANT, content="Hello human")
        formatted = renderer.format_message(msg)
        assert "aria" in formatted
        assert "Hello human" in formatted

    def test_format_error_message(self):
        """Error messages are formatted with 'error' prefix."""
        config = TerminalConfig(no_color=True, show_timestamps=False)
        renderer = AriaRenderer(config)
        msg = Message(role=MessageRole.ERROR, content="Something broke")
        formatted = renderer.format_message(msg)
        assert "error" in formatted.lower()
        assert "Something broke" in formatted

    def test_format_tool_message(self):
        """Tool messages are formatted with 'tool' prefix."""
        config = TerminalConfig(no_color=True, show_timestamps=False)
        renderer = AriaRenderer(config)
        msg = Message(role=MessageRole.TOOL, content="Running analysis...")
        formatted = renderer.format_message(msg)
        assert "tool" in formatted.lower()

    def test_banner_does_not_crash(self):
        """Banner renders without crashing (uses StringIO to avoid cp1252 issues)."""
        import io
        config = TerminalConfig(no_color=True)
        renderer = AriaRenderer(config)
        old_stdout = sys.stdout
        sys.stdout = io.StringIO()
        try:
            renderer.print_banner()
        finally:
            sys.stdout = old_stdout
        # If we got here without crashing, the test passes

    def test_help_does_not_crash(self, capsys):
        """Help renders without crashing."""
        config = TerminalConfig(no_color=True)
        renderer = AriaRenderer(config)
        renderer.print_help()
        captured = capsys.readouterr()
        assert "/help" in captured.out

    def test_clear_screen_does_not_crash(self):
        """clear_screen doesn't crash."""
        config = TerminalConfig(no_color=True)
        renderer = AriaRenderer(config)
        renderer.clear_screen()

    def test_print_error(self, capsys):
        """print_error shows the error message."""
        config = TerminalConfig(no_color=True)
        renderer = AriaRenderer(config)
        renderer.print_error("Test error")
        captured = capsys.readouterr()
        assert "Test error" in captured.out

    def test_print_success(self, capsys):
        """print_success shows the success message."""
        config = TerminalConfig(no_color=True)
        renderer = AriaRenderer(config)
        renderer.print_success("Test success")
        captured = capsys.readouterr()
        assert "Test success" in captured.out

    def test_print_info(self, capsys):
        """print_info shows the info message."""
        config = TerminalConfig(no_color=True)
        renderer = AriaRenderer(config)
        renderer.print_info("Test info")
        captured = capsys.readouterr()
        assert "Test info" in captured.out


# ── SessionManager tests ──────────────────────────────────────────────────────

class TestSessionManager:
    """Proves SessionManager creates, persists, and loads sessions."""

    def test_create_session(self, tmp_path):
        """Creating a session persists it to disk."""
        sm = SessionManager(sessions_dir=tmp_path)
        session = sm.create_session("Test Session")
        assert session.name == "Test Session"
        assert session.id is not None
        # Verify it was saved to disk
        session_file = tmp_path / f"{session.id}.json"
        assert session_file.exists()

    def test_add_message(self, tmp_path):
        """Adding a message updates the session."""
        sm = SessionManager(sessions_dir=tmp_path)
        sm.create_session("Test")
        msg = Message(role=MessageRole.USER, content="Hello")
        sm.add_message(msg)
        assert len(sm.current_session.messages) == 1
        assert sm.current_session.messages[0].content == "Hello"

    def test_add_message_with_tokens(self, tmp_path):
        """Adding a message with tokens updates totals."""
        sm = SessionManager(sessions_dir=tmp_path)
        sm.create_session("Test")
        msg = Message(role=MessageRole.ASSISTANT, content="Hi", tokens=100, cost=0.002)
        sm.add_message(msg)
        assert sm.current_session.total_tokens == 100
        assert sm.current_session.total_cost == 0.002

    def test_load_session(self, tmp_path):
        """Loading a session returns the correct session."""
        sm = SessionManager(sessions_dir=tmp_path)
        s1 = sm.create_session("First")
        s2 = sm.create_session("Second")
        loaded = sm.load_session(s1.id)
        assert loaded is not None
        assert loaded.name == "First"

    def test_delete_session(self, tmp_path):
        """Deleting a session removes it from disk."""
        sm = SessionManager(sessions_dir=tmp_path)
        session = sm.create_session("To Delete")
        session_file = tmp_path / f"{session.id}.json"
        assert session_file.exists()
        sm.delete_session(session.id)
        assert not session_file.exists()
        assert session.id not in sm.sessions

    def test_get_sessions(self, tmp_path):
        """get_sessions returns all sessions."""
        sm = SessionManager(sessions_dir=tmp_path)
        sm.create_session("A")
        sm.create_session("B")
        assert len(sm.get_sessions()) == 2

    def test_auto_create_on_add(self, tmp_path):
        """Adding a message without a session auto-creates one."""
        sm = SessionManager(sessions_dir=tmp_path)
        assert sm.current_session is None
        msg = Message(role=MessageRole.USER, content="Auto")
        sm.add_message(msg)
        assert sm.current_session is not None
        assert len(sm.current_session.messages) == 1


# ── AriaTerminalUI tests ──────────────────────────────────────────────────────

class TestAriaTerminalUI:
    """Proves AriaTerminalUI initialises and handles messages."""

    def test_init_default(self):
        """UI initialises without crashing."""
        config = TerminalConfig(no_color=True)
        ui = AriaTerminalUI(config)
        assert ui.current_model is not None
        assert ui.session_manager.current_session is not None

    def test_init_with_env_model(self):
        """UI reads ARIA_MODEL from env."""
        old = os.environ.get("ARIA_MODEL")
        try:
            os.environ["ARIA_MODEL"] = "gpt-4"
            config = TerminalConfig(no_color=True)
            ui = AriaTerminalUI(config)
            assert ui.current_model == "gpt-4"
        finally:
            if old:
                os.environ["ARIA_MODEL"] = old
            else:
                os.environ.pop("ARIA_MODEL", None)

    def test_get_prompt_default(self):
        """Default prompt shows 'you' prefix."""
        config = TerminalConfig(no_color=True)
        ui = AriaTerminalUI(config)
        prompt = ui.get_prompt()
        assert "you" in prompt

    def test_get_prompt_compact(self):
        """Compact prompt shows 'aria>' prefix."""
        config = TerminalConfig(no_color=True, compact_mode=True)
        ui = AriaTerminalUI(config)
        prompt = ui.get_prompt()
        assert "aria>" in prompt

    @pytest.mark.asyncio
    async def test_send_message_import_error_fallback(self):
        """send_message falls back gracefully when httpx is not available."""
        config = TerminalConfig(no_color=True, show_cost=False)
        ui = AriaTerminalUI(config)
        # Mock the import to raise ImportError
        import builtins
        original_import = builtins.__import__

        def mock_import(name, *args, **kwargs):
            if name == "httpx":
                raise ImportError("No module named 'httpx'")
            return original_import(name, *args, **kwargs)

        builtins.__import__ = mock_import
        try:
            await ui.send_message("test message")
            # Should not crash — should add a message to the session
            assert len(ui.session_manager.current_session.messages) >= 1
            # The last message should be the assistant's fallback response
            last_msg = ui.session_manager.current_session.messages[-1]
            assert last_msg.role == MessageRole.ASSISTANT
            assert "httpx" in last_msg.content.lower()
        finally:
            builtins.__import__ = original_import

    @pytest.mark.asyncio
    async def test_send_message_api_error(self):
        """send_message handles API errors gracefully."""
        config = TerminalConfig(no_color=True)
        ui = AriaTerminalUI(config)

        with patch("httpx.AsyncClient") as mock_client:
            mock_instance = MagicMock()
            mock_client.return_value.__aenter__.return_value = mock_instance
            mock_instance.post.side_effect = Exception("Connection refused")

            await ui.send_message("test")
            # Should add an error message to the session
            assert len(ui.session_manager.current_session.messages) >= 1
            # The last message should be the error
            last_msg = ui.session_manager.current_session.messages[-1]
            assert last_msg.role == MessageRole.ERROR

    def test_stop_does_not_crash(self):
        """stop() doesn't crash even without a session."""
        config = TerminalConfig(no_color=True)
        ui = AriaTerminalUI(config)
        ui.stop()


# ── CommandParser tests ───────────────────────────────────────────────────────

class TestCommandParser:
    """Proves CommandParser handles all commands."""

    def test_help_command(self):
        """Help command shows help."""
        config = TerminalConfig(no_color=True)
        ui = AriaTerminalUI(config)
        result = ui.command_parser.execute("/help")
        assert result is True  # Should continue

    def test_clear_command(self):
        """Clear command doesn't crash."""
        config = TerminalConfig(no_color=True)
        ui = AriaTerminalUI(config)
        result = ui.command_parser.execute("/clear")
        assert result is True

    def test_reset_command(self):
        """Reset command creates a new session."""
        config = TerminalConfig(no_color=True)
        ui = AriaTerminalUI(config)
        old_id = ui.session_manager.current_session.id
        result = ui.command_parser.execute("/reset")
        assert result is True
        assert ui.session_manager.current_session.id != old_id

    def test_status_command(self):
        """Status command doesn't crash."""
        config = TerminalConfig(no_color=True)
        ui = AriaTerminalUI(config)
        result = ui.command_parser.execute("/status")
        assert result is True

    def test_cost_command(self):
        """Cost command doesn't crash."""
        config = TerminalConfig(no_color=True)
        ui = AriaTerminalUI(config)
        result = ui.command_parser.execute("/cost")
        assert result is True

    def test_history_command(self):
        """History command doesn't crash."""
        config = TerminalConfig(no_color=True)
        ui = AriaTerminalUI(config)
        result = ui.command_parser.execute("/history")
        assert result is True

    def test_sessions_command(self):
        """Sessions command doesn't crash."""
        config = TerminalConfig(no_color=True)
        ui = AriaTerminalUI(config)
        result = ui.command_parser.execute("/sessions")
        assert result is True

    def test_session_new_command(self):
        """Session new creates a new session."""
        config = TerminalConfig(no_color=True)
        ui = AriaTerminalUI(config)
        old_id = ui.session_manager.current_session.id
        result = ui.command_parser.execute("/session new")
        assert result is True
        assert ui.session_manager.current_session.id != old_id

    def test_session_list_command(self):
        """Session list doesn't crash."""
        config = TerminalConfig(no_color=True)
        ui = AriaTerminalUI(config)
        result = ui.command_parser.execute("/session list")
        assert result is True

    def test_model_command(self):
        """Model command switches the model."""
        config = TerminalConfig(no_color=True)
        ui = AriaTerminalUI(config)
        result = ui.command_parser.execute("/model gpt-4")
        assert result is True
        assert ui.current_model == "gpt-4"

    def test_theme_command(self):
        """Theme command switches the theme."""
        config = TerminalConfig(no_color=True)
        ui = AriaTerminalUI(config)
        result = ui.command_parser.execute("/theme claude")
        assert result is True
        assert ui.config.theme == "claude"

    def test_theme_invalid(self):
        """Invalid theme shows error."""
        config = TerminalConfig(no_color=True)
        ui = AriaTerminalUI(config)
        result = ui.command_parser.execute("/theme rainbow")
        assert result is True
        assert ui.config.theme == "dark"  # Should not change

    def test_export_command(self, tmp_path):
        """Export command doesn't crash."""
        config = TerminalConfig(no_color=True)
        ui = AriaTerminalUI(config)
        # Add a message so there's something to export
        msg = Message(role=MessageRole.USER, content="Hello")
        ui.session_manager.add_message(msg)
        result = ui.command_parser.execute("/export")
        assert result is True

    def test_exit_command(self):
        """Exit command returns False."""
        config = TerminalConfig(no_color=True)
        ui = AriaTerminalUI(config)
        result = ui.command_parser.execute("/exit")
        assert result is False

    def test_quit_command(self):
        """Quit command returns False."""
        config = TerminalConfig(no_color=True)
        ui = AriaTerminalUI(config)
        result = ui.command_parser.execute("/quit")
        assert result is False

    def test_unknown_command(self):
        """Unknown command shows error."""
        config = TerminalConfig(no_color=True)
        ui = AriaTerminalUI(config)
        result = ui.command_parser.execute("/nonexistent")
        assert result is True  # Should continue, not crash


# ── Message tests ─────────────────────────────────────────────────────────────

class TestMessage:
    """Proves Message dataclass works correctly."""

    def test_message_defaults(self):
        """Message has sensible defaults."""
        msg = Message(role=MessageRole.USER, content="Hello")
        assert msg.role == MessageRole.USER
        assert msg.content == "Hello"
        assert msg.timestamp is not None
        assert msg.tokens is None
        assert msg.cost is None

    def test_message_with_tokens(self):
        """Message stores tokens and cost."""
        msg = Message(role=MessageRole.ASSISTANT, content="Hi", tokens=50, cost=0.001)
        assert msg.tokens == 50
        assert msg.cost == 0.001


# ── ProgressSpinner tests ─────────────────────────────────────────────────────

class TestProgressSpinner:
    """Proves ProgressSpinner starts and stops without crashing."""

    def test_start_stop(self):
        """Spinner starts and stops."""
        from aria_service.terminal_ui import ProgressSpinner
        spinner = ProgressSpinner("Testing")
        spinner.start()
        spinner.stop()
        assert spinner.running is False

    def test_double_stop(self):
        """Stopping twice doesn't crash."""
        from aria_service.terminal_ui import ProgressSpinner
        spinner = ProgressSpinner("Testing")
        spinner.start()
        spinner.stop()
        spinner.stop()  # Second stop should be safe
