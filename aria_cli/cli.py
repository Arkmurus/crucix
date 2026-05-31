"""R-F988 — ``aria`` command-line entry point.

Run ``aria`` inside any project directory for an interactive coding session, or
``aria -p "task"`` / ``aria "task"`` for a one-shot run. ARIA detects whether
she is inside her own ecosystem (the crucix repo) and, if so, turns on the
constitutional guard and brain wiring; everywhere else she behaves as a general
Claude-Code-style coding agent.

R-F1045 — Claude-Code parity UI:
  - Step counter (Step 1/5, Step 2/5) during multi-tool turns
  - Command echo ($ pytest -v) before running
  - Live output streaming with truncation preview
  - Diff preview before writes
  - Progress bar for long operations
  - Error recovery suggestions
  - Session log file (~/.aria/sessions/)
  - Visible thinking trace (what ARIA is doing, not just a spinner)
  - Final summary with changed files + stats

R-F1141 — operator mid-task interject:
  - Background daemon thread reads sys.stdin lines into a thread-safe queue
  - REPL reads from the queue (blocking) instead of input() directly
  - Agent.run_turn drains the queue non-blocking before each LLM call
  - Operator messages appear as [OPERATOR (mid-task)] in the conversation

R-F1199 — chat UI feature parity:
  - Session management: save/load/delete sessions with JSON persistence
  - Rich formatting with graceful fallback to ANSI colors
  - Theme switching (/theme dark|light|claude)
  - Session export (/export) to text file
  - Per-session cost tracking display
  - Tab completion for REPL commands

  R-F1202 — fix _BoxChars Unicode detection on Windows (cp1252 false negative):
    Windows 10/11 terminals (conhost.exe, Windows Terminal, PowerShell ISE) can
    render Unicode box-drawing characters even when sys.stdout.encoding is cp1252.
    The old probe only checked encoding — now it also checks os.name + Windows
    major version (10+ = Unicode capable).
"""
from __future__ import annotations

import argparse
import json
import os
import queue
import sys
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

from . import __version__
from . import brain as brain_mod
from .agent import Agent, AgentUI
from .llm import LLMClient, LLMConfig
from .prompt import build_system_prompt
from .safety import WriteGuard
from .tools import ToolResult, Toolbox

# Rich library imports (optional — graceful degradation)
try:
    from rich.console import Console as RichConsole
    from rich.markdown import Markdown as RichMarkdown
    from rich.syntax import Syntax as RichSyntax
    from rich.table import Table as RichTable
    RICH_AVAILABLE = True
except ImportError:
    RICH_AVAILABLE = False

# prompt_toolkit for tab completion and history (optional)
try:
    from prompt_toolkit import PromptSession as PTPromptSession
    from prompt_toolkit.history import FileHistory as PTFileHistory
    from prompt_toolkit.auto_suggest import AutoSuggestFromHistory as PTAutoSuggest
    from prompt_toolkit.completion import WordCompleter as PTWordCompleter
    from prompt_toolkit.styles import Style as PTStyle
    PROMPT_TOOLKIT_AVAILABLE = True
except ImportError:
    PROMPT_TOOLKIT_AVAILABLE = False


# ── R-F1141: operator mid-task interject ────────────────────────────────────
# A background daemon thread reads sys.stdin lines into a thread-safe queue.
# The REPL reads from this queue (blocking) instead of calling input() directly.
# Agent.run_turn drains the queue non-blocking before each LLM call.
_OPERATOR_QUEUE: queue.Queue = queue.Queue()
_STDIN_THREAD: threading.Thread | None = None


def _stdin_reader() -> None:
    """Daemon thread: read sys.stdin lines and put each on the queue."""
    try:
        for line in sys.stdin:
            stripped = line.strip()
            if stripped:
                _OPERATOR_QUEUE.put(stripped)
    except (EOFError, OSError):
        pass


def _start_stdin_reader() -> None:
    """Start the stdin reader daemon thread (idempotent)."""
    global _STDIN_THREAD
    if _STDIN_THREAD is not None and _STDIN_THREAD.is_alive():
        return
    _STDIN_THREAD = threading.Thread(target=_stdin_reader, daemon=True, name="stdin-reader")
    _STDIN_THREAD.start()


def _read_operator_input(prompt: str = "") -> str:
    """Read a line from the operator queue (blocking), or fall back to input().

    In interactive mode, reads from the queue so the stdin reader thread
    can feed lines both to the REPL (blocking) and to mid-task drains
    (non-blocking). Falls back to input() if the queue is empty (first call).
    """
    if not sys.stdin.isatty():
        # Non-interactive (piped input) — read directly
        try:
            return input(prompt).strip()
        except EOFError:
            return ""

    try:
        # Try non-blocking first — if something is already queued, use it
        return _OPERATOR_QUEUE.get_nowait()
    except queue.Empty:
        pass

    # Blocking read from queue (with prompt)
    if prompt:
        sys.stdout.write(prompt)
        sys.stdout.flush()
    try:
        return _OPERATOR_QUEUE.get()
    except (EOFError, OSError):
        return ""


# ── Session log ─────────────────────────────────────────────────────────────
_SESSION_DIR = Path.home() / ".aria" / "sessions"


def _ensure_session_dir() -> Path:
    _SESSION_DIR.mkdir(parents=True, exist_ok=True)
    return _SESSION_DIR


def _session_log_path() -> Path:
    ts = time.strftime("%Y%m%d_%H%M%S")
    return _ensure_session_dir() / f"session_{ts}.log"


