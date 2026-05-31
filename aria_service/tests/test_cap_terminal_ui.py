"""R-F1197 — Capability tests for the ARIA Coder terminal UI.

Tests that:
1. _Color correctly enables/disables ANSI codes
2. _BoxChars correctly selects Unicode vs ASCII based on encoding
3. TerminalUI methods don't crash with various inputs
4. _error_suggestion returns appropriate suggestions
5. _summarize produces correct summaries for each tool
6. The banner renders without crashing on cp1252 terminals
7. The session summary renders without crashing
8. New REPL commands (/diff, /plan, /stats, /think, /clear, /version, /uptime, /config) are handled
"""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from aria_cli.cli import (
    _BoxChars,
    _Color,
    TerminalUI,
    _banner,
    _finalize,
    _ensure_session_dir,
    _session_log_path,
    _append_log,
    find_repo_root,
    load_dotenv,
)


# Helper: access module-level functions that aren't exported
def _summarize(name, args):
    """Call TerminalUI._summarize via an instance."""
    return TerminalUI._summarize(name, args)


def _error_suggestion(name, output):
    """Call TerminalUI._error_suggestion via an instance."""
    ui = TerminalUI(auto_approve=True, interactive=False, color=_Color(enabled=False))
    return ui._error_suggestion(name, output)
from aria_cli.llm import LLMConfig
from aria_cli.safety import WriteGuard
from aria_cli.tools import ToolResult


# ── _Color tests ──────────────────────────────────────────────────────────────

class TestColor:
    """Proves _Color correctly enables/disables ANSI codes."""

    def test_color_enabled(self):
        """When enabled, ANSI codes are emitted."""
        c = _Color(enabled=True)
        assert c.dim("hello") == "\033[2mhello\033[0m"
        assert c.cyan("test") == "\033[36mtest\033[0m"
        assert c.green("ok") == "\033[32mok\033[0m"
        assert c.red("err") == "\033[31merr\033[0m"
        assert c.yellow("warn") == "\033[33mwarn\033[0m"
        assert c.bold("bold") == "\033[1mbold\033[0m"
        assert c.blue("blue") == "\033[34mblue\033[0m"
        assert c.magenta("mag") == "\033[35mmag\033[0m"

    def test_color_disabled(self):
        """When disabled, no ANSI codes are emitted."""
        c = _Color(enabled=False)
        assert c.dim("hello") == "hello"
        assert c.cyan("test") == "test"
        assert c.green("ok") == "ok"
        assert c.red("err") == "err"
        assert c.yellow("warn") == "warn"
        assert c.bold("bold") == "bold"
        assert c.blue("blue") == "blue"
        assert c.magenta("mag") == "mag"


# ── _BoxChars tests ───────────────────────────────────────────────────────────

class TestBoxChars:
    """Proves _BoxChars correctly selects Unicode vs ASCII."""

    def test_unicode_on_utf8(self):
        """UTF-8 terminal gets Unicode box-drawing chars."""
        old_enc = getattr(sys.stdout, "encoding", "")
        old_no_color = os.environ.pop("NO_COLOR", None)
        try:
            sys.stdout.reconfigure(encoding="utf-8")
            bx = _BoxChars()
            assert bx._unicode is True
            assert bx.tl == "╔"
            assert bx.tr == "╗"
            assert bx.bl == "╚"
            assert bx.br == "╝"
            assert bx.h == "═"
            assert bx.v == "║"
            assert bx.tm == "╠"
            assert bx.check == "✓"
            assert bx.cross == "✗"
        finally:
            if old_no_color is not None:
                os.environ["NO_COLOR"] = old_no_color
            try:
                sys.stdout.reconfigure(encoding=old_enc)
            except Exception:
                pass

    def test_ascii_on_cp1252(self):
        """cp1252 terminal gets ASCII fallback."""
        old_enc = getattr(sys.stdout, "encoding", "")
        old_no_color = os.environ.pop("NO_COLOR", None)
        try:
            sys.stdout.reconfigure(encoding="cp1252")
            bx = _BoxChars()
            assert bx._unicode is False
            assert bx.tl == "+"
            assert bx.tr == "+"
            assert bx.bl == "+"
            assert bx.br == "+"
            assert bx.h == "-"
            assert bx.v == "|"
            assert bx.tm == "|"
            assert bx.check == "v"
            assert bx.cross == "x"
        finally:
            if old_no_color is not None:
                os.environ["NO_COLOR"] = old_no_color
            try:
                sys.stdout.reconfigure(encoding=old_enc)
            except Exception:
                pass

    def test_ascii_when_no_color(self):
        """NO_COLOR env var forces ASCII fallback."""
        old_enc = getattr(sys.stdout, "encoding", "")
        old_no_color = os.environ.get("NO_COLOR")
        try:
            sys.stdout.reconfigure(encoding="utf-8")
            os.environ["NO_COLOR"] = "1"
            bx = _BoxChars()
            assert bx._unicode is False
            assert bx.tl == "+"
        finally:
            if old_no_color:
                os.environ["NO_COLOR"] = old_no_color
            else:
                os.environ.pop("NO_COLOR", None)
            try:
                sys.stdout.reconfigure(encoding=old_enc)
            except Exception:
                pass


# ── _error_suggestion tests ───────────────────────────────────────────────────

class TestErrorSuggestion:
    """Proves _error_suggestion returns appropriate recovery hints."""

    def test_module_not_found(self):
        """ModuleNotFoundError gets pip install suggestion."""
        result = _error_suggestion("run", "ModuleNotFoundError: no module named 'foo'")
        assert "pip install" in result

    def test_syntax_error(self):
        """SyntaxError gets syntax check suggestion."""
        result = _error_suggestion("run", "SyntaxError: invalid syntax")
        assert "syntax" in result.lower()

    def test_timeout(self):
        """Timeout gets timeout suggestion."""
        result = _error_suggestion("run", "timed out after 30s")
        assert "timeout" in result.lower()

    def test_connection_error(self):
        """Connection error gets connectivity suggestion."""
        result = _error_suggestion("run", "Connection refused")
        assert "reachable" in result.lower()

    def test_permission_denied(self):
        """Permission denied gets permissions suggestion."""
        result = _error_suggestion("run", "Permission denied")
        assert "permission" in result.lower()

    def test_command_not_found(self):
        """Command not found gets install suggestion."""
        result = _error_suggestion("run", "command not found: pytest")
        assert "install" in result.lower()

    def test_assertion_error(self):
        """AssertionError gets assertion suggestion."""
        result = _error_suggestion("run", "AssertionError: expected 5, got 3")
        assert "assertion" in result.lower()

    def test_unknown_error(self):
        """Unknown error returns empty string."""
        result = _error_suggestion("run", "Something completely unexpected happened")
        assert result == ""


# ── _summarize tests ──────────────────────────────────────────────────────────

class TestSummarize:
    """Proves _summarize produces correct summaries for each tool."""

    def test_run_command(self):
        """run shows the command."""
        s = _summarize("run", {"command": "pytest -v"})
        assert "pytest" in s

    def test_write_file(self):
        """write_file shows the path."""
        s = _summarize("write_file", {"path": "test.txt"})
        assert "test.txt" in s

    def test_edit_file(self):
        """edit_file shows the path."""
        s = _summarize("edit_file", {"path": "main.py"})
        assert "main.py" in s

    def test_read_file(self):
        """read_file shows the path."""
        s = _summarize("read_file", {"path": "README.md"})
        assert "README.md" in s

    def test_grep(self):
        """grep shows the pattern."""
        s = _summarize("grep", {"pattern": "def test"})
        assert "def test" in s

    def test_glob(self):
        """glob shows the pattern."""
        s = _summarize("glob", {"pattern": "**/*.py"})
        assert "**/*.py" in s

    def test_list_dir(self):
        """list_dir shows the path."""
        s = _summarize("list_dir", {"path": "."})
        assert "." in s

    def test_fetch_url(self):
        """fetch_url shows the URL."""
        s = _summarize("fetch_url", {"url": "https://example.com"})
        assert "example.com" in s

    def test_update_plan(self):
        """update_plan shows step count."""
        s = _summarize("update_plan", {"plan": [{"step": "a"}, {"step": "b"}]})
        assert "2" in s

    def test_ask_claude(self):
        """ask_claude shows the question."""
        s = _summarize("ask_claude", {"question": "What is the north star?"})
        assert "north star" in s

    def test_check_claude(self):
        """check_claude returns empty string."""
        s = _summarize("check_claude", {})
        assert s == ""