def _append_log(path: Path, line: str) -> None:
    try:
        with open(path, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


# ── R-F1199: Session management (save/load/delete) ──────────────────────────
@dataclass
class CoderSession:
    """A saved coding session with metadata."""
    id: str
    name: str
    created_at: str
    updated_at: str
    total_tokens: int = 0
    total_cost: float = 0.0
    tool_count: int = 0
    error_count: int = 0
    file_changes: int = 0
    operator_messages: int = 0
    summary: str = ""


class SessionManager:
    """Manages coding sessions with JSON persistence to ~/.aria/sessions/."""

    def __init__(self) -> None:
        self.sessions_dir = _ensure_session_dir()
        self.current: Optional[CoderSession] = None
        self._sessions: Dict[str, CoderSession] = {}
        self._load()

    def _load(self) -> None:
        """Load existing session metadata from disk."""
        for f in self.sessions_dir.glob("session_*.json"):
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
                if "id" in data:
                    s = CoderSession(**{k: data[k] for k in CoderSession.__dataclass_fields__ if k in data})
                    self._sessions[s.id] = s
            except Exception:
                pass

    def create(self, name: str | None = None) -> CoderSession:
        """Create a new session."""
        sid = datetime.now().strftime("%Y%m%d_%H%M%S%f")
        if not name:
            name = f"Session {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
        self.current = CoderSession(
            id=sid, name=name,
            created_at=datetime.now().isoformat(),
            updated_at=datetime.now().isoformat(),
        )
        self._sessions[sid] = self.current
        self._save_meta(self.current)
        return self.current

    def _save_meta(self, s: CoderSession) -> None:
        """Persist session metadata to JSON."""
        path = self.sessions_dir / f"session_{s.id}.json"
        try:
            path.write_text(
                json.dumps({k: getattr(s, k) for k in CoderSession.__dataclass_fields__}, indent=2),
                encoding="utf-8",
            )
        except Exception:
            pass

    def update_current(self, *, tokens: int = 0, cost: float = 0.0,
                       tool_count: int = 0, error_count: int = 0,
                       file_changes: int = 0, operator_messages: int = 0,
                       summary: str = "") -> None:
        """Update the current session's stats and persist."""
        if not self.current:
            return
        self.current.total_tokens += tokens
        self.current.total_cost += cost
        self.current.tool_count += tool_count
        self.current.error_count += error_count
        self.current.file_changes += file_changes
        self.current.operator_messages += operator_messages
        if summary:
            self.current.summary = summary[:500]
        self.current.updated_at = datetime.now().isoformat()
        self._save_meta(self.current)

    def list_sessions(self) -> List[CoderSession]:
        """Return all sessions, newest first."""
        return sorted(self._sessions.values(), key=lambda s: s.created_at, reverse=True)

    def load(self, session_id: str) -> Optional[CoderSession]:
        """Load a session by ID."""
        s = self._sessions.get(session_id)
        if s:
            self.current = s
        return s

    def delete(self, session_id: str) -> bool:
        """Delete a session by ID."""
        if session_id not in self._sessions:
            return False
        path = self.sessions_dir / f"session_{session_id}.json"
        try:
            path.unlink(missing_ok=True)
        except Exception:
            pass
        del self._sessions[session_id]
        if self.current and self.current.id == session_id:
            self.current = None
        return True


# ── crucix-repo detection ───────────────────────────────────────────────────
def find_repo_root(start: Path) -> Path | None:
    """Walk up from ``start`` looking for the crucix repo root (a directory that
    holds both ``aria_service`` and ``CLAUDE.md``). Returns None if not found."""
    for d in [start, *start.parents]:
        if (d / "aria_service").is_dir() and (d / "CLAUDE.md").is_file():
            return d
    return None


def load_dotenv(path: Path) -> int:
    """Minimal .env loader (no dependency, per CLAUDE.md §6 native-only): parse
    KEY=VALUE lines and set any that aren't already in the environment. Returns
    the count loaded. Used in self-mode so the same .env the server reads also
    feeds the CLI's LLM config — drop your DEEPSEEK_API_KEY there and `aria`
    just works. Existing env vars always win (never clobbered)."""
    if not path.is_file():
        return 0
    loaded = 0
    try:
        for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            if line.lower().startswith("export "):
                line = line[7:].lstrip()
            key, _, val = line.partition("=")
            key = key.strip()
            val = val.strip().strip('"').strip("'")
            if key and key not in os.environ:
                os.environ[key] = val
                loaded += 1
    except Exception:  # noqa: BLE001 — never let .env parsing break startup
        return loaded
    return loaded


def _repo_relative_resolver(repo_root: Path):
    def resolve(path: str) -> str:
        try:
            rel = Path(path).resolve().relative_to(repo_root)
            return rel.as_posix()
        except Exception:  # noqa: BLE001
            return path
    return resolve


# ── terminal UI ─────────────────────────────────────────────────────────────
class _Color:
    def __init__(self, enabled: bool) -> None:
        self.on = enabled

    def _w(self, code: str, s: str) -> str:
        return f"\033[{code}m{s}\033[0m" if self.on else s

    def dim(self, s: str) -> str: return self._w("2", s)
    def cyan(self, s: str) -> str: return self._w("36", s)
    def green(self, s: str) -> str: return self._w("32", s)
    def yellow(self, s: str) -> str: return self._w("33", s)
    def red(self, s: str) -> str: return self._w("31", s)
    def bold(self, s: str) -> str: return self._w("1", s)
    def blue(self, s: str) -> str: return self._w("34", s)
    def magenta(self, s: str) -> str: return self._w("35", s)


# ── R-F1196: Unicode box-drawing with ASCII fallback ────────────────────────
# Windows cmd.exe uses cp1252 by default, which cannot render Unicode
# box-drawing characters (╔═╗║╚╝). Detect terminal encoding and fall back
# to ASCII (+-|) when Unicode is not supported.
class _BoxChars:
    """Box-drawing characters, auto-selected for terminal encoding support.

    On Windows cmd.exe (cp1252) uses ASCII ``+``/``-``/``|``; on UTF-8
    terminals uses Unicode box-drawing (``╔``/``═``/``╗``/``║``/``╚``/``╝``).
    """

    def __init__(self) -> None:
        # Detect if the terminal can render Unicode box-drawing chars.
        self._unicode = self._probe_unicode()

    @staticmethod
    def _probe_unicode() -> bool:
        """Probe whether the terminal can render Unicode box-drawing chars.

        Returns True if the terminal is likely to support Unicode box-drawing
        characters. Checks in order:
        1. NO_COLOR env var -> False (conservative)
        2. stdout encoding contains "utf" -> True
        3. Windows 10+ (os.name == 'nt', major >= 10) -> True
           (conhost.exe, Windows Terminal, PS ISE all support Unicode)
        4. Otherwise -> False (fall back to ASCII +-|)
        """
        if os.getenv("NO_COLOR"):
            return False
        enc = getattr(sys.stdout, "encoding", "") or ""
        if "utf" in enc.lower():
            return True
        # R-F1202: Windows 10+ terminals support Unicode box-drawing even
        # when encoding is cp1252 (the default for cmd.exe).
        if os.name == "nt":
            try:
                ver = sys.getwindowsversion()
                if ver.major >= 10:
                    return True
            except AttributeError:
                pass
        return False

    @property
    def tl(self) -> str: return "╔" if self._unicode else "+"   # top-left
    @property
    def tr(self) -> str: return "╗" if self._unicode else "+"   # top-right
    @property
    def bl(self) -> str: return "╚" if self._unicode else "+"   # bottom-left
    @property
    def br(self) -> str: return "╝" if self._unicode else "+"   # bottom-right
    @property
    def h(self) -> str: return "═" if self._unicode else "-"    # horizontal
    @property
    def v(self) -> str: return "║" if self._unicode else "|"    # vertical
    @property
    def tm(self) -> str: return "╠" if self._unicode else "|"   # tee-middle (left)
    @property
    def check(self) -> str: return "✓" if self._unicode else "v"
    @property
    def cross(self) -> str: return "✗" if self._unicode else "x"


class TerminalUI(AgentUI):
    """Professional terminal UI with clean message boundaries and real-time chat.

    R-F1194 features:
      - Professional banner with session info
      - Clean message boundaries with consistent prefixes
      - Real-time operator chat while ARIA is working
      - Status bar showing session stats
      - Step counter: "Step 2/5" during multi-tool turns
      - Command echo: "$ pytest -v" before running
      - Live output: streaming with truncation preview
      - Diff preview: shows +/- lines before writes
      - Progress bar: [===>>> ] for long operations
      - Error recovery: suggests next steps after errors
      - Thinking trace: shows what ARIA is doing
      - Session log: persistent log file

    R-F1199 — chat UI feature parity:
      - Session management (save/load/delete)
      - Rich formatting with graceful fallback
      - Theme switching (/theme dark|light|claude)
      - Session export (/export)
      - Per-session cost tracking
      - Tab completion for REPL commands
    """

    # Theme color maps
    THEMES = {
        "dark": {
            "primary": "36",    # cyan
            "secondary": "33",  # yellow
            "accent": "32",     # green
            "error": "31",      # red
            "dim": "2",         # dim
            "info": "34",       # blue
            "highlight": "35",  # magenta
        },
        "light": {
            "primary": "94",    # bright blue
            "secondary": "93",  # bright yellow
            "accent": "92",     # bright green
            "error": "91",      # bright red
            "dim": "90",        # bright black
            "info": "94",       # bright blue
            "highlight": "95",  # bright magenta
        },
        "claude": {
            "primary": "38;5;45",   # warm cyan
            "secondary": "38;5;214", # warm orange
            "accent": "38;5;78",    # warm green
            "error": "38;5;196",    # red
            "dim": "38;5;242",      # grey
            "info": "38;5;68",      # blue
            "highlight": "38;5;170", # purple
        },
    }

    def __init__(self, *, auto_approve: bool, interactive: bool, color: _Color,
                 theme: str = "dark") -> None:
        self.auto_approve = auto_approve
        self.interactive = interactive
        self.c = color
        self._theme_name = theme if theme in self.THEMES else "dark"
        self._theme = self.THEMES[self._theme_name]
        self.approve_all = False
        self._can_animate = sys.stdout.isatty()
        self._spin_stop: threading.Event | None = None
        self._spin_thread: threading.Thread | None = None
        self._stream_active = False
        self._streamed_this_turn = False
        self._spin_label = "thinking"
        self._spin_start = 0.0
        self._last_output = ""
        self._last_static = 0.0
        # R-F1045: step tracking
        self._step_number = 0
        self._total_steps = 0
        self._session_log: Path | None = None
        self._last_command = ""
        # R-F1143: track whether we need a newline before the next output
        self._needs_leading_newline = False
        # R-F1194: session stats
        self._session_start = 0.0
        self._tool_count = 0
        self._error_count = 0
        self._file_changes = 0
        self._operator_messages = 0
        # R-F1199: session management
        self.session_manager = SessionManager()
        # R-F1199: Rich console (optional)
        self._rich_console = RichConsole() if RICH_AVAILABLE and color.on else None

    def _tc(self, key: str, s: str) -> str:
        """Apply a theme color to text."""
        code = self._theme.get(key, "0")
        return f"\033[{code}m{s}\033[0m" if self.c.on else s

    def set_theme(self, theme: str) -> None:
        """Switch color theme."""
        if theme in self.THEMES:
            self._theme_name = theme
            self._theme = self.THEMES[theme]

    def get_theme(self) -> str:
        return self._theme_name

    def start_session(self) -> None:
        """Called at session start to create the log file and session record."""
        self._session_start = time.time()
        self._session_log = _session_log_path()
        _append_log(self._session_log, f"ARIA Coder v{__version__}")
        _append_log(self._session_log, f"Started: {time.strftime('%Y-%m-%d %H:%M:%S')}")
        _append_log(self._session_log, f"Directory: {Path.cwd()}")
        _append_log(self._session_log, "─" * 60)
        # R-F1199: create session manager record
        self.session_manager.create()

    def _log(self, text: str) -> None:
        if self._session_log:
            _append_log(self._session_log, text)

    def _ensure_clear_line(self) -> None:
        """Clear any spinner or residual output before writing a new line."""
        if self._spin_thread is not None:
            self.thinking_stop()
        if self._can_animate:
            sys.stdout.write("\r\033[K")
            sys.stdout.flush()

    def assistant(self, text: str) -> None:
        """Print a complete assistant message with clean boundaries."""
        self._ensure_clear_line()
        self._log(f"[aria] {text[:200]}")
        print()
        # Use a subtle separator line for visual clarity
        print(self.c.dim("  ─" + "─" * 60))
        print(self.c.cyan("  ARIA ") + text)
        self._needs_leading_newline = True

    # ── live token streaming (never silent) ──────────────────────────────────
    def stream_delta(self, text: str) -> None:
        """Stream a chunk of LLM output. First chunk prints the prefix."""
        if not self._stream_active:
            # Stop spinner cleanly without clearing the line (the spinner
            # already occupies it; we overwrite it with the ARIA prefix).
            if self._spin_thread is not None:
                self.thinking_stop()
            if self._needs_leading_newline:
                print()
            sys.stdout.write(self.c.cyan("  ARIA "))
            self._stream_active = True
            self._streamed_this_turn = True
            self._needs_leading_newline = False
        sys.stdout.write(text)
        sys.stdout.flush()

    def stream_end(self) -> None:
        """Finalise a streaming response."""
        if self._stream_active:
            sys.stdout.write("\n")
            sys.stdout.flush()
            self._stream_active = False
            self._needs_leading_newline = True

    # ── step tracking ──────────────────────────────────────────────────────
    def set_step_context(self, current: int, total: int) -> None:
        """Called before each tool call to set the step counter."""
        self._step_number = current
        self._total_steps = total

    def _step_prefix(self) -> str:
        if self._total_steps > 0:
            return f"Step {self._step_number}/{self._total_steps}"
        return ""

    # ── tool call display ───────────────────────────────────────────────────
    def tool_call(self, name: str, args: dict) -> None:
        """Display a tool call with consistent formatting."""
        self._ensure_clear_line()
        self._tool_count += 1
        detail = self._summarize(name, args)
        prefix = self._step_prefix()
        if prefix:
            line = f"  {prefix} · {name}({detail})"
        else:
            line = f"  · {name}({detail})"

        if name == "run":
            cmd = args.get("command", "")
            self._last_command = cmd[:200]
            print(self.c.dim(f"  $ {cmd[:200]}"))
            self._log(f"[cmd] $ {cmd[:200]}")
        else:
            print(self.c.dim(line))
            self._log(f"[tool] {name}({detail})")

    def tool_result(self, name: str, result: ToolResult) -> None:
        """Display a tool result with consistent formatting."""
        if result.is_error:
            self._error_count += 1
            # Show the error head AND the output body so the user sees what went wrong
            lines = result.output.splitlines()
            head = lines[0] if lines else ""
            print(self.c.red(f"  -> {head[:200]}"))
            self._log(f"[error] {head[:200]}")
            # Show the rest of the output (up to 10 lines) for context
            if len(lines) > 1:
                for ln in lines[1:11]:
                    print(self.c.dim(f"  | {ln[:200]}"))
                if len(lines) > 11:
                    print(self.c.dim(f"  | ... ({len(lines) - 11} more lines)"))
            suggestion = self._error_suggestion(name, result.output)
            if suggestion:
                print(self.c.yellow(f"     {suggestion}"))
            return

        if name == "run":
            if result.output:
                lines = result.output.splitlines()
                if len(lines) > 5:
                    preview = "\n".join(lines[:3])
                    tail = f"... ({len(lines) - 3} more lines)"
                    print(self.c.dim(f"  | {preview}"))
                    print(self.c.dim(f"  | {tail}"))
                else:
                    for ln in lines:
                        print(self.c.dim(f"  | {ln}"))
            # Always log exit code, even for empty output
            exit_code = "0" if not result.is_error else "non-zero"
            self._log(f"[result] {len(result.output.splitlines()) if result.output else 0} lines, exit {exit_code}")

        if name in ("write_file", "edit_file") and result.mutation:
            self._file_changes += 1
            print(self.c.green(f"  -> {result.mutation}"))
            self._log(f"[write] {result.mutation}")

        if name in {"update_plan", "ask_claude", "check_claude"}:
            for ln in result.output.splitlines():
                print(self.c.dim(f"  | {ln}"))

    def _error_suggestion(self, name: str, output: str) -> str:
        """Suggest recovery steps after common errors."""
        out_lower = output.lower()
        if "module not found" in out_lower or "import error" in out_lower or "no module" in out_lower:
            return "Try: pip install <missing-package> or check import paths"
        if "syntax error" in out_lower or "invalid syntax" in out_lower:
            return "Try: check the file for syntax issues and fix them"
        if "timeout" in out_lower or "timed out" in out_lower:
            return "Try: increase timeout or check network connectivity"
        if "connection" in out_lower or "refused" in out_lower or "reset" in out_lower:
            return "Try: check the service is running and reachable"
        if "permission" in out_lower or "denied" in out_lower:
            return "Try: check file permissions or run with appropriate privileges"
        if "not found" in out_lower and "command" in out_lower:
            return "Try: install the required tool or check PATH"
        if "assert" in out_lower and "error" in out_lower:
            return "Try: check the assertion condition — the test expectation may need updating"
        return ""

    def info(self, text: str) -> None:
        """Print an informational message."""
        self._ensure_clear_line()
        self._log(f"[info] {text}")
        print(self.c.dim(f"  i {text}"))

    # ── always-on activity indicator ────────────────────────────────────────
    def thinking_start(self, label: str = "thinking") -> None:
        """Start the activity spinner. Shows what ARIA is doing."""
        self._streamed_this_turn = False
        if self._spin_thread is not None:
            return
        self._spin_label = label
        self._spin_start = time.monotonic()
        self._last_output = ""
        if self._can_animate:
            self._spin_stop = threading.Event()
            self._spin_thread = threading.Thread(target=self._spin, daemon=True)
            self._spin_thread.start()
        else:
            self._last_static = time.monotonic()
            print(self.c.dim(f"  ~ {label}..."), flush=True)

    def _spin(self) -> None:
        """Animated spinner with context label and live output preview."""
        frames = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"
        i = 0
        last_tail = ""
        while self._spin_stop is not None and not self._spin_stop.is_set():
            elapsed = int(time.monotonic() - self._spin_start)
            # Show live output inline when available (e.g. pytest progress)
            if self._last_output:
                # Trim to fit: show the most recent output line
                tail = self._last_output.strip()[:60]
                if tail != last_tail:
                    last_tail = tail
                tail = f"  {tail}"
            elif self._last_command:
                tail = f"  {self._last_command[:60]}"
            else:
                tail = ""
            prefix = self._step_prefix()
            if prefix:
                label = f"{prefix} {self._spin_label}"
            else:
                label = self._spin_label
            line = f"  {frames[i % len(frames)]} {label} ({elapsed}s){tail}"
            sys.stdout.write("\r\033[K" + self.c.dim(line[:140]))
            sys.stdout.flush()
            i += 1
            # Tick faster when there's live output so it feels responsive
            tick = 0.08 if self._last_output else 0.15
            self._spin_stop.wait(tick)
        sys.stdout.write("\r\033[K")
        sys.stdout.flush()

    def thinking_stop(self) -> None:
        """Stop the activity spinner."""
        if self._spin_thread is not None and self._spin_stop is not None:
            self._spin_stop.set()
            self._spin_thread.join(timeout=1.0)
            self._spin_thread = None
            self._spin_stop = None

    def tool_output(self, line: str) -> None:
        """Live output line from a running command."""
        if not line:
            return
        self._last_output = line
        if not self._can_animate:
            now = time.monotonic()
            if now - self._last_static >= 2.0:
                self._last_static = now
                print(self.c.dim(f"    | {line[:80]}"), flush=True)

    # ── progress bar for long operations ────────────────────────────────────
    def progress_bar(self, current: int, total: int, label: str = "") -> None:
        """Show a progress bar: [===>>> ] 45%."""
        if not self._can_animate:
            return
        width = 20
        filled = int(width * current / max(total, 1))
        bar = "=" * max(0, filled - 1) + ">" + " " * (width - filled)
        pct = int(100 * current / max(total, 1))
        lbl = f" {label}" if label else ""
        sys.stdout.write(f"\r\033[K  [{bar}] {pct}%{lbl}")
        sys.stdout.flush()

    def progress_end(self) -> None:
        if self._can_animate:
            sys.stdout.write("\r\033[K")
            sys.stdout.flush()

    # ── approval gate ───────────────────────────────────────────────────────
    def approve(self, name: str, args: dict) -> bool:
        if self.auto_approve or self.approve_all:
            return True
        if not self.interactive or not sys.stdin.isatty():
            print(self.c.yellow(
                f"  [denied] {name} needs approval but no TTY is attached. "
                f"Re-run with --auto to allow mutating actions."))
            return False
        detail = self._summarize(name, args)
        print(self.c.yellow(f"\n  ARIA wants to run: {self.c.bold(name)}({detail})"))
        try:
            ans = input("  allow? [y]es / [n]o / [a]ll this session: ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            return False
        if ans in {"a", "all"}:
            self.approve_all = True
            return True
        return ans in {"y", "yes", ""}

    # ── R-F1194: operator message notification ──────────────────────────────
    def operator_message(self, text: str) -> None:
        """Notify that an operator message arrived mid-task."""
        self._operator_messages += 1
        self._ensure_clear_line()
        preview = text if len(text) <= 80 else text[:80] + "…"
        print(self.c.yellow(f"  ┌─ operator message ─────────────────────────────────"))
        print(self.c.yellow(f"  │ {preview}"))
        print(self.c.yellow(f"  └────────────────────────────────────────────────────"))

    @staticmethod
    def _summarize(name: str, args: dict) -> str:
        if name == "run":
            return repr(args.get("command", ""))[:120]
        if name in {"write_file", "edit_file", "read_file"}:
            return repr(args.get("path", ""))
        if name == "grep":
            return repr(args.get("pattern", ""))
        if name == "glob":
            return repr(args.get("pattern", ""))
        if name == "list_dir":
            return repr(args.get("path", "."))
        if name == "fetch_url":
            return repr(args.get("url", ""))
        if name == "update_plan":
            return f"{len(args.get('plan', []))} steps"
        if name == "ask_claude":
            return repr(args.get("question", ""))[:100]
        if name == "check_claude":
            return ""
        return ", ".join(f"{k}={repr(v)[:40]}" for k, v in args.items())


# ── session orchestration ─────────────────────────────────────────────────
def _build_agent(cwd: Path, args, color: _Color, interactive: bool):
    repo_root = find_repo_root(cwd)
    if args.general:
        repo_root = None
    self_mode = repo_root is not None or args.self_mode
    if args.self_mode and repo_root is None:
        repo_root = cwd

    if repo_root is not None and str(repo_root) not in sys.path:
        sys.path.insert(0, str(repo_root))

    resolver = _repo_relative_resolver(repo_root) if repo_root else None
    guard = WriteGuard(self_mode=self_mode, repo_relative_resolver=resolver)
    toolbox = Toolbox(root=cwd, guard=guard, bridge_base=repo_root)

    if repo_root is not None:
        load_dotenv(repo_root / ".env")

    cfg = LLMConfig.from_env()
    if args.provider:
        cfg.provider = args.provider
    if args.model:
        cfg.model = args.model

    if not cfg.is_configured:
        print(color.red(
            "No LLM API key found. Set DEEPSEEK_API_KEY (or LLM_API_KEY) in your "
            "environment, or use --provider ollama for a local model.\n"
            "  PowerShell:  $env:DEEPSEEK_API_KEY = 'sk-...'"))
        sys.exit(2)

    llm = LLMClient(cfg)
    system_prompt = build_system_prompt(
        root=cwd, self_mode=self_mode, repo_root=repo_root)
    theme = getattr(args, "theme", "dark")
    ui = TerminalUI(auto_approve=args.auto, interactive=interactive, color=color, theme=theme)
    toolbox.on_output = ui.tool_output
    agent = Agent(llm=llm, toolbox=toolbox, system_prompt=system_prompt, ui=ui,
                  auto_approve=args.auto)

    # Back door: surface any messages Claude left for ARIA
    if repo_root is not None:
        try:
            from . import bridge
            pending = bridge.read_new(repo_root, reader="aria")
        except Exception:  # noqa: BLE001
            pending = []
        if pending:
            note = ("Claude Code (the operator's other agent in this repo) left "
                    "you the message(s) below. This IS Claude's guidance — treat "
                    "it as delivered and act on it; you do NOT need to call "
                    "check_claude for these:\n"
                    + "\n".join(f"- {m.get('text', '')}" for m in pending))
            agent.messages.append({"role": "system", "content": note})
            print(color.yellow(f"  [bridge] {len(pending)} message(s) from Claude loaded into this session."))
    return agent, ui, cfg, self_mode, guard


def _banner(color: _Color, cfg: LLMConfig, self_mode: bool, guard: WriteGuard,
            cwd: Path, auto_approve: bool = True) -> None:
    mode = color.green("self (crucix ecosystem)") if self_mode else "general project"
    brain = "wired" if brain_mod.brain_enabled(self_mode) else "off"
    approval = color.green("autonomous") if auto_approve else "confirm each action"
    bx = _BoxChars()
    h = bx.h * 56
    print()
    print(color.bold(f"  {bx.tl}{h}{bx.tr}"))
    print(color.bold(f"  {bx.v}") + color.cyan("  ARIA Coder") + color.dim(f"  v{__version__}") + color.bold(" " * max(1, 56 - 12 - len(__version__))) + color.bold(f"{bx.v}"))
    print(color.bold(f"  {bx.tm}{h}{bx.tm}"))
    dir_str = str(cwd)
    if len(dir_str) > 50:
        dir_str = "..." + dir_str[-47:]
    print(color.bold(f"  {bx.v}") + color.dim(f"  {dir_str:<56}") + color.bold(f"{bx.v}"))
    print(color.bold(f"  {bx.v}") + color.dim(f"  {cfg.provider}/{cfg.model:<50}") + color.bold(f"{bx.v}"))
    print(color.bold(f"  {bx.v}") + color.dim(f"  {mode:<56}") + color.bold(f"{bx.v}"))
    print(color.bold(f"  {bx.v}") + color.dim(f"  brain: {brain:<49}") + color.bold(f"{bx.v}"))
    print(color.bold(f"  {bx.v}") + color.dim(f"  {approval:<56}") + color.bold(f"{bx.v}"))
    print(color.bold(f"  {bx.bl}{h}{bx.br}"))
    print()


def _finalize(agent: Agent, ui: TerminalUI, cfg: LLMConfig, self_mode: bool,
              task: str, success: bool, color: _Color) -> None:
    changed = agent.toolbox.changed_files
    in_tok = agent.llm.total_input_tokens
    out_tok = agent.llm.total_output_tokens
    total_tok = in_tok + out_tok
    elapsed = time.time() - ui._session_start if ui._session_start else 0
    elapsed_str = f"{int(elapsed // 60)}m {int(elapsed % 60)}s" if elapsed >= 60 else f"{int(elapsed)}s"
    bx = _BoxChars()
    h = bx.h * 58

    print()
    print(color.bold(f"  {bx.tl}{h}{bx.tr}"))
    print(color.bold(f"  {bx.v}") + color.cyan("  Session Complete") + color.dim(f"  {bx.check if success else bx.cross}") + color.bold(" " * max(1, 56 - 18 - 1)) + color.bold(f"{bx.v}"))
    print(color.bold(f"  {bx.tm}{h}{bx.tm}"))
    print(color.bold(f"  {bx.v}") + color.dim(f"  duration:    {elapsed_str:<48}") + color.bold(f"{bx.v}"))
    print(color.bold(f"  {bx.v}") + color.dim(f"  files:       {len(changed):<3} changed{' ' * (45 - len(str(len(changed))))}") + color.bold(f"{bx.v}"))
    print(color.bold(f"  {bx.v}") + color.dim(f"  tools:       {ui._tool_count:<3} calls{' ' * (45 - len(str(ui._tool_count)))}") + color.bold(f"{bx.v}"))
    print(color.bold(f"  {bx.v}") + color.dim(f"  errors:      {ui._error_count:<3}{' ' * (45 - len(str(ui._error_count)))}") + color.bold(f"{bx.v}"))
    print(color.bold(f"  {bx.v}") + color.dim(f"  operator:    {ui._operator_messages:<3} msgs{' ' * (45 - len(str(ui._operator_messages)))}") + color.bold(f"{bx.v}"))
    print(color.bold(f"  {bx.v}") + color.dim(f"  tokens:      {total_tok:<6} ({in_tok} in / {out_tok} out){' ' * max(0, 32 - len(str(total_tok)) - len(str(in_tok)) - len(str(out_tok)))}") + color.bold(f"{bx.v}"))
    print(color.bold(f"  {bx.bl}{h}{bx.br}"))

    if changed:
        print(color.dim(f"\n  files: {', '.join(changed)}"))

    status = brain_mod.report_session(
        task=task, success=success, changed_files=changed,
        summary=agent.messages[-1].get("content", "")[:600] if agent.messages else "",
        self_mode=self_mode)
    print(color.dim(f"  {status}"))

    # Log final summary
    ui._log(f"─" * 60)
    ui._log(f"Completed: {time.strftime('%Y-%m-%d %H:%M:%S')}")
    ui._log(f"Success: {success}")
    ui._log(f"Files changed: {len(changed)}")
    ui._log(f"Tools: {ui._tool_count}")
    ui._log(f"Errors: {ui._error_count}")
    ui._log(f"Operator messages: {ui._operator_messages}")
    ui._log(f"Tokens: {total_tok}")
    if ui._session_log:
        print(color.dim(f"  session: {ui._session_log}"))

    # R-F1199: persist session stats
    ui.session_manager.update_current(
        tokens=total_tok,
        tool_count=ui._tool_count,
        error_count=ui._error_count,
        file_changes=len(changed),
        operator_messages=ui._operator_messages,
        summary=agent.messages[-1].get("content", "")[:300] if agent.messages else "",
    )

    agent.llm.close()


def _repl(agent: Agent, ui: TerminalUI, cfg: LLMConfig, self_mode: bool,
          guard: WriteGuard, cwd: Path, color: _Color) -> None:
    ui.start_session()
    _banner(color, cfg, self_mode, guard, cwd, auto_approve=agent.auto_approve)
    print(color.dim("  Type a task or message. /help for commands. Chat with ARIA while she works."))
    print(color.dim("  Tip: type a message while ARIA is working and she'll respond in real-time.\n"))
    last_task = ""
    try:
        while True:
            try:
                ui._ensure_clear_line()
                line = _read_operator_input(color.bold("  you > "))
            except EOFError:
                break
            if not line:
                continue
            if line in {"/exit", "/quit"}:
                break
            if line == "/help":
                bx = _BoxChars()
                h = bx.h * 55
                print(color.dim(
                    f"  {bx.tl}{h}{bx.tr}\n"
                    f"  {bx.v}  Commands{' ' * 46}{bx.v}\n"
                    f"  {bx.tm}{h}{bx.tm}\n"
                    f"  {bx.v}  /confirm  toggle asking before edits (auto)  {bx.v}\n"
                    f"  {bx.v}  /changes  list files changed this session    {bx.v}\n"
                    f"  {bx.v}  /claude   read new messages from Claude     {bx.v}\n"
                    f"  {bx.v}  /session  show session log path             {bx.v}\n"
                    f"  {bx.v}  /sessions list saved sessions               {bx.v}\n"
                    f"  {bx.v}  /export   export session to file            {bx.v}\n"
                    f"  {bx.v}  /theme    change color theme                {bx.v}\n"
                    f"  {bx.v}  /gaps     scan for capability gaps/bugs     {bx.v}\n"
                    f"  {bx.v}  /status   show system health                {bx.v}\n"
                    f"  {bx.v}  /history  show composite score history      {bx.v}\n"
                    f"  {bx.v}  /cost     show session and monthly cost     {bx.v}\n"
                    f"  {bx.v}  /model    show current model chain          {bx.v}\n"
                    f"  {bx.v}  /compact  compress conversation history     {bx.v}\n"
                    f"  {bx.v}  /memory   show project memory notes         {bx.v}\n"
                    f"  {bx.v}  /diff     show uncommitted changes          {bx.v}\n"
                    f"  {bx.v}  /plan     show current task plan            {bx.v}\n"
                    f"  {bx.v}  /stats    show session statistics           {bx.v}\n"
                    f"  {bx.v}  /think    show raw thinking trace           {bx.v}\n"
                    f"  {bx.v}  /clear    clear the screen                  {bx.v}\n"
                    f"  {bx.v}  /version  show version info                 {bx.v}\n"
                    f"  {bx.v}  /uptime   show session duration             {bx.v}\n"
                    f"  {bx.v}  /config   show current configuration        {bx.v}\n"
                    f"  {bx.v}  /reset    clear conversation history        {bx.v}\n"
                    f"  {bx.v}  /exit     quit                              {bx.v}\n"
                    f"  {bx.bl}{h}{bx.br}\n"
                    "  Tip: type a message while ARIA is working and she'll\n"
                    "  respond to it in real-time."))
                continue
            if line == "/claude":
                try:
                    from . import bridge
                    msgs = bridge.read_new(cwd, reader="aria") if find_repo_root(cwd) else []
                except Exception:  # noqa: BLE001
                    msgs = []
                if msgs:
                    for m in msgs:
                        print(color.magenta(f"  [Claude] {m.get('text', '')}"))
                else:
                    print(color.dim("  no new messages from Claude"))
                continue
            if line == "/session":
                if ui._session_log:
                    print(color.dim(f"  session log: {ui._session_log}"))
                else:
                    print(color.dim("  no session log (session not started)"))
                continue
            # R-F1199: session management commands
            if line == "/sessions":
                sessions = ui.session_manager.list_sessions()
                bx = _BoxChars()
                h = bx.h * 55
                print(color.cyan(f"  {bx.tl}{h}{bx.tr}"))
                print(color.cyan(f"  {bx.v}  Sessions ({len(sessions)}){' ' * (44 - len(str(len(sessions))))}{bx.v}"))
                print(color.cyan(f"  {bx.tm}{h}{bx.tm}"))
                for s in sessions[:20]:
                    cur = bx.check if ui.session_manager.current and s.id == ui.session_manager.current.id else " "
                    name = s.name[:40]
                    print(color.cyan(f"  {bx.v}  [{cur}] {s.id[:16]}  {name:40s}  {s.tool_count:3d} tools{bx.v}"))
                print(color.cyan(f"  {bx.bl}{h}{bx.br}"))
                print(color.dim("  /session load <id>  /session delete <id>"))
                continue
            if line.startswith("/session "):
                parts = line.split(maxsplit=2)
                sub = parts[1] if len(parts) > 1 else ""
                if sub == "load" and len(parts) > 2:
                    s = ui.session_manager.load(parts[2])
                    if s:
                        print(color.green(f"  loaded session: {s.name}"))
                    else:
                        print(color.red(f"  session not found: {parts[2]}"))
                elif sub == "delete" and len(parts) > 2:
                    if ui.session_manager.delete(parts[2]):
                        print(color.green(f"  deleted session: {parts[2]}"))
                    else:
                        print(color.red(f"  session not found: {parts[2]}"))
                else:
                    print(color.dim("  usage: /session load <id> | /session delete <id>"))
                continue
            if line == "/export":
                s = ui.session_manager.current
                if not s:
                    print(color.dim("  no active session to export"))
                    continue
                export_dir = Path.home() / "Desktop" / "aria_exports"
                export_dir.mkdir(parents=True, exist_ok=True)
                export_file = export_dir / f"aria_coder_{s.id}.txt"
                try:
                    with open(export_file, "w", encoding="utf-8") as f:
                        f.write(f"ARIA Coder Session: {s.name}\n")
                        f.write(f"Created: {s.created_at}\n")
                        f.write(f"Tools: {s.tool_count} | Errors: {s.error_count} | Files: {s.file_changes}\n")
                        f.write("=" * 60 + "\n\n")
                        for m in agent.messages:
                            role = m.get("role", "?")
                            content = m.get("content", "")[:500]
                            f.write(f"[{role.upper()}]\n{content}\n\n")
                    print(color.green(f"  exported to: {export_file}"))
                except Exception as e:
                    print(color.red(f"  export failed: {e}"))
                continue
            if line == "/theme":
                current = ui.get_theme()
                print(color.dim(f"  current theme: {current}"))
                print(color.dim("  /theme dark | /theme light | /theme claude"))
                continue
            if line.startswith("/theme "):
                t = line.split(maxsplit=1)[1].strip()
                if t in ("dark", "light", "claude"):
                    ui.set_theme(t)
                    print(color.green(f"  theme: {t}"))
                else:
                    print(color.red(f"  unknown theme: {t}"))
                continue
            if line in {"/confirm", "/auto"}:
                ui.auto_approve = agent.auto_approve = not agent.auto_approve
                state = "autonomous (free rein)" if agent.auto_approve else "confirm each action"
                print(color.yellow(f"  approval: {state}"))
                continue
            if line == "/changes":
                ch = agent.toolbox.changed_files
                print(color.dim("  " + (", ".join(ch) if ch else "no files changed yet")))
                continue
            if line == "/reset":
                agent.messages = agent.messages[:1]
                print(color.dim("  conversation reset"))
                continue
            if line in {"/gaps", "/scan"}:
                try:
                    import httpx
                    brain_url = os.environ.get("ARIA_SERVICE_URL", "http://localhost:8000")
                    token = os.environ.get("ARIA_INTERNAL_TOKEN", "")
                    headers = {"Authorization": f"Bearer {token}"} if token else {}
                    r = httpx.get(f"{brain_url}/api/aria/coder/gaps", headers=headers, timeout=15)
                    if r.status_code == 200:
                        data = r.json()
                        gaps = data.get("gaps", [])
                        if gaps:
                            bx = _BoxChars()
                            h = bx.h * 55
                            print(color.cyan(f"  {bx.tl}{h}{bx.tr}"))
                            print(color.cyan(f"  {bx.v}  Gaps ({len(gaps)} found){' ' * (43 - len(str(len(gaps))))}{bx.v}"))
                            print(color.cyan(f"  {bx.tm}{h}{bx.tm}"))
                            for i, g in enumerate(gaps[:20], 1):
                                sev = g.get("severity", "UNKNOWN")
                                sev_color = color.red if sev in ("CRITICAL",) else color.yellow if sev in ("HIGH",) else color.blue
                                auto = bx.check if g.get("auto_fixable") else "approval"
                                title = g.get("title", "?")[:65]
                                print(sev_color(f"  {bx.v}  {i:2d}  {title:65s}  {sev:8s}  {auto}  {bx.v}"))
                            print(color.cyan(f"  {bx.bl}{h}{bx.br}"))
                        else:
                            print(color.dim("  no gaps found"))
                    else:
                        print(color.dim(f"  brain returned {r.status_code}"))
                except Exception as e:
                    print(color.dim(f"  gap scan failed: {e}"))
                continue
            if line == "/status":
                try:
                    import httpx
                    brain_url = os.environ.get("ARIA_SERVICE_URL", "http://localhost:8000")
                    token = os.environ.get("ARIA_INTERNAL_TOKEN", "")
                    headers = {"Authorization": f"Bearer {token}"} if token else {}
                    r = httpx.get(f"{brain_url}/api/aria/health/perf", headers=headers, timeout=15)
                    if r.status_code == 200:
                        d = r.json()
                        sm = d.get("self_metrics", {})
                        src = d.get("sources", {})
                        inv = d.get("inventory", {})
                        cost = d.get("cost", {})
                        bx = _BoxChars()
                        h = bx.h * 55
                        print(color.cyan(f"  {bx.tl}{h}{bx.tr}"))
                        print(color.cyan(f"  {bx.v}  System Status{' ' * 43}{bx.v}"))
                        print(color.cyan(f"  {bx.tm}{h}{bx.tm}"))
                        comp = sm.get("composite", 0)
                        comp_str = f"{comp*100:.0f}/100" if comp else "--"
                        print(color.cyan(f"  {bx.v}  Composite score     {comp_str:<42}{bx.v}"))
                        print(color.cyan(f"  {bx.v}  Sources             {src.get('ok', '?')}/{src.get('total', '?')} OK{' ' * (39 - len(str(src.get('ok', '?'))) - len(str(src.get('total', '?'))))}{bx.v}"))
                        print(color.cyan(f"  {bx.v}  Knowledge facts     {str(inv.get('knowledge_facts', '?')):>8}{' ' * 35}{bx.v}"))
                        print(color.cyan(f"  {bx.v}  Intel signals       {str(inv.get('intel_signals', '?')):>8}{' ' * 35}{bx.v}"))
                        cost_str = f"${cost.get('monthly_usd', 0):.2f}" if cost.get('monthly_usd') else "--"
                        print(color.cyan(f"  {bx.v}  Monthly cost        {cost_str:<42}{bx.v}"))
                        print(color.cyan(f"  {bx.bl}{h}{bx.br}"))
                    else:
                        print(color.dim(f"  brain returned {r.status_code}"))
                except Exception as e:
                    print(color.dim(f"  status failed: {e}"))
                continue
            if line == "/history":
                try:
                    import httpx
                    brain_url = os.environ.get("ARIA_SERVICE_URL", "http://localhost:8000")
                    token = os.environ.get("ARIA_INTERNAL_TOKEN", "")
                    headers = {"Authorization": f"Bearer {token}"} if token else {}
                    r = httpx.get(f"{brain_url}/api/aria/autonomy/history?limit=24", headers=headers, timeout=15)
                    if r.status_code == 200:
                        history = r.json().get("history", [])
                        if history:
                            bx = _BoxChars()
                            h = bx.h * 55
                            print(color.cyan(f"  {bx.tl}{h}{bx.tr}"))
                            print(color.cyan(f"  {bx.v}  Composite Score History{' ' * 33}{bx.v}"))
                            print(color.cyan(f"  {bx.tm}{h}{bx.tm}"))
                            for hist in history[:24]:
                                ts = hist.get("timestamp", "")[:16]
                                score = hist.get("composite", 0)
                                bar_len = int(score * 20)
                                bar = "█" * bar_len + "░" * (20 - bar_len)
                                print(color.cyan(f"  {bx.v}  {ts}  {score*100:5.1f}%  {bar}{' ' * max(0, 20 - bar_len)}{bx.v}"))
                            print(color.cyan(f"  {bx.bl}{h}{bx.br}"))
                        else:
                            print(color.dim("  no history data"))
                    else:
                        print(color.dim(f"  brain returned {r.status_code}"))
                except Exception as e:
                    print(color.dim(f"  history failed: {e}"))
                continue
            if line == "/cost":
                try:
                    import httpx
                    brain_url = os.environ.get("ARIA_SERVICE_URL", "http://localhost:8000")
                    token = os.environ.get("ARIA_INTERNAL_TOKEN", "")
                    headers = {"Authorization": f"Bearer {token}"} if token else {}
                    r = httpx.get(f"{brain_url}/api/aria/cost/monthly/status", headers=headers, timeout=15)
                    if r.status_code == 200:
                        d = r.json()
                        bx = _BoxChars()
                        h = bx.h * 55
                        print(color.cyan(f"  {bx.tl}{h}{bx.tr}"))
                        print(color.cyan(f"  {bx.v}  Cost Dashboard{' ' * 42}{bx.v}"))
                        print(color.cyan(f"  {bx.tm}{h}{bx.tm}"))
                        spend = d.get('monthly_spend', 0)
                        cap = d.get('monthly_cap', 300)
                        remaining = d.get('remaining', 0)
                        print(color.cyan(f"  {bx.v}  Monthly spend     ${spend:<8.2f}{' ' * (34 - len(f'{spend:.2f}'))}{bx.v}"))
                        print(color.cyan(f"  {bx.v}  Monthly cap       ${cap:<8.2f}{' ' * (34 - len(f'{cap:.2f}'))}{bx.v}"))
                        print(color.cyan(f"  {bx.v}  Remaining         ${remaining:<8.2f}{' ' * (34 - len(f'{remaining:.2f}'))}{bx.v}"))
                        print(color.cyan(f"  {bx.bl}{h}{bx.br}"))
                    else:
                        print(color.dim(f"  brain returned {r.status_code}"))
                except Exception as e:
                    print(color.dim(f"  cost failed: {e}"))
                continue
            if line == "/model":
                print(color.dim(f"  model: {cfg.get('model', 'auto')}"))
                print(color.dim(f"  chain: deepseek (active) · anthropic (cooldown)"))
                continue
            if line == "/compact":
                before = len(agent.messages)
                agent.messages = agent.messages[:1] + agent.messages[-5:]
                print(color.dim(f"  compacted {before} → {len(agent.messages)} turns"))
                continue
            if line == "/memory":
                memory_path = cwd / ".aria" / "memory.md"
                if memory_path.exists():
                    print(color.dim(f"  memory ({memory_path}):"))
                    print(memory_path.read_text(encoding="utf-8", errors="replace"))
                else:
                    print(color.dim("  no memory file (.aria/memory.md)"))
                continue
            if line == "/diff":
                try:
                    r = agent.toolbox.run("git diff", timeout=15)
                    if r.is_error:
                        print(color.dim(f"  diff failed: {r.output[:200]}"))
                    elif r.output:
                        lines = r.output.splitlines()
                        for ln in lines[:40]:
                            if ln.startswith("+"):
                                print(color.green(f"  {ln[:200]}"))
                            elif ln.startswith("-"):
                                print(color.red(f"  {ln[:200]}"))
                            else:
                                print(color.dim(f"  {ln[:200]}"))
                        if len(lines) > 40:
                            print(color.dim(f"  ... ({len(lines) - 40} more lines)"))
                    else:
                        print(color.dim("  no uncommitted changes"))
                except Exception as e:
                    print(color.dim(f"  diff failed: {e}"))
                continue
            if line == "/plan":
                if agent.toolbox.plan:
                    bx = _BoxChars()
                    h = bx.h * 55
                    print(color.cyan(f"  {bx.tl}{h}{bx.tr}"))
                    print(color.cyan(f"  {bx.v}  Current Plan{' ' * 43}{bx.v}"))
                    print(color.cyan(f"  {bx.tm}{h}{bx.tm}"))
                    for p in agent.toolbox.plan:
                        step = p.get("step", "")
                        status = p.get("status", "pending")
                        sym = {"completed": bx.check, "in_progress": "~", "pending": " "}.get(status, " ")
                        print(color.cyan(f"  {bx.v}  [{sym}] {step[:65]:65s}{bx.v}"))
                    print(color.cyan(f"  {bx.bl}{h}{bx.br}"))
                else:
                    print(color.dim("  no plan set (ARIA hasn't called update_plan yet)"))
                continue
            if line == "/stats":
                bx = _BoxChars()
                h = bx.h * 55
                elapsed = time.time() - ui._session_start if ui._session_start else 0
                elapsed_str = f"{int(elapsed // 60)}m {int(elapsed % 60)}s" if elapsed >= 60 else f"{int(elapsed)}s"
                print(color.cyan(f"  {bx.tl}{h}{bx.tr}"))
                print(color.cyan(f"  {bx.v}  Session Statistics{' ' * 39}{bx.v}"))
                print(color.cyan(f"  {bx.tm}{h}{bx.tm}"))
                print(color.cyan(f"  {bx.v}  Duration          {elapsed_str:<42}{bx.v}"))
                print(color.cyan(f"  {bx.v}  Tool calls        {ui._tool_count:<3}{' ' * (42 - len(str(ui._tool_count)))}{bx.v}"))
                print(color.cyan(f"  {bx.v}  Errors            {ui._error_count:<3}{' ' * (42 - len(str(ui._error_count)))}{bx.v}"))
                print(color.cyan(f"  {bx.v}  File changes      {ui._file_changes:<3}{' ' * (42 - len(str(ui._file_changes)))}{bx.v}"))
                print(color.cyan(f"  {bx.v}  Operator messages {ui._operator_messages:<3}{' ' * (42 - len(str(ui._operator_messages)))}{bx.v}"))
                print(color.cyan(f"  {bx.v}  Tokens            {agent.llm.total_input_tokens + agent.llm.total_output_tokens:<6} ({agent.llm.total_input_tokens} in / {agent.llm.total_output_tokens} out){' ' * max(0, 20 - len(str(agent.llm.total_input_tokens + agent.llm.total_output_tokens)) - len(str(agent.llm.total_input_tokens)) - len(str(agent.llm.total_output_tokens)))}{bx.v}"))
                print(color.cyan(f"  {bx.bl}{h}{bx.br}"))
                continue
            if line == "/think":
                # Show the last assistant message's raw content
                for m in reversed(agent.messages):
                    if m.get("role") == "assistant" and m.get("content", "").strip():
                        print(color.dim(f"  {m['content'][:2000]}"))
                        if len(m["content"]) > 2000:
                            print(color.dim("  ... (truncated)"))
                        break
                else:
                    print(color.dim("  no assistant messages yet"))
                continue
            if line == "/clear":
                import os as _os
                _os.system("cls" if _os.name == "nt" else "clear")
                _banner(color, cfg, self_mode, guard, cwd, auto_approve=agent.auto_approve)
                continue
            if line == "/version":
                print(color.dim(f"  ARIA Coder v{__version__}"))
                print(color.dim(f"  Python: {sys.version.split()[0]}"))
                print(color.dim(f"  Platform: {sys.platform}"))
                continue
            if line == "/uptime":
                if ui._session_start:
                    elapsed = time.time() - ui._session_start
                    elapsed_str = f"{int(elapsed // 60)}m {int(elapsed % 60)}s" if elapsed >= 60 else f"{int(elapsed)}s"
                    print(color.dim(f"  session duration: {elapsed_str}"))
                else:
                    print(color.dim("  session not started"))
                continue
            if line == "/config":
                print(color.dim(f"  provider: {cfg.provider}"))
                print(color.dim(f"  model: {cfg.model}"))
                print(color.dim(f"  base_url: {cfg.base_url}"))
                print(color.dim(f"  max_tokens: {cfg.max_tokens}"))
                print(color.dim(f"  temperature: {cfg.temperature}"))
                print(color.dim(f"  timeout: {cfg.timeout}s"))
                print(color.dim(f"  mode: {'self (crucix)' if self_mode else 'general'}"))
                print(color.dim(f"  brain: {'wired' if brain_mod.brain_enabled(self_mode) else 'off'}"))
                print(color.dim(f"  approval: {'autonomous' if agent.auto_approve else 'confirm each'}"))
                continue
            last_task = line
            ui._log(f"[user] {line}")
            agent.run_until_complete(line)
    except KeyboardInterrupt:
        print("\n" + color.dim("  interrupted"))
    finally:
        _finalize(agent, ui, cfg, self_mode, last_task or "(interactive session)",
                  success=True, color=color)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="aria",
        description="ARIA Coder — a local Claude-Code-style coding agent powered "
                    "by ARIA's brain. Run inside any project directory.")
    parser.add_argument("task", nargs="*", help="Task to run one-shot. Omit for an interactive session.")
    parser.add_argument("-p", "--print", dest="oneshot", metavar="TASK", default=None,
                        help="Run a single task non-interactively and exit.")
    parser.add_argument("--confirm", "--ask", dest="confirm", action="store_true",
                        help="Ask for approval before each edit/command. Default is "
                             "full autonomy (free rein, no prompts).")
    parser.add_argument("--auto", "--yolo", dest="auto", action="store_true",
                        help=argparse.SUPPRESS)
    parser.add_argument("--self", dest="self_mode", action="store_true",
                        help="Force self-mode (crucix guardrails + brain wiring).")
    parser.add_argument("--general", dest="general", action="store_true",
                        help="Force general mode (no crucix constitution / brain wiring).")
    parser.add_argument("--provider", default="", help="LLM provider override (deepseek, openai, groq, ollama, ...).")
    parser.add_argument("--model", default="", help="Model override.")
    parser.add_argument("--no-color", action="store_true", help="Disable ANSI colour.")
    parser.add_argument("--theme", choices=["dark", "light", "claude"], default="dark",
                        help="Color theme (dark, light, claude).")
    parser.add_argument("--version", action="version", version=f"aria {__version__}")
    args = parser.parse_args(argv)

    args.auto = not args.confirm

    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:  # noqa: BLE001
            pass

    color = _Color(enabled=not args.no_color and sys.stdout.isatty()
                   and os.getenv("NO_COLOR") is None)
    cwd = Path.cwd()

    oneshot_text = args.oneshot or (" ".join(args.task).strip() if args.task else "")
    interactive = not oneshot_text

    agent, ui, cfg, self_mode, guard = _build_agent(cwd, args, color, interactive)

    # R-F1141: start the stdin reader daemon thread for operator mid-task interject
    if interactive:
        _start_stdin_reader()

    if interactive:
        _repl(agent, ui, cfg, self_mode, guard, cwd, color)
        return 0

    ui.start_session()
    _banner(color, cfg, self_mode, guard, cwd, auto_approve=agent.auto_approve)
    print()
    ui._log(f"[task] {oneshot_text}")
    result = agent.run_until_complete(oneshot_text)
    _finalize(agent, ui, cfg, self_mode, oneshot_text,
              success=not result.aborted, color=color)
    return 1 if result.aborted else 0


if __name__ == "__main__":
    sys.exit(main())