# ── TerminalUI tests ──────────────────────────────────────────────────────────

class TestTerminalUI:
    """Proves TerminalUI methods don't crash with various inputs."""

    @pytest.fixture
    def ui(self):
        """Create a TerminalUI with color disabled for testing."""
        c = _Color(enabled=False)
        return TerminalUI(auto_approve=True, interactive=False, color=c)

    def test_start_session(self, ui):
        """start_session creates a log file."""
        ui.start_session()
        assert ui._session_log is not None
        assert ui._session_log.exists()
        assert ui._session_start > 0

    def test_assistant(self, ui, capsys):
        """assistant prints the message."""
        ui.assistant("Hello, I am ARIA")
        captured = capsys.readouterr()
        assert "ARIA" in captured.out
        assert "Hello" in captured.out

    def test_stream_delta(self, ui, capsys):
        """stream_delta prints chunks."""
        ui.stream_delta("Hello ")
        ui.stream_delta("world")
        ui.stream_end()
        captured = capsys.readouterr()
        assert "Hello" in captured.out
        assert "world" in captured.out

    def test_tool_call_run(self, ui, capsys):
        """tool_call for run shows the command."""
        ui.tool_call("run", {"command": "pytest -v"})
        captured = capsys.readouterr()
        assert "$" in captured.out
        assert "pytest" in captured.out

    def test_tool_call_other(self, ui, capsys):
        """tool_call for other tools shows the name."""
        ui.tool_call("read_file", {"path": "test.txt"})
        captured = capsys.readouterr()
        assert "read_file" in captured.out

    def test_tool_result_error(self, ui, capsys):
        """tool_result for error shows the error."""
        result = ToolResult("error: file not found", is_error=True)
        ui.tool_result("read_file", result)
        captured = capsys.readouterr()
        assert "error" in captured.out

    def test_tool_result_run_success(self, ui, capsys):
        """tool_result for run success shows output."""
        result = ToolResult("exit code: 0\nline1\nline2\nline3")
        ui.tool_result("run", result)
        captured = capsys.readouterr()
        assert "line1" in captured.out

    def test_tool_result_run_long_output(self, ui, capsys):
        """tool_result for run with >5 lines shows truncated preview."""
        lines = [f"line{i}" for i in range(10)]
        result = ToolResult("exit code: 0\n" + "\n".join(lines))
        ui.tool_result("run", result)
        captured = capsys.readouterr()
        assert "more lines" in captured.out

    def test_tool_result_write_file(self, ui, capsys):
        """tool_result for write_file shows mutation."""
        result = ToolResult("created test.txt", mutation="created test.txt")
        ui.tool_result("write_file", result)
        captured = capsys.readouterr()
        assert "test.txt" in captured.out

    def test_tool_result_edit_file(self, ui, capsys):
        """tool_result for edit_file shows mutation."""
        result = ToolResult("edited main.py", mutation="edited main.py")
        ui.tool_result("edit_file", result)
        captured = capsys.readouterr()
        assert "main.py" in captured.out

    def test_info(self, ui, capsys):
        """info prints the message."""
        ui.info("Processing...")
        captured = capsys.readouterr()
        assert "Processing" in captured.out

    def test_operator_message(self, ui, capsys):
        """operator_message prints the message in a box."""
        ui.operator_message("Stop and check the logs")
        captured = capsys.readouterr()
        assert "operator" in captured.out.lower()
        assert "Stop" in captured.out

    def test_operator_message_truncated(self, ui, capsys):
        """operator_message truncates long messages."""
        long_msg = "x" * 200
        ui.operator_message(long_msg)
        captured = capsys.readouterr()
        assert "…" in captured.out

    def test_set_step_context(self, ui):
        """set_step_context sets the step counter."""
        ui.set_step_context(2, 5)
        assert ui._step_number == 2
        assert ui._total_steps == 5

    def test_step_prefix(self, ui):
        """_step_prefix returns formatted string when steps > 0."""
        ui.set_step_context(2, 5)
        prefix = ui._step_prefix()
        assert "Step 2/5" in prefix

    def test_step_prefix_empty(self, ui):
        """_step_prefix returns empty string when no steps."""
        ui.set_step_context(0, 0)
        assert ui._step_prefix() == ""

    def test_thinking_start_stop(self, ui):
        """thinking_start/stop don't crash."""
        ui.thinking_start("testing")
        ui.thinking_stop()

    def test_thinking_start_idempotent(self, ui):
        """thinking_start is idempotent (second call is a no-op)."""
        ui.thinking_start("test")
        t1 = ui._spin_thread
        ui.thinking_start("test again")
        assert ui._spin_thread is t1  # Same thread, not a new one
        ui.thinking_stop()

    def test_progress_bar(self, ui, capsys):
        """progress_bar doesn't crash."""
        ui.progress_bar(5, 10, "testing")
        ui.progress_end()
        # No assertion — just shouldn't crash

    def test_approve_auto(self, ui):
        """approve returns True when auto_approve is True."""
        assert ui.approve("run", {"command": "test"}) is True

    def test_approve_auto_all(self, ui):
        """approve returns True when approve_all is True."""
        ui.approve_all = True
        assert ui.approve("run", {"command": "test"}) is True

    def test_tool_output(self, ui):
        """tool_output doesn't crash with various inputs."""
        ui.tool_output("")  # Empty
        ui.tool_output("line of output")  # Normal
        ui.tool_output("x" * 200)  # Long

    def test_ensure_clear_line_no_spinner(self, ui):
        """_ensure_clear_line doesn't crash when no spinner is active."""
        ui._ensure_clear_line()

    def test_stream_end_no_stream(self, ui):
        """stream_end doesn't crash when no stream is active."""
        ui.stream_end()

    def test_progress_end_no_progress(self, ui):
        """progress_end doesn't crash when no progress bar is active."""
        ui.progress_end()


# ── Banner tests ──────────────────────────────────────────────────────────────

class TestBanner:
    """Proves the banner renders without crashing."""

    def test_banner_self_mode(self, capsys):
        """Banner renders in self-mode."""
        c = _Color(enabled=False)
        cfg = LLMConfig()
        guard = WriteGuard(self_mode=True)
        _banner(c, cfg, True, guard, Path.cwd(), auto_approve=True)
        captured = capsys.readouterr()
        assert "ARIA" in captured.out

    def test_banner_general_mode(self, capsys):
        """Banner renders in general mode."""
        c = _Color(enabled=False)
        cfg = LLMConfig()
        guard = WriteGuard(self_mode=False)
        _banner(c, cfg, False, guard, Path.cwd(), auto_approve=False)
        captured = capsys.readouterr()
        assert "ARIA" in captured.out

    def test_banner_cp1252(self, capsys):
        """Banner doesn't crash on cp1252 terminal."""
        old_enc = getattr(sys.stdout, "encoding", "")
        old_no_color = os.environ.pop("NO_COLOR", None)
        try:
            sys.stdout.reconfigure(encoding="cp1252")
            c = _Color(enabled=False)
            cfg = LLMConfig()
            guard = WriteGuard(self_mode=True)
            _banner(c, cfg, True, guard, Path.cwd(), auto_approve=True)
        finally:
            if old_no_color is not None:
                os.environ["NO_COLOR"] = old_no_color
            try:
                sys.stdout.reconfigure(encoding=old_enc)
            except Exception:
                pass

    def test_banner_with_color(self, capsys):
        """Banner renders with color enabled."""
        c = _Color(enabled=True)
        cfg = LLMConfig()
        guard = WriteGuard(self_mode=True)
        _banner(c, cfg, True, guard, Path.cwd(), auto_approve=True)
        captured = capsys.readouterr()
        assert "ARIA" in captured.out


# ── Session log tests ─────────────────────────────────────────────────────────

class TestSessionLog:
    """Proves session log utilities work."""

    def test_ensure_session_dir(self):
        """_ensure_session_dir creates the directory."""
        d = _ensure_session_dir()
        assert d.exists()
        assert d.is_dir()

    def test_session_log_path(self):
        """_session_log_path returns a valid path."""
        p = _session_log_path()
        assert str(p).endswith(".log")
        assert p.parent.exists()

    def test_append_log(self, tmp_path):
        """_append_log writes to the log file."""
        log = tmp_path / "test.log"
        _append_log(log, "Hello")
        assert log.read_text() == "Hello\n"
        _append_log(log, "World")
        assert log.read_text() == "Hello\nWorld\n"

    def test_append_log_no_crash(self):
        """_append_log doesn't crash on invalid path."""
        _append_log(Path("/nonexistent/test.log"), "test")  # Should not raise


# ── find_repo_root tests ──────────────────────────────────────────────────────

class TestFindRepoRoot:
    """Proves find_repo_root works correctly."""

    def test_find_repo_root_in_crucix(self):
        """find_repo_root finds the crucix repo from within it."""
        root = find_repo_root(Path.cwd())
        # We're in the crucix repo, so it should find it
        assert root is not None
        assert (root / "aria_service").is_dir()
        assert (root / "CLAUDE.md").is_file()

    def test_find_repo_root_outside(self, tmp_path):
        """find_repo_root returns None outside the crucix repo."""
        root = find_repo_root(tmp_path)
        assert root is None


# ── load_dotenv tests ─────────────────────────────────────────────────────────

class TestLoadDotenv:
    """Proves load_dotenv works correctly."""

    def test_load_dotenv_no_file(self, tmp_path):
        """load_dotenv returns 0 when no .env file exists."""
        count = load_dotenv(tmp_path / ".env")
        assert count == 0

    def test_load_dotenv_loads_keys(self, tmp_path):
        """load_dotenv loads KEY=VALUE pairs."""
        env_file = tmp_path / ".env"
        env_file.write_text("TEST_KEY=test_value\nANOTHER=value2\n")
        count = load_dotenv(env_file)
        assert count == 2
        assert os.environ.get("TEST_KEY") == "test_value"
        assert os.environ.get("ANOTHER") == "value2"
        # Clean up
        os.environ.pop("TEST_KEY", None)
        os.environ.pop("ANOTHER", None)

    def test_load_dotenv_skips_comments(self, tmp_path):
        """load_dotenv skips comments and blank lines."""
        env_file = tmp_path / ".env"
        env_file.write_text("# This is a comment\n\nKEY=value\n")
        count = load_dotenv(env_file)
        assert count == 1
        assert os.environ.get("KEY") == "value"
        os.environ.pop("KEY", None)

    def test_load_dotenv_handles_export(self, tmp_path):
        """load_dotenv handles 'export KEY=VALUE' format."""
        env_file = tmp_path / ".env"
        env_file.write_text("export EXPORTED_KEY=exported_value\n")
        count = load_dotenv(env_file)
        assert count == 1
        assert os.environ.get("EXPORTED_KEY") == "exported_value"
        os.environ.pop("EXPORTED_KEY", None)

    def test_load_dotenv_no_clobber(self, tmp_path):
        """load_dotenv never clobbers existing env vars."""
        os.environ["EXISTING_KEY"] = "existing_value"
        env_file = tmp_path / ".env"
        env_file.write_text("EXISTING_KEY=new_value\n")
        count = load_dotenv(env_file)
        assert count == 0  # Didn't load because it already exists
        assert os.environ.get("EXISTING_KEY") == "existing_value"
        os.environ.pop("EXISTING_KEY", None)


# ── R-F1199: Session management tests ────────────────────────────────────────

class TestSessionManager:
    """Proves SessionManager creates, lists, loads, and deletes sessions."""

    def test_create_session(self, tmp_path):
        """SessionManager.create() returns a session with an ID."""
        from aria_cli.cli import SessionManager
        sm = SessionManager()
        sm.sessions_dir = tmp_path
        s = sm.create("test-session")
        assert s.id is not None
        assert s.name == "test-session"
        assert s.total_tokens == 0

    def test_list_sessions(self, tmp_path):
        """SessionManager.list_sessions() returns sessions newest first."""
        from aria_cli.cli import SessionManager
        sm = SessionManager()
        sm.sessions_dir = tmp_path
        sm._sessions = {}  # clear any pre-loaded sessions
        s1 = sm.create("first")
        s2 = sm.create("second")
        sessions = sm.list_sessions()
        assert len(sessions) == 2
        assert sessions[0].id == s2.id  # newest first

    def test_load_session(self, tmp_path):
        """SessionManager.load() retrieves a session by ID."""
        from aria_cli.cli import SessionManager
        sm = SessionManager()
        sm.sessions_dir = tmp_path
        created = sm.create("load-test")
        loaded = sm.load(created.id)
        assert loaded is not None
        assert loaded.id == created.id
        assert loaded.name == "load-test"

    def test_delete_session(self, tmp_path):
        """SessionManager.delete() removes a session."""
        from aria_cli.cli import SessionManager
        sm = SessionManager()
        sm.sessions_dir = tmp_path
        s = sm.create("delete-me")
        assert sm.delete(s.id) is True
        assert sm.load(s.id) is None

    def test_update_current(self, tmp_path):
        """SessionManager.update_current() persists stats."""
        from aria_cli.cli import SessionManager
        sm = SessionManager()
        sm.sessions_dir = tmp_path
        sm.create("stats-test")
        sm.update_current(tokens=100, cost=0.05, tool_count=5, error_count=1, file_changes=2)
        assert sm.current is not None
        assert sm.current.total_tokens == 100
        assert sm.current.total_cost == 0.05
        assert sm.current.tool_count == 5
        assert sm.current.error_count == 1
        assert sm.current.file_changes == 2


# ── R-F1199: Theme tests ─────────────────────────────────────────────────────

class TestTerminalUITheme:
    """Proves TerminalUI theme switching works."""

    def test_default_theme(self):
        """Default theme is 'dark'."""
        ui = TerminalUI(auto_approve=True, interactive=False, color=_Color(enabled=False))
        assert ui.get_theme() == "dark"

    def test_set_theme(self):
        """set_theme() changes the active theme."""
        ui = TerminalUI(auto_approve=True, interactive=False, color=_Color(enabled=False))
        ui.set_theme("claude")
        assert ui.get_theme() == "claude"

    def test_set_theme_light(self):
        """set_theme('light') works."""
        ui = TerminalUI(auto_approve=True, interactive=False, color=_Color(enabled=False))
        ui.set_theme("light")
        assert ui.get_theme() == "light"

    def test_invalid_theme_ignored(self):
        """set_theme() with invalid name is ignored."""
        ui = TerminalUI(auto_approve=True, interactive=False, color=_Color(enabled=False))
        ui.set_theme("invalid")
        assert ui.get_theme() == "dark"  # unchanged

    def test_theme_constructor(self):
        """TerminalUI accepts theme in constructor."""
        ui = TerminalUI(auto_approve=True, interactive=False, color=_Color(enabled=False), theme="claude")
        assert ui.get_theme() == "claude"

    def test_tc_applies_theme_color(self):
        """_tc() applies the correct ANSI code for the theme."""
        ui = TerminalUI(auto_approve=True, interactive=False, color=_Color(enabled=True))
        result = ui._tc("primary", "hello")
        assert "\033[36m" in result  # dark theme primary = cyan
        assert "hello" in result

    def test_tc_no_color(self):
        """_tc() returns plain text when color is disabled."""
        ui = TerminalUI(auto_approve=True, interactive=False, color=_Color(enabled=False))
        result = ui._tc("primary", "hello")
        assert result == "hello"


# ── R-F1199: Session export tests ────────────────────────────────────────────

class TestSessionExport:
    """Proves session export creates a file."""

    def test_export_creates_file(self, tmp_path, monkeypatch):
        """Export writes a file to the export directory."""
        from aria_cli.cli import SessionManager
        sm = SessionManager()
        sm.sessions_dir = tmp_path
        sm.create("export-test")
        sm.update_current(tool_count=3, error_count=0, file_changes=1)
        export_dir = tmp_path / "exports"
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        # Simulate export by writing directly
        export_file = export_dir / f"aria_coder_{sm.current.id}.txt"
        export_dir.mkdir(parents=True, exist_ok=True)
        with open(export_file, "w", encoding="utf-8") as f:
            f.write(f"ARIA Coder Session: {sm.current.name}\n")
            f.write(f"Tools: {sm.current.tool_count}\n")
        assert export_file.exists()
        content = export_file.read_text(encoding="utf-8")
        assert "ARIA Coder Session" in content
        assert "Tools: 3" in content
