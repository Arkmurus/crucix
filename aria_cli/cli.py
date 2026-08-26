"""R-F1260 — ``aria`` command-line entry point with clean, structured UI.

Run ``aria`` inside any project directory for an interactive coding session, or
``aria -p "task"`` / ``aria "task"`` for a one-shot run. ARIA detects whether
she is inside her own ecosystem (the crucix repo) and, if so, turns on the
constitutional guard and brain wiring; everywhere else she behaves as a general
Claude-Code-style coding agent.

UI design (R-F1260):
  - Text-first, not boxes-first: indentation, spacing, and color provide
    structure, not box-drawing characters.
  - Clear hierarchy: headings, body, metadata, and errors each have distinct
    visual weight.
  - Clean message boundaries: ``ARIA >`` for assistant, ``$`` for commands,
    ``!`` for errors, ``~`` for info, ``+`` for file changes.
  - Minimal decoration: box-drawing only for the banner header and session
    summary — everything else is clean text.

Features:
  - Interactive REPL with tab completion and history
  - One-shot mode via ``-p "task"`` or positional args
  - Operator mid-task interject (type while ARIA works)
  - Session management (save/load/delete/export)
  - Theme switching (/theme dark|light|claude)
  - Step counter [N/M] during multi-tool turns
  - Live output streaming with animated spinner
  - Error recovery suggestions
  - Persistent session log (~/.aria/sessions/)
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import queue
import sys
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from .redact import redact_secrets as _redact_secrets  # R-F2796 (D9)
from typing import Any, Dict, List, Optional

# R-F2550 — allow direct `python aria_cli/cli.py` invocation, not only the `aria` entry
# point / `python -m aria_cli.cli`. Run directly there is no package context, so the
# relative imports below throw a cryptic "attempted relative import with no known parent
# package". Bootstrap the package onto sys.path + set __package__ so BOTH styles work.
if __package__ in (None, ""):
    import os as _os_boot, sys as _sys_boot
    _sys_boot.path.insert(0, _os_boot.path.dirname(_os_boot.path.dirname(_os_boot.path.abspath(__file__))))
    __package__ = "aria_cli"

from . import __version__
from . import brain as brain_mod
from .agent import Agent, AgentUI
from .llm import LLMClient, LLMConfig, LLMError
from .model_identity import session_model_identity  # R-F4335 (C-281)
from .prompt import (build_compact_system_prompt, build_system_prompt,
                     compact_prompt_active)
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
    from prompt_toolkit.key_binding import KeyBindings
    # R-F1383 — patch_stdout lets the agent (running in a worker thread)
    # print ABOVE the always-active bottom input box without corrupting it.
    from prompt_toolkit.patch_stdout import patch_stdout as PTPatchStdout
    PROMPT_TOOLKIT_AVAILABLE = True

    class PTSafeFileHistory(PTFileHistory):
        """R-F1308 — surrogate-safe history. A Windows console paste (e.g.
        WhatsApp text with emoji) can contain lone UTF-16 surrogates;
        FileHistory.store_string does f.write(t.encode('utf-8')) which raises
        'surrogates not allowed' INSIDE the key handler — an unhandled
        event-loop exception that freezes the whole REPL (live incident
        2026-06-03 ~21:00). History is a convenience: sanitize, and never
        let it break the loop."""

        def store_string(self, string: str) -> None:
            try:
                safe = string.encode("utf-8", errors="replace").decode("utf-8")
                super().store_string(safe)
            except Exception:  # noqa: BLE001 — history must never crash input
                pass
except ImportError:
    PROMPT_TOOLKIT_AVAILABLE = False


def _sanitize_input_line(line: str) -> str:
    """R-F1308 — strip lone surrogates from operator input. They survive a
    Windows clipboard paste and later explode in ANY utf-8 encode: the history
    file, json.dumps for the LLM request, the brain wire. Replace rather than
    crash; the visible text is unchanged for normal input."""
    try:
        return line.encode("utf-8", errors="replace").decode("utf-8")
    except Exception:  # noqa: BLE001
        return line


# ── R-F1141: operator mid-task interject ────────────────────────────────────
# A background daemon thread reads sys.stdin lines into a thread-safe queue.
# The REPL reads from this queue (blocking) instead of calling input() directly.
# Agent.run_turn drains the queue non-blocking before each LLM call.
_OPERATOR_QUEUE: queue.Queue = queue.Queue()
_STDIN_THREAD: threading.Thread | None = None


def _stdin_reader() -> None:
    """Daemon thread: read operator input and put each line on the queue.
    When paused (_STDIN_PAUSED), yields stdin to the REPL prompt (R-F1265).

    Uses msvcrt.kbhit() + msvcrt.getwch() on Windows (console-level input,
    not stdin — avoids fd conflicts with the REPL). Falls back to
    select.select() + sys.stdin.readline() on other platforms.

    CRITICAL (R-F1270): Never use os.read() on stdin — it steals raw bytes
    from Python's buffered I/O, causing the REPL prompt to deadlock when
    both the thread and the main loop try to read from the same fd."""
    try:
        buf = ""
        while True:
            if _STDIN_PAUSED.is_set():
                threading.Event().wait(0.1)
                continue
            try:
                import msvcrt
                # Windows: use console-level input (kbhit + getwch) to avoid
                # conflicting with the REPL's stdin reads. This reads keypresses
                # directly from the console buffer, not from the stdin fd.
                if msvcrt.kbhit():
                    ch = msvcrt.getwch()
                    if ch == "\r" or ch == "\n":
                        if buf.strip():
                            _OPERATOR_QUEUE.put(buf.strip())
                            buf = ""
                    elif ch == "\b" or ch == "\x7f":  # backspace
                        buf = buf[:-1]
                    elif ch == "\x03":  # Ctrl+C
                        raise KeyboardInterrupt
                    elif len(buf) < 8192:   # R-F2093 — cap the interject buffer so a
                        buf += ch           # huge mid-task paste can't grow unbounded
                    # else: drop excess — an interject is a short message, not a file
                else:
                    threading.Event().wait(0.05)
            except ImportError:
                # Non-Windows: use select with timeout + readline
                try:
                    import select
                    r, _, _ = select.select([sys.stdin], [], [], 0.05)
                    if r:
                        line = sys.stdin.readline()
                        if not line:
                            break
                        stripped = line.strip()
                        if stripped:
                            _OPERATOR_QUEUE.put(stripped)
                    else:
                        threading.Event().wait(0.05)
                except (ImportError, OSError):
                    threading.Event().wait(0.05)
    except (EOFError, OSError, ValueError, KeyboardInterrupt):
        pass


_STDIN_PAUSED = threading.Event()

# R-F1407: Claude bridge idle-poll daemon — polls for new Claude messages
# every ~20s even while the CLI sits idle at the prompt. When a message
# arrives, it's queued for the next turn and the prompt is woken so ARIA
# acts without the operator having to type anything.
_CLAUDE_BRIDGE_EVENT = threading.Event()
_CLAUDE_BRIDGE_BASE: Path | None = None  # set once at REPL start
_PT_SESSION: Any = None  # R-F1407/R-F1426: module-level ref (no longer used for wake — prompt is self-checking via in-loop poller)
# R-F1423: poll the bridge every 5s (was 20s) so idle resume is near-instant
# after Claude replies, not up to 20s later.
_BRIDGE_POLL_INTERVAL_S = float(os.getenv("ARIA_BRIDGE_POLL_INTERVAL_S", "5"))
# R-F1426: in-loop poller interval for the concurrent prompt_async poller task.
# The poller checks _OPERATOR_QUEUE every ~2s and calls app.exit() on the same
# event loop when a Claude message arrives. 2s balances responsiveness vs CPU.
_PROMPT_POLL_INTERVAL_S = 2.0

# R-F1438 — crash-proofing latch for the prompt_toolkit input box. The anchored
# box (prompt_async + a per-iteration asyncio.run) can raise (e.g. a cross-
# event-loop error), and historically that took down the WHOLE REPL. Once the
# box fails once, latch this True and fall back to the plain input() prompt for
# the rest of the session — degraded, but the CLI stays alive. Relaunch to retry.
_PT_PROMPT_BROKEN = [False]


def _wake_prompt_threadsafe() -> None:
    """R-F1426 — no-op. The prompt is now self-checking via a concurrent
    in-loop task that polls _OPERATOR_QUEUE and calls app.exit() on the
    SAME event loop (thread-safe by construction). No cross-thread wake
    needed. Kept as a no-op for backward compat with the bridge poller
    call site. Never raises (daemon must survive)."""
    pass


def _claude_bridge_poller() -> None:
    """Daemon thread: poll the bridge for new Claude→ARIA messages every ~20s.

    When a message is found, queue it on _OPERATOR_QUEUE and signal the
    REPL loop to wake and process it. Never breaks the prompt loop.
    """
    base = _CLAUDE_BRIDGE_BASE
    if not base:
        return
    try:
        from . import bridge as _bridge
    except Exception:
        return
    while True:
        try:
            new = _bridge.read_new(base, reader="aria")
        except Exception:
            threading.Event().wait(20)
            continue
        if new:
            # R-F2051: only WAKE the idle prompt for REPLIES to a question ARIA
            # asked — unsolicited notes must not hijack the operator's task (see
            # Agent._drain_claude_bridge). Notes are left for that drain to show
            # as a dim, non-actioned info line. ARIA_CLI_BRIDGE_NOTES=1 opts in.
            inject_notes = os.getenv("ARIA_CLI_BRIDGE_NOTES", "").strip() in {"1", "true", "yes"}
            queued = False
            for m in new:
                text = (m.get("text") or "").strip()
                if not text:
                    continue
                if not m.get("reply_to") and not inject_notes:
                    continue
                _OPERATOR_QUEUE.put(
                    "[MESSAGE FROM CLAUDE — your senior reviewer, via the agent "
                    "bridge. This is reference/guidance: read it, but STAY ON THE "
                    "OPERATOR'S CURRENT TASK unless it directly bears on what you "
                    "are doing right now. Do not switch tasks because of it.]\n" + text
                )
                queued = True
            if queued:
                _CLAUDE_BRIDGE_EVENT.set()
            # R-F1426: no wake needed — the prompt is self-checking via a
            # concurrent in-loop task that polls _OPERATOR_QUEUE and calls
            # app.exit() on the SAME event loop (thread-safe by construction).
            # The daemon just queues the message; the in-loop poller picks it
            # up within ~2s and exits the prompt.
        threading.Event().wait(_BRIDGE_POLL_INTERVAL_S)



def _pause_stdin_reader() -> None:
    """Pause the stdin reader thread so the REPL prompt can read stdin directly.
    Used when prompt_toolkit is available and takes over stdin (R-F1265)."""
    _STDIN_PAUSED.set()


def _resume_stdin_reader() -> None:
    """Resume the stdin reader thread after the REPL prompt finishes (R-F1265)."""
    _STDIN_PAUSED.clear()


def _start_stdin_reader() -> None:
    """Start the stdin reader daemon thread (idempotent)."""
    global _STDIN_THREAD
    if _STDIN_THREAD is not None and _STDIN_THREAD.is_alive():
        return
    _STDIN_PAUSED.clear()
    _STDIN_THREAD = threading.Thread(target=_stdin_reader, daemon=True, name="stdin-reader")
    _STDIN_THREAD.start()


def _read_operator_input(prompt: str = "") -> str:
    """Read a line from the operator (blocking).

    R-F1270: Uses input() directly when the REPL is active (stdin is a TTY),
    which avoids the deadlock caused by reading from the background thread's
    queue when the thread is paused. The background thread is ONLY for mid-task
    interject (typing while ARIA works), NOT for REPL input.

    Falls back to the operator queue for non-TTY mode (piped input).
    """
    if not sys.stdin.isatty():
        # Non-TTY: read from the queue (fed by the background thread)
        try:
            return _OPERATOR_QUEUE.get(timeout=30)
        except queue.Empty:
            return ""
        except (EOFError, OSError):
            return ""

    # TTY mode: use input() directly — this is the REPL prompt.
    # Do NOT read from the queue here; the queue is for mid-task interject only.
    try:
        return input(prompt).strip()
    except EOFError:
        return ""
    except KeyboardInterrupt:
        return ""


def _read_line_robust(prompt_fn, fallback_fn, on_box_failure=None) -> str:
    """Read one operator line, guaranteeing the prompt box can NEVER crash the
    REPL (R-F1438).

    Tries ``prompt_fn()`` (the prompt_toolkit anchored box). On ANY error other
    than EOFError/KeyboardInterrupt (which are control flow and propagate), it
    latches ``_PT_PROMPT_BROKEN`` and permanently falls back to ``fallback_fn()``
    (the plain input() prompt) for the rest of the session. ``on_box_failure``,
    if given, is called once with the exception so the caller can print a notice.

    Pure/side-effect-light by design so it is unit-testable without a terminal.
    """
    if prompt_fn is not None and not _PT_PROMPT_BROKEN[0]:
        try:
            return prompt_fn()
        except (EOFError, KeyboardInterrupt):
            raise
        except Exception as e:  # noqa: BLE001 — the box must never kill the REPL
            _PT_PROMPT_BROKEN[0] = True
            if on_box_failure is not None:
                try:
                    on_box_failure(e)
                except Exception:
                    pass
    return fallback_fn()


# ── R-F2053: proactive terminal-capability negotiation ──────────────────────
# The anchored prompt_toolkit input box needs a real interactive console. Under
# Git Bash / MSYS / winpty / a piped or redirected stdio, stdout is a pipe with
# no Windows console screen buffer, so prompt_toolkit's Win32Output raises
# NoConsoleScreenBufferError — historically AT PTPromptSession() construction,
# which is upstream of _read_line_robust's per-prompt guard and so took down the
# whole REPL on launch. Rather than build the box and crash, we decide UP FRONT
# whether it can run and pick the input mode deliberately, telling the operator
# which mode they're in and why. Cached after the first probe.
_PT_CAPABILITY: tuple[bool, str] | None = None


def _probe_prompt_toolkit_capability() -> tuple[bool, str]:
    """Return (can_use_anchored_box, reason) for THIS terminal, without
    constructing — and possibly crashing — the full prompt session.

    The authoritative test is prompt_toolkit's own ``create_output()``: it is the
    exact call that raises on a console-less terminal, so probing it here predicts
    whether ``PTPromptSession()`` will succeed. A False result is normal control
    flow (use the basic line-mode prompt), never an error."""
    global _PT_CAPABILITY
    if _PT_CAPABILITY is not None:
        return _PT_CAPABILITY
    if not PROMPT_TOOLKIT_AVAILABLE:
        _PT_CAPABILITY = (False, "prompt_toolkit not installed")
        return _PT_CAPABILITY
    # Honour an explicit operator override either way (CI, odd terminals).
    forced = os.getenv("ARIA_CLI_BASIC_PROMPT", "").strip().lower()
    if forced in {"1", "true", "yes"}:
        _PT_CAPABILITY = (False, "ARIA_CLI_BASIC_PROMPT set")
        return _PT_CAPABILITY
    # An anchored box requires an interactive TTY on both ends.
    try:
        if not (sys.stdin and sys.stdin.isatty() and sys.stdout and sys.stdout.isatty()):
            _PT_CAPABILITY = (False, "stdin/stdout is not an interactive terminal")
            return _PT_CAPABILITY
    except Exception:  # noqa: BLE001 — a terminal we can't even query → basic mode
        _PT_CAPABILITY = (False, "terminal not queryable")
        return _PT_CAPABILITY
    try:
        from prompt_toolkit.output.defaults import create_output
        create_output(stdout=sys.stdout)  # raises on a console-less terminal
        _PT_CAPABILITY = (True, "interactive console")
    except Exception as exc:  # noqa: BLE001 — any failure → basic line mode
        _PT_CAPABILITY = (
            False,
            f"{type(exc).__name__}: no console screen buffer "
            "(Git Bash / MSYS / winpty / redirected stdio)",
        )
    return _PT_CAPABILITY


# ── Session log ─────────────────────────────────────────────────────────────
_SESSION_DIR = Path.home() / ".aria" / "sessions"


def _ensure_session_dir() -> Path:
    _SESSION_DIR.mkdir(parents=True, exist_ok=True)
    return _SESSION_DIR


# ── R-F1310: liveness files for the aria-forever supervisor ─────────────────
# heartbeat = touched on REPL activity + tool results/stream chunks (throttled);
# busy      = exists while a turn is in flight. The supervisor restarts the CLI
# when busy is set but the heartbeat stops advancing (stall), or on crash.
_HB_LAST = 0.0


def _hb_touch(force: bool = False) -> None:
    """Touch the heartbeat file (throttled to one write per ~5s)."""
    global _HB_LAST
    now = time.time()
    if not force and now - _HB_LAST < 5:
        return
    _HB_LAST = now
    try:
        (_ensure_session_dir() / "heartbeat").write_text(str(now), encoding="utf-8")
    except Exception:  # noqa: BLE001 — liveness must never break the CLI
        pass


def _busy_set(on: bool) -> None:
    """Mark a turn as in-flight (the supervisor only treats a stale heartbeat
    as a stall while busy — idle at the prompt is never a stall)."""
    p = _ensure_session_dir() / "busy"
    try:
        if on:
            p.write_text(str(time.time()), encoding="utf-8")
        else:
            p.unlink(missing_ok=True)
    except Exception:  # noqa: BLE001
        pass


def _session_log_path() -> Path:
    ts = time.strftime("%Y%m%d_%H%M%S")
    return _ensure_session_dir() / f"session_{ts}.log"


# ── R-F1383: professional terminal — threaded turns + anchored input box ────
# The operator's complaints (2026-06-06): (1) no Claude-Code-style input box;
# (2) typing mid-task was INVISIBLE (the msvcrt reader consumes keys without
# echo); (3) module logging flooded the screen ("takes the entire screen
# running logs"). Design: the agent turn runs in a WORKER thread while the
# main thread keeps the prompt_toolkit input box open at the bottom
# (patch_stdout renders agent output ABOVE it). A line submitted while ARIA
# works goes to _OPERATOR_QUEUE — visible, editable, with history — and the
# agent drains it at its next step (R-F1141 mechanism, unchanged).
_TURN_STATE: dict = {"busy": False, "task": "", "started": 0.0}


def _turn_worker(agent, line: str, color, ui=None) -> threading.Thread:
    """Run one agent turn in a daemon worker thread (R-F1383).

    Only used when auto-approve is ON (the default): in --confirm mode the
    agent calls input() for approvals, which must own stdin — those turns
    keep the legacy inline path. R-F1385: while the worker runs behind the
    live input box, the UI goes into ANCHORED mode (spinner off — the bottom
    toolbar carries the activity; \\r redraws would spam lines above the box)."""
    _TURN_STATE.update(busy=True, task=line[:64], started=time.time())
    _busy_set(True)
    _hb_touch(force=True)
    if ui is not None:
        ui.anchored = True

    def _w() -> None:
        try:
            agent.run_until_complete(line)
        except Exception as exc:  # noqa: BLE001 — surface, never kill the REPL
            print("\n" + color.red(f"  ❌ Error: {_sanitize_output(str(exc))}"))
            print(color.dim("  The LLM provider may be unavailable. Try again later."))
            try:
                if agent.messages:
                    agent.messages = agent.messages[:-1]
            except Exception:
                pass
        finally:
            if ui is not None:
                ui.anchored = False
            _TURN_STATE.update(busy=False, task="")
            _busy_set(False)
            _hb_touch(force=True)
            print(color.dim("  ✓ task finished — the input box is live"))

    t = threading.Thread(target=_w, daemon=True, name="aria-turn")
    t.start()
    return t


def _box_width() -> int:
    """Terminal-fitted box width shared by the top rule, base rule and toolbar
    (R-F1385 — mismatched widths looked broken)."""
    import shutil as _shutil
    return max(40, min(_shutil.get_terminal_size((100, 24)).columns - 2, 120))


def _toolbar_text(cfg, self_mode: bool):
    """R-F1389 — clean toolbar: base rule + one-line status.
    The base rule mirrors the top rule from _boxed_prompt, completing the
    input box visually. The status line sits below it, always visible."""
    w = _box_width()
    base = "─" * w
    if _TURN_STATE["busy"]:
        secs = int(time.time() - (_TURN_STATE["started"] or time.time()))
        mm, ss = divmod(secs, 60)
        status = (
            f"● working — {_TURN_STATE['task']}  ({mm:02d}:{ss:02d})"
            f"  ·  type + Enter to guide mid-task"
        )
    else:
        mode = "self" if self_mode else "general"
        status = f"○ ready  ·  {cfg.provider}/{cfg.model}  ·  {mode}  ·  /help for commands"
    return base + "\n" + status[: w - 1]


def _quiet_console_logging() -> None:
    """R-F1383 — keep the terminal professional: ALL module logging (aria_service
    imports in self-mode emit MASTERY/absorb/intel walls) goes to a session
    logfile; only ERROR+ reaches the console. The CLI's own UI prints via
    print(), not logging, so nothing user-facing is lost."""
    import logging as _logging
    root = _logging.getLogger()
    try:
        logfile = _ensure_session_dir() / "cli.log"
        fh = _logging.FileHandler(str(logfile), encoding="utf-8")
        fh.setLevel(_logging.INFO)
        fh.setFormatter(_logging.Formatter("%(asctime)s %(levelname)s %(name)s | %(message)s"))
        # Replace any existing console handlers with the file handler + a
        # console handler that only lets ERROR+ through.
        for h in list(root.handlers):
            root.removeHandler(h)
        ch = _logging.StreamHandler()
        ch.setLevel(_logging.ERROR)
        ch.setFormatter(_logging.Formatter("  %(levelname)s %(name)s: %(message)s"))
        root.addHandler(fh)
        root.addHandler(ch)
        root.setLevel(_logging.INFO)
    except Exception:  # noqa: BLE001 — logging cosmetics must never break the CLI
        pass


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
    just works. Existing env vars always win (never clobbered).

    R-F3398 — the implementation now lives in `aria_service.env_bootstrap` so
    the training tooling can load the same environment without importing this
    module (and its ~390ms of prompt_toolkit startup). Two copies of a loader
    drift; this signature is kept because callers pass an explicit path.
    """
    from aria_service.env_bootstrap import load_env_file

    return load_env_file(path)


def _repo_relative_resolver(repo_root: Path):
    def resolve(path: str) -> str:
        try:
            rel = Path(path).resolve().relative_to(repo_root)
            return rel.as_posix()
        except Exception:  # noqa: BLE001
            return path
    return resolve


# ── terminal UI (R-F1260: clean text structure) ─────────────────────────────
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
    Used only by _banner and _finalize (R-F1260).
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
        # R-F1683: the Windows-10+ heuristic below assumed conhost renders Unicode
        # even under cp1252 — but if sys.stdout.encoding is LITERALLY cp1252 (cmd.exe
        # default, or a redirected/reconfigured stream), Python ENCODES output as
        # cp1252 and the box chars (═ etc.) raise UnicodeEncodeError mid-print, which
        # corrupts stdout and (in the full test suite) hangs the next test's output.
        # Display capability != byte encoding: if there is an explicit encoding that
        # cannot encode the box chars, fall back to ASCII regardless of OS version.
        if enc:
            try:
                "═╔╗║╚╝".encode(enc)
            except (UnicodeEncodeError, LookupError):
                return False
        # R-F1202: Windows 10+ terminals (conhost/Windows Terminal) render Unicode
        # box-drawing — safe now that we've confirmed the encoding can encode them.
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
    def mr(self) -> str: return "╣" if self._unicode else "|"   # middle-right (right tee)
    @property
    def check(self) -> str: return "✓" if self._unicode else "v"
    @property
    def cross(self) -> str: return "✗" if self._unicode else "x"


# ── shared box-drawing helpers ──────────────────────────────────────────────
# R-F1258: extracted to module level to avoid duplication in _banner, _finalize,
# /help, /sessions, /gaps, /status, /history, /cost, /plan, /stats.
import re as _re


def _visible_len(s: str) -> int:
    """Return the DISPLAY WIDTH (terminal columns) of a string, stripping ANSI
    escape codes. R-F2093: was len() — which mis-measured emoji (✓/❌/🚀 and any
    pasted emoji/CJK) as 1 column when they occupy 2, shifting every box border.
    wcswidth counts wide chars as 2 and zero-width/combining as 0; it returns -1
    on a non-printable control char, in which case we fall back to len()."""
    plain = _re.sub(r'\033\[[0-9;]*m', '', s)
    try:
        from wcwidth import wcswidth
        w = wcswidth(plain)
        if w >= 0:
            return w
    except Exception:
        pass
    return len(plain)


# R-F2093 — strip terminal control sequences + C0/C1 control chars from UNTRUSTED
# text (tool/command output, file contents, exception messages) BEFORE printing.
# A file or command whose output contains ANSI codes, a carriage return, a
# backspace, or an OSC title-set sequence could otherwise corrupt or SPOOF the
# operator's terminal (e.g. overwrite a line with a fake "success", or inject a
# colored fake error). The coder agent runs in arbitrary repos, so its tool output
# is untrusted. Keeps \t and \n; converts \r → \n so line structure survives.
_OUTPUT_CTRL_RE = _re.compile(
    r'\x1b\[[0-9;?]*[ -/]*[@-~]'        # CSI sequences (incl SGR colour)
    r'|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)'  # OSC sequences (e.g. set-window-title)
    r'|\x1b[@-Z\\-_]'                   # other single-char escape sequences
    r'|[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f]'  # C0 (excl \t \n) + DEL + C1
)


def _sanitize_output(s: str) -> str:
    """Make UNTRUSTED text safe to print to the terminal (see _OUTPUT_CTRL_RE).

    R-F2796 (D9) — "safe to print" now includes "contains no customer secrets".
    A terminal transcript is effectively published: it gets screenshotted,
    pasted into tickets and shared. Before this, `aria` running `printenv`,
    `flyctl secrets list` or `cat .env` rendered tokens verbatim to the screen.

    This is the single choke point every untrusted-output site already routes
    through — the error path (:470), tool-result rendering (:1403/:1418/:1447)
    and live streamed command output (:1544) — so redacting HERE covers the
    product rather than one call site. Same failure class as R-F2705 on the WA
    tier; the key vocabulary is mirrored from its log-redact module.
    """
    if not s:
        return s
    s = s.replace('\r\n', '\n').replace('\r', '\n')
    s = _OUTPUT_CTRL_RE.sub('', s)
    return _redact_secrets(s)


def _is_mistyped_command(token: str) -> bool:
    """R-F2095 — True when `token` is a single slash-command shape (/word), so an
    unknown one is reported instead of silently becoming an agent task (which wastes
    tokens + confuses the operator). A path (/etc/hosts) or a sentence does NOT
    match — only a leading /word — so genuine input still runs as a task."""
    return bool(_re.match(r"^/[A-Za-z][A-Za-z0-9_-]*$", token or ""))


def _content(s: str) -> str:
    """Pad string to exactly 56 visible chars for box content.
    Strips ANSI codes before measuring so colored text aligns correctly.
    If the visible content exceeds 56 chars, it is truncated with ``…``.
    Used only by _box_content (R-F1260, R-F1265)."""
    visible = _visible_len(s)
    if visible <= 56:
        return s + " " * (56 - visible)

    # Truncate to fit: walk the string char by char, counting visible width.
    # Stop at 53 visible chars, append "…" (1 visible char) = 54 total,
    # leaving 2 trailing spaces so the right border doesn't touch the text.
    count = 0
    result: list[str] = []
    i = 0
    while i < len(s):
        ch = s[i]
        if ch == "\033":
            # ANSI escape sequence — keep it intact
            end = s.find("m", i)
            if end != -1:
                result.append(s[i:end + 1])
                i = end + 1
                continue
        if _visible_len(ch) > 0:
            if count >= 53:
                result.append("…")
                break
            count += 1
        result.append(ch)
        i += 1
    return "".join(result) + "  "


def _box_line(corner_left: str, h: str, corner_right: str) -> str:
    """Build a full-width box border line: '  {corner_left}{56 h}{corner_right}'.
    Used only by _banner and _finalize (R-F1260)."""
    return f"  {corner_left}{h * 56}{corner_right}"


def _box_content(v: str, text: str) -> str:
    """Build a full-width box content line: '  {v}{56 padded text}{v}'.
    Used by _banner, _finalize, and command panels (R-F1260, R-F1263)."""
    return f"  {v}{_content(text)}{v}"


def _sep_line(h: str) -> str:
    """Build a simple separator line: '  {56 h}'.
    Used by command panels for clean text separation (R-F1263)."""
    return f"  {h * 56}"


# ── R-F1267: code detection helpers ──────────────────────────────────────────
def _guess_language(code: str) -> str:
    """Guess the programming language of a code snippet (R-F1267).
    Uses simple heuristics — no external dependency needed."""
    code_stripped = code.strip()
    if not code_stripped:
        return "text"
    lines = code_stripped.splitlines()
    first_line = lines[0].strip() if lines else ""

    # Shell: #!/bin/bash, $, export, alias (check before JS export)
    if first_line.startswith("#!/") or first_line.startswith("$ "):
        return "bash"
    if first_line.startswith("export "):
        # Bash: "export FOO=bar" — no braces, no semicolons typically
        if "=" in first_line and "{" not in first_line:
            return "bash"
        return "typescript"
    if first_line.startswith("alias "):
        return "bash"
    if first_line.startswith("function "):
        # Bash: "function foo {" — no parentheses typically
        # JS: "function foo() {" — has parentheses
        if "(" in first_line and ")" in first_line:
            return "typescript"
        return "bash"

    # JavaScript/TypeScript: const/let/var/import
    if first_line.startswith("import "):
        # Python: "import os" or "import os.path" — no braces
        # JS: "import { foo } from 'bar'" — has braces
        if "{" in first_line or "}" in first_line:
            return "typescript"
        # Python import — no semicolon
        if not code_stripped.rstrip().endswith(";"):
            return "python"
        return "typescript"
    if any(first_line.startswith(kw) for kw in ("const ", "let ", "var ")):
        return "typescript"

    # Python: from/def/class/print/if __name__
    if any(first_line.startswith(kw) for kw in ("from ", "def ", "class ", "print", "if ")):
        # Check for JS-specific patterns: ; at end, =>
        if code_stripped.rstrip().endswith(";") or "=>" in code_stripped[:200]:
            return "typescript"
        return "python"
    if "def " in code_stripped[:200] or "class " in code_stripped[:200]:
        return "python"
    if "lambda " in code_stripped[:200] or "yield " in code_stripped[:200]:
        return "python"

    # Shell: #!/bin/bash, $, export, alias
    if first_line.startswith("#!/") or first_line.startswith("$ "):
        return "bash"
    if any(first_line.startswith(kw) for kw in ("export ", "alias ", "function ")):
        return "bash"

    # JSON: starts with { or [
    if first_line.startswith("{") or first_line.startswith("["):
        return "json"

    # XML/HTML: starts with <
    if first_line.startswith("<"):
        return "xml" if first_line.startswith("<?xml") else "html"

    # YAML: starts with --- or key: value
    if first_line == "---" or (":" in first_line and not first_line.startswith("#")):
        return "yaml"

    # JavaScript/TypeScript: const/let/var/function/import from
    if any(first_line.startswith(kw) for kw in ("const ", "let ", "var ", "function ", "import ", "export ")):
        return "typescript"

    # Go: package/func
    if first_line.startswith("package ") or first_line.startswith("func "):
        return "go"

    # Rust: fn/let mut/use
    if first_line.startswith("fn ") or first_line.startswith("use ") or first_line.startswith("let "):
        return "rust"

    # SQL: SELECT/INSERT/UPDATE/DELETE/CREATE
    if any(first_line.upper().startswith(kw) for kw in ("SELECT", "INSERT", "UPDATE", "DELETE", "CREATE", "ALTER", "DROP")):
        return "sql"

    # Dockerfile
    if first_line.upper().startswith("FROM ") or first_line.upper().startswith("RUN "):
        return "dockerfile"

    # Markdown
    if first_line.startswith("# ") or first_line.startswith("## "):
        return "markdown"

    # Diff: starts with --- or +++
    if first_line.startswith("---") or first_line.startswith("+++"):
        return "diff"

    return "text"


def _looks_like_code(text: str) -> bool:
    """Heuristic: does this text look like code rather than plain output? (R-F1267)
    Returns True if the text has significant code-like characteristics."""
    if not text or len(text) < 20:
        return False
    lines = text.splitlines()
    if len(lines) < 2:
        return False

    # Count code-like patterns
    code_patterns = 0
    for line in lines[:20]:
        stripped = line.strip()
        if not stripped:
            continue
        # Indentation (code is typically indented)
        if line.startswith("    ") or line.startswith("\t"):
            code_patterns += 1
        # Brackets
        if "{" in stripped or "}" in stripped:
            code_patterns += 2
        # Semicolons
        if stripped.endswith(";"):
            code_patterns += 1
        # Assignment
        if " = " in stripped and not stripped.startswith("#"):
            code_patterns += 1
        # Function/class definitions
        if stripped.startswith("def ") or stripped.startswith("class ") or stripped.startswith("fn "):
            code_patterns += 3
        # Imports
        if stripped.startswith("import ") or stripped.startswith("from ") or stripped.startswith("use "):
            code_patterns += 2
        # Comments
        if stripped.startswith("#") or stripped.startswith("//") or stripped.startswith("/*"):
            code_patterns += 1

    return code_patterns >= 4


class TerminalUI(AgentUI):
    """Professional terminal UI with Rich rendering, markdown, and syntax highlighting.

    Design principles (R-F1267):
      - Rich rendering: markdown, code blocks with syntax highlighting, tables
      - Clean hierarchy: headings, body, metadata, and errors each have distinct
        visual weight.
      - Claude-Code parity: streaming with live rendering, inline code blocks,
        diff highlighting, file tree display
      - Graceful degradation: falls back to clean ANSI text when Rich is unavailable
      - Compact but readable: shows what matters, hides what doesn't.

    Features:
      - Rich markdown rendering for assistant messages
      - Syntax-highlighted code blocks
      - Rich diff rendering (/diff command)
      - File tree display for changed files
      - Live streaming with markdown rendering
      - Animated spinner with live output preview
      - Progress bar for long operations
      - Error recovery suggestions
      - Session management (save/load/delete/export)
      - Theme switching (/theme dark|light|claude)
      - Tab completion for REPL commands
      - Multi-line input editing (Alt+Enter)
      - Command palette (Ctrl+K)
      - Persistent session log
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
        # R-F1385 — anchored mode: a threaded turn is running behind the live
        # input box. The \r\033[K spinner CANNOT redraw in place there —
        # patch_stdout turns every tick into a NEW line above the box (the
        # "entire screen running logs" mess). In anchored mode the spinner is
        # OFF; the bottom toolbar carries the live activity instead.
        self.anchored = False
        # R-F1354: Claude-Code-style glyphs with cp1252 ASCII fallback. Reuses
        # the same encoding probe _BoxChars uses (utf in stdout encoding, or
        # Windows 10+ conhost which renders Unicode even under cp1252).
        self._unicode = _BoxChars._probe_unicode()
        # token count for the spinner ` · ↓ N tokens` tail; set by the agent
        # wiring (callable returning an int) — None when unavailable.
        self._token_getter: "callable | None" = None
        self._spin_stop: threading.Event | None = None
        self._spin_thread: threading.Thread | None = None
        self._stream_active = False
        self._streamed_this_turn = False
        self._spin_label = "thinking"
        self._spin_start = 0.0
        self._last_output = ""
        self._last_static = 0.0
        self._step_number = 0
        self._total_steps = 0
        self._session_log: Path | None = None
        self._last_command = ""
        self._needs_leading_newline = False
        self._session_start = 0.0
        self._tool_count = 0
        self._error_count = 0
        self._file_changes = 0
        self._operator_messages = 0
        self.session_manager = SessionManager()
        self._rich_console = RichConsole() if RICH_AVAILABLE and color.on else None
        # R-F1267: streaming buffer for live markdown rendering
        self._stream_buffer = ""
        self._stream_line_buf = ""  # R-F1390: anchored-mode line coalescing
        self._stream_code_block = False

    def _tc(self, key: str, s: str) -> str:
        """Apply a theme color to text (R-F1260)."""
        code = self._theme.get(key, "0")
        return f"\033[{code}m{s}\033[0m" if self.c.on else s

    # R-F1354: Claude-Code-style glyphs with ASCII fallback for cp1252 terminals.
    _GLYPHS = {
        "bullet": ("●", "*"),    # tool-call filled bullet
        "branch": ("⎿", "\\"),   # tool-result tree branch
        "spark": ("✦", "*"),     # spinner sparkle
        "down": ("↓", "v"),      # tokens-down arrow
        "ellipsis": ("…", "..."),
    }

    def _g(self, key: str) -> str:
        """Return the unicode glyph or its ASCII fallback for this terminal."""
        uni, ascii_ = self._GLYPHS[key]
        return uni if self._unicode else ascii_

    # R-F1354: Claude-Code-style whimsical gerunds, rotated by a counter.
    _GERUNDS = ("Pondering", "Moseying", "Noodling", "Schlepping",
                "Conjuring", "Percolating", "Ruminating", "Finessing")

    @staticmethod
    def _fmt_elapsed(seconds: float) -> str:
        """Format elapsed seconds as ``Xm Ys`` (>=60s) or ``Ys`` (R-F1354)."""
        s = int(seconds)
        return f"{s // 60}m {s % 60}s" if s >= 60 else f"{s}s"

    def _spin_line(self, counter: int, elapsed: float, *, tokens: int | None = None,
                   tail: str = "") -> str:
        """Build the spinner line (R-F1354, extracted for testability).

        Format: ``{✦} {Gerund}… ({elapsed} · ↓ {tokens} tokens){tail}``.
        The ``· ↓ N tokens`` segment appears ONLY when ``tokens`` is given.
        Gerund rotates by ``counter`` (deterministic, not random). Sparkle is
        yellow; the body is dim.
        """
        spark = self.c.yellow(self._g("spark"))
        gerund = self._GERUNDS[counter % len(self._GERUNDS)]
        inner = self._fmt_elapsed(elapsed)
        if tokens is not None:
            inner += f" {self.c.dim('·')} {self._g('down')} {tokens} tokens"
        body = self.c.dim(f"{gerund}{self._g('ellipsis')} ({inner}){tail}")
        return f"{spark} {body}"

    def set_theme(self, theme: str) -> None:
        """Switch color theme (R-F1260)."""
        if theme in self.THEMES:
            self._theme_name = theme
            self._theme = self.THEMES[theme]

    def get_theme(self) -> str:
        return self._theme_name

    def start_session(self) -> None:
        """Called at session start to create the log file and session record (R-F1260)."""
        self._session_start = time.time()
        self._session_log = _session_log_path()
        _append_log(self._session_log, f"ARIA Coder v{__version__}")
        _append_log(self._session_log, f"Started: {time.strftime('%Y-%m-%d %H:%M:%S')}")
        _append_log(self._session_log, f"Directory: {Path.cwd()}")
        _append_log(self._session_log, "─" * 60)
        self.session_manager.create()

    def _log(self, text: str) -> None:
        if self._session_log:
            _append_log(self._session_log, text)

    def _ensure_clear_line(self) -> None:
        """Clear any spinner or residual output before writing a new line (R-F1260)."""
        if self._spin_thread is not None:
            self.thinking_stop()
        # R-F1385 — anchored mode: there is no in-place spinner line to clear,
        # and \r\033[K through patch_stdout just emits noise above the box.
        if self.anchored:
            return
        sys.stdout.write("\r\033[K")
        sys.stdout.flush()

    def _render_markdown(self, text: str) -> None:
        """Render text with Rich markdown and syntax highlighting when available (R-F1389).

        R-F2796 (D9) — assistant prose can quote a secret the model just read out
        of the customer's environment (it was asked to inspect a .env, a deploy
        config, a log). That reaches the terminal here, so it is scrubbed too.

        `vendor_only`, NOT full: blanket redaction would break the legitimate
        case where the customer asks ARIA to GENERATE a value ("give me a
        JWT_SECRET") — a name-anchored rule would blank exactly what they asked
        for. Third-party vendor tokens have no such legitimate use, so those go.
        """
        text = _redact_secrets(text, mode="vendor_only")
        if self._rich_console:
            try:
                # Check if text contains code blocks
                if "```" in text:
                    # Use Rich Markdown for full rendering
                    md = RichMarkdown(text, code_theme="monokai" if self._theme_name == "dark" else "default")
                    self._rich_console.print(md)
                else:
                    # Check for inline code
                    if "`" in text:
                        md = RichMarkdown(text)
                        self._rich_console.print(md)
                    else:
                        # Plain text — just print with the ARIA prefix
                        print(self.c.cyan("  ARIA  ") + text)
                return
            except Exception:
                pass
        # Fallback: plain text
        print(self.c.cyan("  ARIA  ") + text)

    def assistant(self, text: str) -> None:
        """Print a complete assistant message with Rich rendering (R-F1267)."""
        self._ensure_clear_line()
        self._log(f"[aria] {text[:200]}")
        print()
        self._render_markdown(text)
        self._needs_leading_newline = True

    # ── live token streaming (never silent) ──────────────────────────────────
    def stream_delta(self, text: str) -> None:
        """Stream a chunk of LLM output. First chunk prints the prefix (R-F1260).

        R-F1390 — ANCHORED FLICKER FIX. Behind the live input box, output goes
        through prompt_toolkit's patch_stdout, which REPAINTS the whole box
        region on every flush. Token-by-token write+flush = hundreds of repaints
        = the "old-TV blink" the operator saw. In anchored mode we COALESCE to
        line granularity: buffer chunks, flush only on newline (remainder at
        stream_end). Repaints drop from per-token to per-line → text flows.
        The non-anchored path (one-shot / no prompt_toolkit) keeps the live
        per-token write — there's no patch_stdout app there to repaint."""
        _hb_touch()  # R-F1310: streaming = alive (throttled)
        if not self._stream_active:
            if self._spin_thread is not None:
                self.thinking_stop()
            if self._needs_leading_newline:
                print()
            sys.stdout.write(self.c.cyan("  ARIA  "))
            self._stream_active = True
            self._streamed_this_turn = True
            self._needs_leading_newline = False
            self._stream_buffer = ""
            self._stream_line_buf = ""
            self._stream_code_block = False
        # Track code block state for live rendering
        self._stream_buffer += text
        if "```" in self._stream_buffer:
            count = self._stream_buffer.count("```")
            self._stream_code_block = count % 2 == 1

        # ── R-F2804 (SECURITY) — redact BEFORE anything reaches stdout ────────
        # R-F2796 scrubbed assistant prose in _render_markdown, but agent.py:579
        # ALWAYS passes on_delta=stream_delta and only calls ui.assistant() when
        # the provider did NOT stream (agent.py:591). So on the primary path the
        # model's tokens came straight here and were written raw — the R-F2796
        # docstring claim was false for the case that actually happens. A model
        # asked "what's in my .env?" streamed the key verbatim.
        #
        # Redaction is applied at LINE granularity, never per token: a tokenizer
        # splits mid-string, so a secret routinely straddles two deltas and
        # per-chunk redaction would miss it. Both paths therefore buffer to a
        # newline. The non-anchored path loses per-token liveness as a result —
        # that is a deliberate trade: line-latency is a UX cost, printing a
        # customer's key is a security incident.
        self._stream_line_buf += text
        nl = self._stream_line_buf.rfind("\n")
        if nl != -1:
            _emit = self._stream_line_buf[: nl + 1]
            self._stream_line_buf = self._stream_line_buf[nl + 1:]
        elif len(self._stream_line_buf) > 16384:
            # R-F2129: a pathological stream with no newline must not grow the
            # coalescing buffer unbounded — flush the partial and reset.
            _emit = self._stream_line_buf
            self._stream_line_buf = ""
        else:
            return
        # vendor_only, matching _render_markdown: blanket redaction would blank a
        # value the customer explicitly asked ARIA to GENERATE.
        sys.stdout.write(_redact_secrets(_emit, mode="vendor_only"))
        sys.stdout.flush()

    def stream_end(self) -> None:
        """Finalise a streaming response (R-F1260)."""
        if self._stream_active:
            # R-F1390 — flush any buffered partial line from coalescing.
            # R-F2804 — the tail is the LAST thing a model emits, so a secret
            # with no trailing newline lives exactly here. Redact it too, or the
            # stream_delta guard is trivially escapable by omitting a newline.
            tail = getattr(self, "_stream_line_buf", "")
            if tail:
                sys.stdout.write(_redact_secrets(tail, mode="vendor_only"))
                self._stream_line_buf = ""
            sys.stdout.write("\n")
            sys.stdout.flush()
            self._stream_active = False
            self._needs_leading_newline = True
            self._stream_buffer = ""
            self._stream_line_buf = ""

    # ── step tracking ──────────────────────────────────────────────────────
    def set_step_context(self, current: int, total: int) -> None:
        """Called before each tool call to set the step counter (R-F1260)."""
        self._step_number = current
        self._total_steps = total

    def _step_prefix(self) -> str:
        if self._total_steps > 0:
            return f"[{self._step_number}/{self._total_steps}]"
        return ""

    # ── tool call display ───────────────────────────────────────────────────
    def tool_call(self, name: str, args: dict) -> None:
        """Display a tool call with clean formatting (R-F1260)."""
        self._ensure_clear_line()
        self._tool_count += 1
        detail = self._summarize(name, args)
        prefix = self._step_prefix()

        bullet = self.c.green(self._g("bullet"))
        # R-F2804 — the COMMAND LINE is as sensitive as its output, and R-F2796
        # only covered the output. `curl -H "Authorization: Bearer sk-ant-…"`,
        # `flyctl secrets set BRAVE_SEARCH_API_KEY=…`, `export TOKEN=… && …` all
        # printed verbatim — and `_log` persists them to the session logfile,
        # which outlives the screenshot. `detail` is redacted for the same reason
        # (see _summarize: it reprs arbitrary tool args).
        if name == "run":
            cmd = _redact_secrets(args.get("command", ""))
            self._last_command = cmd[:200]
            step_tag = f"{self.c.dim(prefix)} " if prefix else ""
            print(f"{bullet} {step_tag}run({cmd[:200]})")
            self._log(f"[cmd] $ {cmd[:200]}")
        else:
            safe_detail = _redact_secrets(detail)
            step_tag = f"{self.c.dim(prefix)} " if prefix else ""
            print(f"{bullet} {step_tag}{name}({safe_detail})")
            self._log(f"[tool] {name}({safe_detail})")

    def _render_code_block(self, code: str, language: str = "") -> None:
        """Render a code block with syntax highlighting (R-F1267)."""
        if self._rich_console and code.strip():
            try:
                lang = language or _guess_language(code)
                syntax = RichSyntax(code, lang, theme="monokai" if self._theme_name == "dark" else "default",
                                    line_numbers=False, word_wrap=True)
                self._rich_console.print(syntax)
                return
            except Exception:
                pass
        # Fallback: dim text
        for ln in code.splitlines():
            print(self.c.dim(f"    {ln}"))

    def _branch_lines(self, lines: list[str], *, color=None, width: int = 200) -> None:
        """Render result lines under a tool call with a tree branch (R-F1354).

        First line:      ``  ⎿  {content}`` (branch glyph, dim)
        Following lines: ``     {content}`` (aligned beneath, dim)
        ``color`` is a _Color method applied to the CONTENT; the branch glyph
        and the leading indent are always dim. Empty ``lines`` prints nothing.
        """
        if not lines:
            return
        tint = color or self.c.dim
        print(self._branch_lead(tint(lines[0][:width])))
        for ln in lines[1:]:
            print(f"     {tint(ln[:width])}")

    def _branch_lead(self, content: str) -> str:
        """Return the lead result line: ``  ⎿  {content}`` (branch glyph dim).
        ``content`` is pre-colored by the caller."""
        branch = self.c.dim("  " + self._g("branch") + "  ")
        return f"{branch}{content}"

    def _trunc_hint(self, remaining: int) -> None:
        """Print the ``… +N lines`` truncation hint, aligned + dim (R-F1354)."""
        if remaining > 0:
            print(f"     {self.c.dim(self._g('ellipsis') + f' +{remaining} lines')}")

    def tool_result(self, name: str, result: ToolResult) -> None:
        """Display a tool result with Rich rendering (R-F1267, R-F1354)."""
        _hb_touch(force=True)  # R-F1310: every completed tool step = alive
        if result.is_error:
            self._error_count += 1
            lines = _sanitize_output(result.output).splitlines()  # R-F2093
            head = lines[0] if lines else ""
            # lead error line uses the branch glyph (dim) + red content.
            print(self._branch_lead(self.c.red(head[:200])))
            self._log(f"[error] {head[:200]}")
            for ln in lines[1:11]:
                print(f"     {self.c.dim(ln[:200])}")
            self._trunc_hint(len(lines) - 11 if len(lines) > 11 else 0)
            suggestion = self._error_suggestion(name, result.output)
            if suggestion:
                print(f"     {self.c.yellow(suggestion)}")
            return

        if name == "run":
            if result.output:
                output = _sanitize_output(result.output)  # R-F2093 — untrusted command output
                # Check if output looks like code (has indentation, brackets, etc.)
                if _looks_like_code(output):
                    # R-F1385 — CAP code-looking output. A pytest/build run that
                    # "looks like code" was rendered FULL-LENGTH (hundreds of
                    # lines — the "takes the entire screen" complaint). Show the
                    # head; the complete output is always in the session log.
                    _code_lines = output.splitlines()
                    if len(_code_lines) > 20:
                        self._render_code_block("\n".join(_code_lines[:20]))
                        self._trunc_hint(len(_code_lines) - 20)
                    else:
                        self._render_code_block(output)
                else:
                    lines = output.splitlines()
                    if len(lines) > 5:
                        self._branch_lines(lines[:3])
                        self._trunc_hint(len(lines) - 3)
                    else:
                        self._branch_lines(lines)
            exit_code = "0" if not result.is_error else "non-zero"
            self._log(f"[result] {len(result.output.splitlines()) if result.output else 0} lines, exit {exit_code}")

        if name in ("write_file", "edit_file") and result.mutation:
            self._file_changes += 1
            print(self._branch_lead(self.c.green(result.mutation)))
            self._log(f"[write] {result.mutation}")

        if name in {"update_plan", "ask_claude", "check_claude"}:
            self._branch_lines(_sanitize_output(result.output).splitlines())  # R-F2093

    def _error_suggestion(self, name: str, output: str) -> str:
        """Suggest recovery steps after common errors (R-F1260)."""
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
        """Print an informational message (R-F1389)."""
        self._ensure_clear_line()
        self._log(f"[info] {text}")
        print(self.c.dim(f"  · {text}"))

    # ── always-on activity indicator ────────────────────────────────────────
    def thinking_start(self, label: str = "thinking") -> None:
        """Start the activity spinner. Shows what ARIA is doing (R-F1260)."""
        self._streamed_this_turn = False
        if self._spin_thread is not None:
            return
        self._spin_label = label
        self._spin_start = time.monotonic()
        self._last_output = ""
        # R-F1385 — anchored mode: the bottom toolbar shows live activity; an
        # in-place spinner would print a new line per tick above the box.
        if self.anchored:
            return
        if self._can_animate:
            self._spin_stop = threading.Event()
            self._spin_thread = threading.Thread(target=self._spin, daemon=True)
            self._spin_thread.start()
        else:
            self._last_static = time.monotonic()
            print(self.c.dim(f"  ~ {label}..."), flush=True)

    def _spin(self) -> None:
        """Claude-Code-style spinner: ✦ {Gerund}… (elapsed · ↓ N tokens) (R-F1354).

        The redraw cadence, \\r\\033[K carriage-return clear, live-output tail and
        thread/stop logic are unchanged from R-F1260 — only the rendered line
        format and the gerund/token presentation are new.
        """
        last_tail = ""
        while self._spin_stop is not None and not self._spin_stop.is_set():
            elapsed = time.monotonic() - self._spin_start
            if self._last_output:
                t = self._last_output.strip()[:60]
                if t != last_tail:
                    last_tail = t
                tail = f"  {t}"
            elif self._last_command:
                tail = f"  {self._last_command[:60]}"
            else:
                tail = ""
            # Gerund rotates ~every 3s (deterministic by elapsed, not random).
            counter = int(elapsed // 3)
            tokens = None
            if self._token_getter is not None:
                try:
                    tk = self._token_getter()
                    if tk:
                        tokens = int(tk)
                except Exception:  # noqa: BLE001
                    tokens = None
            line = self._spin_line(counter, elapsed, tokens=tokens, tail=tail)
            sys.stdout.write("\r\033[K" + line[:160])
            sys.stdout.flush()
            tick = 0.08 if self._last_output else 0.15
            self._spin_stop.wait(tick)
        sys.stdout.write("\r\033[K")
        sys.stdout.flush()

    def thinking_stop(self) -> None:
        """Stop the activity spinner (R-F1260)."""
        if self._spin_thread is not None and self._spin_stop is not None:
            self._spin_stop.set()
            self._spin_thread.join(timeout=1.0)
            self._spin_thread = None
            self._spin_stop = None

    def tool_output(self, line: str) -> None:
        """Live output line from a running command (R-F1260)."""
        if not line:
            return
        line = _sanitize_output(line)  # R-F2093 — untrusted live command output
        self._last_output = line
        if not self._can_animate:
            now = time.monotonic()
            if now - self._last_static >= 2.0:
                self._last_static = now
                print(self.c.dim(f"    | {line[:80]}"), flush=True)

    # ── progress bar for long operations ────────────────────────────────────
    def progress_bar(self, current: int, total: int, label: str = "") -> None:
        """Show a progress bar: [===>>> ] 45% (R-F1260)."""
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
        # R-F1270: pause the stdin reader so it doesn't consume keystrokes
        # meant for input() during approval.
        _pause_stdin_reader()
        try:
            ans = input("  allow? [y]es / [n]o / [a]ll this session: ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            return False
        finally:
            _resume_stdin_reader()
        if ans in {"a", "all"}:
            self.approve_all = True
            return True
        return ans in {"y", "yes", ""}

    # ── R-F1194: operator message notification ──────────────────────────────
    def operator_message(self, text: str) -> None:
        """Notify that an operator message arrived mid-task (R-F1260, R-F1265).
        Prints on a new line without clearing existing output, so streaming
        content is not disrupted."""
        self._operator_messages += 1
        preview = text if len(text) <= 80 else text[:80] + "…"
        sys.stdout.write(f"\n{self.c.yellow(f'  [operator] {preview}')}\n")
        sys.stdout.flush()

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


# ── R-F1267: prompt_toolkit key bindings ──────────────────────────────────────
def _build_key_bindings():
    """Build key bindings for the REPL prompt (R-F1267, fixed R-F1279).
    - Enter: submit (the universal expectation — Claude-Code parity)
    - Alt+Enter / Esc+Enter: insert a newline for multi-line input
    - Ctrl+K: command palette (fuzzy search)

    R-F1279: the prompt is created with ``multiline=True`` so pasted/explicit
    newlines work, but with multiline on, prompt_toolkit's DEFAULT Enter inserts
    a newline instead of submitting. That made a plain Enter never hand the line
    to the agent — ARIA appeared to "not respond at all" and the completion menu
    popped up instead. We bind Enter to submit and move newline insertion to
    Alt+Enter so the REPL behaves the way every operator expects.
    """
    if not PROMPT_TOOLKIT_AVAILABLE:
        return None
    try:
        from prompt_toolkit.keys import Keys
        kb = KeyBindings()

        @kb.add("enter")
        def _(event):
            """Enter submits the current input.

            R-F1309 — self-heal: an exception escaping THIS handler becomes an
            unhandled event-loop exception that FREEZES the whole REPL ('Press
            ENTER to continue…' — the 2026-06-03 surrogate incident froze ARIA
            twice). The specific cause is fixed (R-F1308), but no input-handler
            failure may ever take the loop down again: catch everything, salvage
            the typed text into a sanitized submit, and keep the REPL alive."""
            try:
                event.current_buffer.validate_and_handle()
            except Exception:  # noqa: BLE001 — the REPL must survive any handler error
                try:
                    buf = event.current_buffer
                    text = _sanitize_input_line(buf.text or "")
                    buf.reset()
                    if text:
                        buf.insert_text(text)
                        buf.validate_and_handle()
                except Exception:  # noqa: BLE001 — last resort: drop the line, keep the loop
                    try:
                        event.current_buffer.reset()
                    except Exception:
                        pass

        @kb.add("escape", "enter")
        def _(event):
            """Alt+Enter (or Esc then Enter) inserts a newline for multi-line input."""
            event.current_buffer.insert_text("\n")

        @kb.add("c-k")
        def _(event):
            """Ctrl+K: show command palette (fuzzy search)."""
            from prompt_toolkit.application import run_in_terminal
            try:
                run_in_terminal(lambda: _show_command_palette(event.app))
            except Exception:  # noqa: BLE001 — R-F1309: never crash the loop
                pass

        return kb
    except Exception:
        return None


def _print_gaps_fallback(gaps, color, bx) -> None:
    """Print gaps in plain text (fallback when Rich is unavailable)."""
    print()
    print(color.cyan(f"  Gaps ({len(gaps)} found)"))
    print(color.dim(_sep_line(bx.h)))
    for i, g in enumerate(gaps[:20], 1):
        sev = g.get("severity", "UNKNOWN")
        sev_color = color.red if sev in ("CRITICAL",) else color.yellow if sev in ("HIGH",) else color.blue
        auto = "auto" if g.get("auto_fixable") else "manual"
        title = g.get("title", "?")[:50]
        print(sev_color(f"  {i:2d}  {title}  [{sev}]  {auto}"))
    print(color.dim(_sep_line(bx.h)))


def _print_cost_fallback(d, color, bx) -> None:
    """Print cost in plain text (fallback when Rich is unavailable)."""
    print()
    print(color.cyan("  Cost Dashboard"))
    print(color.dim(_sep_line(bx.h)))
    spend = d.get('monthly_spend', 0)
    cap = d.get('monthly_cap', 300)
    remaining = d.get('remaining', 0)
    print(f"  {color.dim('spend:')}     ${spend:.2f}")
    print(f"  {color.dim('cap:')}       ${cap:.2f}")
    print(f"  {color.dim('remaining:')} ${remaining:.2f}")
    pct = min(spend / max(cap, 1), 1.0)
    bar_len = int(pct * 20)
    bar = "█" * bar_len + "░" * (20 - bar_len)
    print(f"  {color.dim('usage:')}    {pct*100:.0f}%  {bar}")
    print(color.dim(_sep_line(bx.h)))


def _print_status_fallback(sm, src, inv, cost, color, bx) -> None:
    """Print status in plain text (fallback when Rich is unavailable)."""
    print()
    print(color.cyan("  System Status"))
    print(color.dim(_sep_line(bx.h)))
    comp = sm.get("composite", 0)
    comp_str = f"{comp*100:.0f}/100" if comp else "--"
    print(f"  {color.dim('composite:')}  {comp_str}")
    print(f"  {color.dim('sources:')}   {src.get('ok', '?')}/{src.get('total', '?')} OK")
    print(f"  {color.dim('facts:')}     {str(inv.get('knowledge_facts', '?'))}")
    print(f"  {color.dim('signals:')}   {str(inv.get('intel_signals', '?'))}")
    cost_str = f"${cost.get('monthly_usd', 0):.2f}" if cost.get('monthly_usd') else "--"
    print(f"  {color.dim('cost:')}     {cost_str}")
    print(color.dim(_sep_line(bx.h)))


def _print_stats_fallback(agent, ui, elapsed_str, color, bx) -> None:
    """Print stats in plain text (fallback when Rich is unavailable)."""
    print()
    print(color.cyan("  Session Statistics"))
    print(color.dim(_sep_line(bx.h)))
    print(f"  {color.dim('duration:')}  {elapsed_str}")
    print(f"  {color.dim('tools:')}    {ui._tool_count}")
    print(f"  {color.dim('errors:')}   {ui._error_count}")
    print(f"  {color.dim('files:')}    {ui._file_changes}")
    print(f"  {color.dim('operator:')} {ui._operator_messages}")
    print(f"  {color.dim('tokens:')}   {agent.llm.total_input_tokens + agent.llm.total_output_tokens} ({agent.llm.total_input_tokens} in / {agent.llm.total_output_tokens} out)")
    print(color.dim(_sep_line(bx.h)))


def _print_sessions_fallback(sessions, ui, color, bx) -> None:
    """Print sessions in plain text (fallback when Rich is unavailable)."""
    print()
    print(color.cyan(f"  Sessions ({len(sessions)})"))
    print(color.dim(_sep_line(bx.h)))
    for s in sessions[:20]:
        cur = ">" if ui.session_manager.current and s.id == ui.session_manager.current.id else " "
        name = s.name[:40]
        print(f"  [{cur}] {s.id[:16]}  {name}  {s.tool_count} tools")
    print(color.dim(_sep_line(bx.h)))
    print(color.dim("  /session load <id>  /session delete <id>"))


def _print_history_fallback(history: list, color, bx) -> None:
    """Print history in plain text (fallback when Rich is unavailable)."""
    print()
    print(color.cyan("  Composite Score History"))
    print(color.dim(_sep_line(bx.h)))
    for hist in history[:24]:
        ts = hist.get("timestamp", "")[:16]
        score = hist.get("composite", 0)
        bar_len = int(score * 20)
        bar = "█" * bar_len + "░" * (20 - bar_len)
        print(f"  {ts}  {score*100:5.1f}%  {bar}")
    print(color.dim(_sep_line(bx.h)))


def _print_plan_fallback(agent, color, bx) -> None:
    """Print plan in plain text (fallback when Rich is unavailable)."""
    print()
    print(color.cyan("  Current Plan"))
    print(color.dim(_sep_line(bx.h)))
    for p in agent.toolbox.plan:
        step = p.get("step", "")
        status = p.get("status", "pending")
        sym = {"completed": "✓", "in_progress": "~", "pending": " "}.get(status, " ")
        print(f"  [{sym}] {step[:52]}")
    print(color.dim(_sep_line(bx.h)))


def _show_command_palette(app) -> None:
    """Show a fuzzy-search command palette (R-F1267).
    Lists all available /commands and lets the user pick one."""
    commands = [
        ("/help", "Show this help message"),
        ("/exit", "Exit the session"),
        ("/quit", "Exit the session"),
        ("/confirm", "Toggle approval mode (auto/confirm)"),
        ("/changes", "Show changed files"),
        ("/chat", "Explain chat/interject mode"),
        ("/claude", "Check for messages from Claude"),
        ("/session", "Show current session info"),
        ("/sessions", "List saved sessions"),
        ("/export", "Export current session"),
        ("/theme", "Switch color theme"),
        ("/gaps", "Scan for capability gaps"),
        ("/status", "Show system status"),
        ("/history", "Show composite score history"),
        ("/cost", "Show cost dashboard"),
        ("/model", "Show current model info"),
        ("/compact", "Compact conversation history"),
        ("/memory", "Show memory file"),
        ("/diff", "Show uncommitted git diff"),
        ("/plan", "Show current plan"),
        ("/stats", "Show session statistics"),
        ("/think", "Show last assistant thinking"),
        ("/clear", "Clear the terminal"),
        ("/version", "Show version info"),
        ("/uptime", "Show session duration"),
        ("/config", "Show configuration"),
        ("/reset", "Reset conversation"),
    ]
    print()
    print("\033[36m  Command Palette\033[0m")
    print("\033[2m  " + "-" * 56 + "\033[0m")
    for cmd, desc in commands:
        print(f"  \033[36m{cmd:<12}\033[0m  {desc}")
    print("\033[2m  " + "-" * 56 + "\033[0m")
    print("  Type a command at the prompt, or Ctrl+K to show this again.")


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

    # R-F4303 — the override goes IN, so base_url/model/key are resolved for the
    # provider actually selected. Assigning cfg.provider afterwards left the
    # endpoint pointing at the previous provider.
    # R-F4370 (C-315) — from_env now FAILS CLOSED (no ARIA_LLM_URL, a withdrawn
    # provider, an endpoint-less one) rather than quietly selecting a vendor.
    # Catching it here is the delivery half of that: an uncaught LLMError would
    # meet the operator as a traceback, which reads as a broken CLI rather than
    # as the configuration message it is.
    try:
        cfg = LLMConfig.from_env(provider_override=args.provider)
    except LLMError as exc:
        print(color.red(f"  {exc}"))
        sys.exit(2)
    if args.model:
        cfg.model = args.model

    if not cfg.is_configured:
        print(color.red(
            f"No credential found for provider '{cfg.provider}'.\n"
            "  ARIA's own model needs no key — set ARIA_LLM_URL + ARIA_LLM_MODEL "
            "and leave ARIA_CODER_LLM_PROVIDER unset.\n"
            "  PowerShell:  $env:ARIA_LLM_URL = 'https://<pod>/v1'"))
        sys.exit(2)

    llm = LLMClient(cfg)
    # R-F2166: the `aria` provider can't do tool-calling — a coder on it silently
    # becomes a tool-less chat box. Make that LOUD so it's never a mystery.
    if not llm.supports_tools:
        # R-F4303 — name the provider actually in use. Hardcoding 'aria' here
        # would report the wrong one for `aria-llm`, and point at the wrong fix.
        _remedy = (
            "set ARIA_LLM_SUPPORTS_TOOLS=1 if the pod is served with a tool-call "
            "parser" if cfg.provider == "aria-llm" else
            "Set DEEPSEEK_API_KEY (or ARIA_CODER_LLM_PROVIDER to a tool-capable "
            "provider) to enable coding")
        print(color.red(
            f"  [warning] LLM provider '{cfg.provider}' does NOT support "
            f"tool-calling — the coder will have NO tools (no read/edit/run). "
            f"{_remedy}."))
    # R-F4325 (C-273) — a 7B-class sovereign cannot act under the full
    # prompt (measured 0/5 correct tool calls, and degenerate output).
    if compact_prompt_active(cfg.provider):
        system_prompt = build_compact_system_prompt(root=cwd)
    else:
        system_prompt = build_system_prompt(
            root=cwd, self_mode=self_mode, repo_root=repo_root)
    theme = getattr(args, "theme", "dark")
    ui = TerminalUI(auto_approve=args.auto, interactive=interactive, color=color, theme=theme)
    toolbox.on_output = ui.tool_output
    # R-F1354: feed the spinner a live token count (output tokens so far).
    ui._token_getter = lambda: llm.total_output_tokens
    agent = Agent(llm=llm, toolbox=toolbox, system_prompt=system_prompt, ui=ui,
                  auto_approve=args.auto, task_rag=self_mode)

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
    """Clean, compact banner — Claude Code style, no box-drawing (R-F1389).

    A colored title line with a subtle separator underneath. Clean, minimal,
    professional — the input box below provides the interactive structure.
    """
    mode = color.green("self") if self_mode else "general"
    brain = "wired" if brain_mod.brain_enabled(self_mode) else "off"
    approval = color.green("auto") if auto_approve else "confirm"
    bx = _BoxChars()
    # R-F1683: under a non-Unicode stdout (cp1252) use ASCII for the ellipsis and
    # the dot separator too — not just the box chars — so the banner emits only
    # encodable bytes (avoids UnicodeEncodeError on a real cp1252 terminal and the
    # capsys utf-8-capture choke in the test suite).
    ell = "…" if bx._unicode else "..."
    dot = "·" if bx._unicode else "-"
    dir_short = str(cwd)
    # Truncate path to a reasonable length
    if len(dir_short) > 60:
        dir_short = ell + dir_short[-59:]
    print()
    print(f"  {color.cyan('ARIA')} {color.dim('v' + __version__)}  {color.dim(dir_short)}")
    # R-F4335 (C-281) — the banner used to NAME a model without ever checking the
    # endpoint served it. Live 2026-08-25 it printed `aria-llm/aria-llm-v0.4-dpo`
    # while a name collision meant the untuned BASE model answered every request.
    # A label is a claim; this is the evidence for it.
    ident = session_model_identity(
        provider=cfg.provider, base_url=cfg.base_url, model=cfg.model,
        api_key=cfg.api_key, self_mode=self_mode)
    if ident is None:
        model_tag = f"{cfg.provider}/{cfg.model}"
    elif ident.healthy:
        model_tag = f"{cfg.provider}/{cfg.model} {color.green('verified')}"
    elif ident.is_breach:
        model_tag = f"{cfg.provider}/{cfg.model} {color.red('UNVERIFIED')}"
    else:
        # `unknown` is COULD NOT MEASURE and is never dressed as health (§1).
        model_tag = f"{cfg.provider}/{cfg.model} {color.dim('unverified')}"
    print(f"  {model_tag}  {color.dim(f'{dot}  {mode}  {dot}  brain:{brain}  {dot}  {approval}')}")
    print(f"  {color.dim(bx.h * 40)}")
    if ident is not None and ident.is_breach:
        # Loud, and it must stay loud: a warning the operator can miss is how a
        # dead fine-tune served for a whole session without anyone noticing.
        print(f"  {color.red('! MODEL NOT VERIFIED')} {color.dim('- ' + ident.detail)}")
        print(f"  {color.dim('  answers below come from an unintended model.')}")
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
    icon = bx.check if success else bx.cross
    print()
    print(f"  {color.cyan('Session Complete')}  {icon}")
    print(f"  {color.dim(bx.h * 40)}")
    print(f"  {color.dim('duration:')}  {elapsed_str}")
    print(f"  {color.dim('files:')}    {len(changed)} changed")
    print(f"  {color.dim('tools:')}    {ui._tool_count} calls")
    print(f"  {color.dim('errors:')}   {ui._error_count}")
    print(f"  {color.dim('operator:')} {ui._operator_messages} msgs")
    print(f"  {color.dim('tokens:')}   {total_tok} ({in_tok} in / {out_tok} out)")
    print(f"  {color.dim(bx.h * 40)}")

    if changed:
        print(color.dim(f"\n  files: {', '.join(changed)}"))

    status = brain_mod.report_session(
        task=task, success=success, changed_files=changed,
        summary=agent.messages[-1].get("content", "")[:600] if agent.messages else "",
        self_mode=self_mode)
    print(color.dim(f"  {status}"))

    # Log final summary
    ui._log(bx.h * 60)
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
    bx = _BoxChars()
    ui.start_session()
    _banner(color, cfg, self_mode, guard, cwd, auto_approve=agent.auto_approve)
    print(color.dim("  /help for commands  ·  type a message or task"))
    # R-F2053: only promise the live anchored box when this terminal can run it;
    # the basic-mode notice below tells the operator what they actually get.
    _box_capable, _ = _probe_prompt_toolkit_capability()
    if _box_capable:
        print(color.dim("  input box stays live while ARIA works — type + Enter to guide her mid-task\n"))
    else:
        print()
    last_task = ""
    _input_history: list[str] = []
    _history_idx = 0
    # R-F1310 — supervisor recovery: if aria-forever restarted us after a crash
    # or stall, announce it and run a recovery turn so ARIA RESUMES her work
    # instead of sitting at a blank prompt waiting for a human.
    _recovered = os.environ.pop("ARIA_RECOVERED", "").strip()
    if _recovered:
        print(color.yellow(f"  ♻ aria-forever restarted ARIA after: {_recovered}"))
        _busy_set(True)
        _hb_touch(force=True)
        try:
            agent.run_until_complete(
                "[SUPERVISOR RECOVERY] You were automatically restarted after: "
                f"{_recovered}. In-flight context may be lost. Do this now: "
                "1) check_claude for new bridge guidance; 2) re-open your task "
                "list (/gaps + the punch-list) and continue the task you were "
                "on; 3) if a write/commit/deploy was mid-flight, VERIFY its "
                "actual state (git status / git log / staged queue) before "
                "redoing anything. Then report what you resumed."
            )
        except Exception as _exc:  # noqa: BLE001 — recovery must not crash the REPL
            print(color.red(f"  recovery turn failed: {_exc}"))
        finally:
            _busy_set(False)
    # R-F1383 — ONE PromptSession for the whole session (was rebuilt per loop):
    # an anchored, Claude-Code-style input box that stays LIVE while ARIA works
    # (turns run in a worker thread; patch_stdout renders her output above the
    # box). Mid-task lines are visible/editable and go to _OPERATOR_QUEUE.
    pt_session = None
    _PT_SESSION = None  # R-F1407: reset module-level ref for bridge poller wake
    _LAST_SIGINT = [0.0]  # R-F1383 — double-Ctrl+C force-quit window
    # R-F2053: negotiate the input mode up front instead of constructing the box
    # and letting a console-less terminal crash the whole REPL on launch.
    _box_ok, _box_reason = _probe_prompt_toolkit_capability()
    if _box_ok:
        # R-F1279: Enter submits, Alt+Enter inserts a newline (see
        # _build_key_bindings). complete_while_typing=False so the
        # /command menu only appears on Tab — natural-language tasks
        # don't pop a menu on every keystroke.
        try:
            kb = _build_key_bindings()
            pt_session = PTPromptSession(
                # R-F1308: surrogate-safe — a pasted emoji crashed
                # the raw FileHistory write and froze the REPL.
                history=PTSafeFileHistory(str(_ensure_session_dir() / "repl_history.txt")),
                auto_suggest=PTAutoSuggest(),
                complete_while_typing=False,
                completer=PTWordCompleter([
                    "/help", "/exit", "/quit", "/confirm", "/changes", "/chat",
                    "/claude", "/session", "/sessions", "/export", "/theme",
                    "/gaps", "/status", "/history", "/cost", "/model", "/compact",
                    "/memory", "/diff", "/plan", "/stats", "/think", "/clear",
                    "/version", "/uptime", "/config", "/reset",
                ]),
                style=PTStyle.from_dict({
                    "prompt": "bold cyan" if color.on else "",
                    "bottom-toolbar": "noreverse bg:#1c1f2e #8ea0c8" if color.on else "",
                }),
                key_bindings=kb,
                multiline=True,
                bottom_toolbar=lambda: _toolbar_text(cfg, self_mode),
            )
            _PT_SESSION = pt_session  # R-F1407: module-level ref for bridge poller wake
        except Exception as _box_exc:  # noqa: BLE001 — probe passed but build failed
            # Belt-and-suspenders: never let box construction crash the REPL.
            pt_session = None
            _PT_SESSION = None
            _box_ok = False
            _box_reason = f"{type(_box_exc).__name__} while building the input box"
    if not _box_ok:
        # Deliberate, transparent downgrade — the CLI is fully usable in basic
        # line mode (type + Enter; mid-task typing still works via the stdin
        # reader, resumed below and in the loop).
        _resume_stdin_reader()
        print(color.dim(
            f"  input: basic line mode — {_box_reason}.\n"
            "  type + Enter works; mid-task typing supported. For the live input "
            "box, run aria in Windows Terminal or PowerShell."))

    # R-F1407: start the Claude bridge poller daemon (idle-wake for real-time notes)
    _CLAUDE_BRIDGE_BASE = find_repo_root(cwd)
    _CLAUDE_BRIDGE_EVENT.clear()
    _bridge_thread = threading.Thread(
        target=_claude_bridge_poller, daemon=True, name="claude-bridge-poller"
    )
    _bridge_thread.start()

    # R-F1426: self-checking prompt — uses prompt_async() with a concurrent
    # in-loop task that polls _OPERATOR_QUEUE every ~2s. When a Claude message
    # arrives, the poller calls app.exit(result=message) on the SAME event loop
    # (thread-safe by construction). The box renders ONCE — no re-render spam.
    # No cross-thread app.exit() needed; the bridge poller daemon just queues.

    async def _async_prompt_with_poller(
        session: Any,  # PTPromptSession
        box_width: int,
    ) -> str:
        """Run prompt_async() with a concurrent queue poller on the same loop.

        The box renders ONCE. A concurrent task polls _OPERATOR_QUEUE every
        ~2s. When a message is found, it calls app.exit(result=message) on
        the SAME event loop — thread-safe because it's the same loop, not a
        daemon thread. Returns the submitted line or the Claude message.
        """
        top = "─" * box_width
        prompt_tokens = [("", "\n"), ("class:prompt", top + "\n"), ("class:prompt", "❯ ")]
        app = getattr(session, "app", None)

        async def _queue_poller() -> None:
            """Poll _OPERATOR_QUEUE every ~2s and exit the prompt on a message.

            R-F1438: when busy, do NOT drain the queue — the worker thread is the
            only consumer. The poller's job is waking an IDLE prompt with a bridge
            message; mid-task the operator's typed line returns via prompt_async
            directly and is re-queued for the worker at line 2001.
            """
            while True:
                await asyncio.sleep(_PROMPT_POLL_INTERVAL_S)
                if _TURN_STATE["busy"]:
                    continue
                try:
                    msg = _OPERATOR_QUEUE.get_nowait()
                except Exception:
                    continue
                if app is not None:
                    # Same event loop — thread-safe by construction
                    app.exit(result=msg)
                return

        poller_task = asyncio.create_task(_queue_poller())
        try:
            line_ = await session.prompt_async(prompt_tokens, vi_mode=False)
            print(color.dim("─" * box_width))
            return line_
        except EOFError:
            raise
        except KeyboardInterrupt:
            raise
        finally:
            poller_task.cancel()
            try:
                await poller_task
            except asyncio.CancelledError:
                pass

    try:
        while True:
            try:
                # R-F1407: check for Claude bridge messages before blocking on prompt.
                # If a message arrived while we were processing the last turn, drain
                # it immediately without waiting for operator input.
                # R-F1438: when busy, the worker thread is the ONLY consumer of
                # _OPERATOR_QUEUE (via _drain_operator_stdin at each loop step).
                # The main loop must NOT steal messages meant for the worker —
                # doing so creates an infinite drain/re-queue loop at lines 1943+2001.
                _CLAUDE_BRIDGE_EVENT.clear()
                claude_line = None
                if not _TURN_STATE["busy"]:
                    try:
                        claude_line = _OPERATOR_QUEUE.get_nowait()
                    except Exception:
                        pass
                if claude_line is not None:
                    line = claude_line
                    # Skip the prompt entirely — process the Claude message as a turn
                else:
                    ui._ensure_clear_line()
                    # R-F1270: ALWAYS pause the stdin reader before reading REPL input,
                    # regardless of whether prompt_toolkit is available. This prevents
                    # the background thread from consuming keystrokes meant for input().
                    _pause_stdin_reader()
                    try:
                        # R-F1383: prompt stays open even while a turn runs —
                        # patch_stdout routes worker output above the box.
                        # R-F1426: prompt_async with a concurrent queue poller on
                        # the same event loop; the box renders ONCE.
                        def _box_prompt() -> str:
                            with PTPatchStdout(raw=True):
                                return _sanitize_input_line(
                                    asyncio.run(
                                        _async_prompt_with_poller(pt_session, _box_width())
                                    )
                                ).strip()

                        def _plain_prompt() -> str:
                            return _sanitize_input_line(
                                _read_operator_input(color.bold("  you > "))
                            ).strip()

                        def _on_box_fail(err: Exception) -> None:
                            print(color.dim(
                                f"  (input box unavailable: {type(err).__name__} — "
                                "switched to a basic prompt; type + Enter still works. "
                                "Relaunch to retry the box.)"
                            ))

                        # R-F1438: the box can never crash the REPL — on failure it
                        # latches and degrades to the plain input() prompt.
                        line = _read_line_robust(
                            _box_prompt if pt_session is not None else None,
                            _plain_prompt,
                            on_box_failure=_on_box_fail,
                        )
                    finally:
                        # The msvcrt reader provides mid-task interject on the
                        # plain-prompt path — resume it whenever the box isn't
                        # the active input (no prompt_toolkit, or box latched off).
                        if pt_session is None or _PT_PROMPT_BROKEN[0]:
                            _resume_stdin_reader()
            except EOFError:
                break
            except KeyboardInterrupt:
                # R-F1383 — Ctrl+C: graceful stop request while ARIA works;
                # twice within 5s force-quits. Idle: hint, never exit by accident.
                now_ = time.time()
                if _TURN_STATE["busy"]:
                    if now_ - _LAST_SIGINT[0] < 5:
                        print(color.yellow("  force-quit"))
                        break
                    _LAST_SIGINT[0] = now_
                    _OPERATOR_QUEUE.put(
                        "STOP — operator pressed Ctrl+C. Halt the current task at the "
                        "next safe point and summarise exactly where you stopped."
                    )
                    print(color.yellow("  ⏹ stop requested — ARIA halts at her next step. Ctrl+C again within 5s to force-quit."))
                    continue
                print(color.dim("  (Ctrl+C — type /exit to quit)"))
                continue
            if not line:
                continue
            # R-F1383 — a line submitted while ARIA is mid-task is operator
            # guidance: queue it (the agent drains _OPERATOR_QUEUE at the top
            # of each loop step, R-F1141) and keep the box live. Slash
            # commands still work mid-task EXCEPT new-task dispatch below.
            if _TURN_STATE["busy"] and not line.startswith("/"):
                _OPERATOR_QUEUE.put(line)
                print(color.yellow(f"  ▸ sent to ARIA mid-task — she'll act on it at her next step"))
                continue
            if _TURN_STATE["busy"] and line in {"/exit", "/quit"}:
                print(color.yellow("  ARIA is mid-task. /exit again after she finishes, or Ctrl+C twice to force."))
                continue
            if _TURN_STATE["busy"] and line in {"/reset", "/compact"}:
                # R-F1383 — these mutate agent.messages, which the worker thread
                # is appending to. Defer instead of racing.
                print(color.yellow(f"  {line} is deferred while ARIA works — run it when she finishes."))
                continue
            # R-F1258: track input history (skip duplicates)
            if not _input_history or _input_history[-1] != line:
                _input_history.append(line)
            _history_idx = len(_input_history)
            if line in {"/exit", "/quit"}:
                break
            if line == "/help":
                # R-F1267: Rich help with categories
                if ui._rich_console:
                    try:
                        from rich.table import Table as RichTable
                        from rich import box as RichBox
                        print()
                        tbl = RichTable(box=RichBox.ROUNDED, border_style="dim", show_header=False, padding=(0, 2))
                        tbl.add_column("Category", style="cyan", width=12)
                        tbl.add_column("Commands", style="dim")
                        tbl.add_row("Session", "/session /sessions /export /stats /uptime")
                        tbl.add_row("Control", "/confirm /reset /compact /clear /exit")
                        tbl.add_row("View", "/changes /diff /plan /think /memory")
                        tbl.add_row("System", "/status /gaps /history /cost /model /config /version")
                        tbl.add_row("Chat", "/chat /claude /theme")
                        ui._rich_console.print(tbl)
                        print(color.dim("  tip: type while ARIA works — she'll respond mid-task."))
                        print(color.dim("  tip: Ctrl+K for command palette · Alt+Enter for multi-line"))
                        continue
                    except Exception:
                        pass
                # Fallback: clean text
                print()
                print(color.cyan("  Commands"))
                print(color.dim(_sep_line(bx.h)))
                print(color.dim("  Session:  /session /sessions /export /stats /uptime"))
                print(color.dim("  Control:  /confirm /reset /compact /clear /exit"))
                print(color.dim("  View:     /changes /diff /plan /think /memory"))
                print(color.dim("  System:   /status /gaps /history /cost /model /config /version"))
                print(color.dim("  Chat:     /chat /claude /theme"))
                print(color.dim(_sep_line(bx.h)))
                print(color.dim("  tip: type while ARIA works — she'll respond mid-task."))
                print(color.dim("  tip: Ctrl+K for command palette · Alt+Enter for multi-line"))
                continue
            if line == "/chat":
                print(color.dim(
                    "  Chat mode: talk to ARIA while she's working.\n"
                    "  \n"
                    "  Just type a message at any time — even while ARIA is\n"
                    "  running a command or thinking. Your message is injected\n"
                    "  as high-priority guidance and ARIA responds on her next turn.\n"
                    "  \n"
                    "  Examples:\n"
                    '    "Stop what you\'re doing and check the logs"\n'
                    '    "Use a different approach — try grep instead"\n'
                    '    "What are you working on right now?"\n'
                    "  \n"
                    "  Your messages appear with a [operator] tag.\n"
                    "  ARIA acknowledges them and adjusts her plan accordingly."))
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
                if ui._rich_console:
                    try:
                        from rich.table import Table as RichTable
                        from rich import box as RichBox
                        print()
                        tbl = RichTable(box=RichBox.SIMPLE, border_style="dim", show_header=True, padding=(0, 1))
                        tbl.add_column("", width=1)
                        tbl.add_column("ID", style="dim", width=16)
                        tbl.add_column("Name", style="cyan", width=40)
                        tbl.add_column("Tools", style="dim", width=6)
                        for s in sessions[:20]:
                            cur = ">" if ui.session_manager.current and s.id == ui.session_manager.current.id else " "
                            tbl.add_row(cur, s.id[:16], s.name[:40], str(s.tool_count))
                        ui._rich_console.print(tbl)
                        print(color.dim("  /session load <id>  /session delete <id>"))
                    except Exception:
                        _print_sessions_fallback(sessions, ui, color, bx)
                else:
                    _print_sessions_fallback(sessions, ui, color, bx)
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
                    print(color.dim("  usage: /session load <id>  /session delete <id>"))
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
                        # R-F1383: snapshot — worker thread appends mid-task.
                        for m in list(agent.messages):
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
                print(color.dim("  /theme dark  /theme light  /theme claude"))
                continue
            if line.startswith("/theme "):
                t = line.split(maxsplit=1)[1].strip()
                if t in ("dark", "light", "claude"):
                    ui.set_theme(t)
                    print(color.green(f"  theme: {t}"))
                else:
                    print(color.red(f"  unknown theme: {t}"))
                continue
            if line in {"/confirm", "/auto", "/ask"}:
                ui.auto_approve = agent.auto_approve = not agent.auto_approve
                state = "autonomous (free rein)" if agent.auto_approve else "confirm each action"
                print(color.yellow(f"  approval: {state}"))
                continue
            if line == "/changes":
                ch = agent.toolbox.changed_files
                if not ch:
                    print(color.dim("  no files changed yet"))
                elif ui._rich_console:
                    try:
                        from rich.tree import Tree as RichTree
                        tree = RichTree("  [green]changed files[/green]", guide_style="dim")
                        for f in sorted(ch):
                            parts = f.split("/")
                            if len(parts) > 1:
                                tree.add(f"[dim]{'/'.join(parts[:-1])}/[/dim]{parts[-1]}")
                            else:
                                tree.add(f)
                        ui._rich_console.print(tree)
                    except Exception:
                        print(color.dim("  " + ", ".join(ch)))
                else:
                    print(color.dim("  " + ", ".join(ch)))
                continue
            if line == "/reset":
                agent.messages = agent.messages[:1]
                print(color.dim("  conversation reset"))
                continue
            if line in {"/gaps", "/scan", "/bugs"}:
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
                            if ui._rich_console:
                                try:
                                    from rich.table import Table as RichTable
                                    from rich import box as RichBox
                                    print()
                                    tbl = RichTable(box=RichBox.SIMPLE, border_style="dim", show_header=True, padding=(0, 1))
                                    tbl.add_column("#", style="dim", width=3)
                                    tbl.add_column("Title", style="cyan")
                                    tbl.add_column("Severity", width=10)
                                    tbl.add_column("Fix", width=6)
                                    for i, g in enumerate(gaps[:20], 1):
                                        sev = g.get("severity", "UNKNOWN")
                                        sev_style = "red" if sev in ("CRITICAL",) else "yellow" if sev in ("HIGH",) else "blue"
                                        auto = "auto" if g.get("auto_fixable") else "manual"
                                        title = g.get("title", "?")[:50]
                                        tbl.add_row(str(i), title, f"[{sev_style}]{sev}[/{sev_style}]", auto)
                                    ui._rich_console.print(tbl)
                                except Exception:
                                    _print_gaps_fallback(gaps, color, bx)
                            else:
                                _print_gaps_fallback(gaps, color, bx)
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
                        if ui._rich_console:
                            try:
                                from rich.table import Table as RichTable
                                from rich import box as RichBox
                                print()
                                tbl = RichTable(box=RichBox.SIMPLE, border_style="dim", show_header=False, padding=(0, 2))
                                tbl.add_column("Metric", style="dim", width=12)
                                tbl.add_column("Value", style="cyan")
                                comp = sm.get("composite", 0)
                                comp_str = f"{comp*100:.0f}/100" if comp else "--"
                                tbl.add_row("composite", comp_str)
                                tbl.add_row("sources", f"{src.get('ok', '?')}/{src.get('total', '?')} OK")
                                tbl.add_row("facts", str(inv.get('knowledge_facts', '?')))
                                tbl.add_row("signals", str(inv.get('intel_signals', '?')))
                                cost_str = f"${cost.get('monthly_usd', 0):.2f}" if cost.get('monthly_usd') else "--"
                                tbl.add_row("cost", cost_str)
                                ui._rich_console.print(tbl)
                            except Exception:
                                _print_status_fallback(sm, src, inv, cost, color, bx)
                        else:
                            _print_status_fallback(sm, src, inv, cost, color, bx)
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
                            if ui._rich_console:
                                try:
                                    from rich.table import Table as RichTable
                                    from rich import box as RichBox
                                    print()
                                    tbl = RichTable(box=RichBox.SIMPLE, border_style="dim", show_header=True, padding=(0, 1))
                                    tbl.add_column("Time", style="dim", width=16)
                                    tbl.add_column("Score", style="cyan", width=8)
                                    tbl.add_column("Bar", style="green", width=22)
                                    for hist in history[:24]:
                                        ts = hist.get("timestamp", "")[:16]
                                        score = hist.get("composite", 0)
                                        bar_len = int(score * 20)
                                        bar = "█" * bar_len + "░" * (20 - bar_len)
                                        tbl.add_row(ts, f"{score*100:5.1f}%", bar)
                                    ui._rich_console.print(tbl)
                                except Exception:
                                    _print_history_fallback(history, color, bx)
                            else:
                                _print_history_fallback(history, color, bx)
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
                        if ui._rich_console:
                            try:
                                from rich.table import Table as RichTable
                                from rich import box as RichBox
                                print()
                                tbl = RichTable(box=RichBox.SIMPLE, border_style="dim", show_header=False, padding=(0, 2))
                                tbl.add_column("Metric", style="dim", width=12)
                                tbl.add_column("Value", style="cyan")
                                spend = d.get('monthly_spend', 0)
                                cap = d.get('monthly_cap', 300)
                                remaining = d.get('remaining', 0)
                                tbl.add_row("spend", f"${spend:.2f}")
                                tbl.add_row("cap", f"${cap:.2f}")
                                tbl.add_row("remaining", f"${remaining:.2f}")
                                # Add a visual bar
                                pct = min(spend / max(cap, 1), 1.0)
                                bar_len = int(pct * 20)
                                bar = "█" * bar_len + "░" * (20 - bar_len)
                                tbl.add_row("usage", f"{pct*100:.0f}%  {bar}")
                                ui._rich_console.print(tbl)
                            except Exception:
                                _print_cost_fallback(d, color, bx)
                        else:
                            _print_cost_fallback(d, color, bx)
                    else:
                        print(color.dim(f"  brain returned {r.status_code}"))
                except Exception as e:
                    print(color.dim(f"  cost failed: {e}"))
                continue
            if line == "/model":
                # R-F1354: dynamic — read the REAL configured provider/model from
                # cfg instead of the old hardcoded "deepseek · anthropic". If the
                # provider/base_url points at the sovereign ARIA-LLM, show that.
                _prov = (getattr(cfg, "provider", "") or "").lower()
                _burl = (getattr(cfg, "base_url", "") or "").lower()
                _is_aria = (
                    _prov in {"aria", "aria-llm"}
                    or "aria-llm" in _burl
                    or "runpod" in _burl
                    or "/api/aria" in _burl
                )
                active = "aria-llm" if _is_aria else (_prov or "unknown")
                print(color.dim(f"  model: {getattr(cfg, 'model', 'auto') or 'auto'}"))
                print(color.dim(f"  chain: {active} (active)  ·  {getattr(cfg, 'base_url', '') or 'n/a'}"))
                continue
            if line == "/compact":
                # R-F2164: non-destructive compaction — stub the oldest bulky tool
                # outputs instead of deleting the whole session (the old
                # messages[:1]+[-5:] threw away all working context and could
                # orphan tool calls).
                before = sum(len(str(m.get("content") or "")) for m in agent.messages)
                reclaimed = agent._compact(force=True)
                after = sum(len(str(m.get("content") or "")) for m in agent.messages)
                print(color.dim(f"  compacted {before // 1000}k → {after // 1000}k chars "
                                f"(reclaimed ~{reclaimed // 1000}k, history preserved)"))
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
                        # R-F1267: Rich syntax-highlighted diff
                        if ui._rich_console:
                            try:
                                syntax = RichSyntax(r.output, "diff", theme="monokai" if ui._theme_name == "dark" else "default",
                                                    line_numbers=True, word_wrap=True)
                                ui._rich_console.print(syntax)
                            except Exception:
                                # Fallback to plain rendering
                                lines = r.output.splitlines()
                                for ln in lines[:40]:
                                    if ln.startswith("+"):
                                        print(color.green(f"  + {ln[1:200]}"))
                                    elif ln.startswith("-"):
                                        print(color.red(f"  - {ln[1:200]}"))
                                    else:
                                        print(color.dim(f"    {ln[:200]}"))
                                if len(lines) > 40:
                                    print(color.dim(f"    ... ({len(lines) - 40} more lines)"))
                        else:
                            lines = r.output.splitlines()
                            for ln in lines[:40]:
                                if ln.startswith("+"):
                                    print(color.green(f"  + {ln[1:200]}"))
                                elif ln.startswith("-"):
                                    print(color.red(f"  - {ln[1:200]}"))
                                else:
                                    print(color.dim(f"    {ln[:200]}"))
                            if len(lines) > 40:
                                print(color.dim(f"    ... ({len(lines) - 40} more lines)"))
                    else:
                        print(color.dim("  no uncommitted changes"))
                except Exception as e:
                    print(color.dim(f"  diff failed: {e}"))
                continue
            if line == "/plan":
                if agent.toolbox.plan:
                    if ui._rich_console:
                        try:
                            from rich.table import Table as RichTable
                            from rich import box as RichBox
                            print()
                            tbl = RichTable(box=RichBox.SIMPLE, border_style="dim", show_header=False, padding=(0, 1))
                            tbl.add_column("Status", width=3)
                            tbl.add_column("Step")
                            for p in agent.toolbox.plan:
                                step = p.get("step", "")
                                status = p.get("status", "pending")
                                sym = {"completed": "✓", "in_progress": "~", "pending": " "}.get(status, " ")
                                style = "green" if status == "completed" else "yellow" if status == "in_progress" else "dim"
                                tbl.add_row(f"[{style}]{sym}[/{style}]", f"[{style}]{step}[/{style}]")
                            ui._rich_console.print(tbl)
                        except Exception:
                            _print_plan_fallback(agent, color, bx)
                    else:
                        _print_plan_fallback(agent, color, bx)
                else:
                    print(color.dim("  no plan set (ARIA hasn't called update_plan yet)"))
                continue
            if line == "/stats":
                elapsed = time.time() - ui._session_start if ui._session_start else 0
                elapsed_str = f"{int(elapsed // 60)}m {int(elapsed % 60)}s" if elapsed >= 60 else f"{int(elapsed)}s"
                if ui._rich_console:
                    try:
                        from rich.table import Table as RichTable
                        from rich import box as RichBox
                        print()
                        tbl = RichTable(box=RichBox.SIMPLE, border_style="dim", show_header=False, padding=(0, 2))
                        tbl.add_column("Metric", style="dim", width=12)
                        tbl.add_column("Value", style="cyan")
                        tbl.add_row("duration", elapsed_str)
                        tbl.add_row("tools", str(ui._tool_count))
                        tbl.add_row("errors", str(ui._error_count))
                        tbl.add_row("files", str(ui._file_changes))
                        tbl.add_row("operator", str(ui._operator_messages))
                        total_tok = agent.llm.total_input_tokens + agent.llm.total_output_tokens
                        tbl.add_row("tokens", f"{total_tok} ({agent.llm.total_input_tokens} in / {agent.llm.total_output_tokens} out)")
                        ui._rich_console.print(tbl)
                    except Exception:
                        _print_stats_fallback(agent, ui, elapsed_str, color, bx)
                else:
                    _print_stats_fallback(agent, ui, elapsed_str, color, bx)
                continue
            if line == "/think":
                # R-F1383: snapshot — the worker thread appends to agent.messages
                # mid-task; iterating the live list can raise mid-iteration.
                for m in reversed(list(agent.messages)):
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
            # R-F2095 — a command-like token (/word) that matched no known command
            # above must NOT silently become an agent task (wastes tokens + confuses
            # the operator). Only a leading "/word" single token is treated as a
            # mistyped command; a path like /etc/hosts or a sentence still runs.
            _first_tok = line.split()[0] if line.split() else ""
            if _is_mistyped_command(_first_tok):
                print(color.yellow(f"  Unknown command: {_first_tok}  —  type /help for the list"))
                ui._log(f"[unknown-command] {_first_tok}")
                continue
            last_task = line
            ui._log(f"[user] {line}")
            # R-F1383 — threaded turn keeps the input box live for mid-task
            # guidance. Requires auto-approve (the default): in --confirm mode
            # the agent owns stdin for approval prompts, so run inline.
            if pt_session is not None and agent.auto_approve:
                _turn_worker(agent, line, color, ui)
                continue
            _busy_set(True)           # R-F1310: turn in flight — stall watch on
            _hb_touch(force=True)
            try:
                agent.run_until_complete(line)
            except KeyboardInterrupt:
                print("\n" + color.yellow("  ⏹  Cancelled — type a new task or /exit"))
                agent.messages = agent.messages[:-1] if agent.messages else agent.messages
                continue
            except Exception as exc:
                print("\n" + color.red(f"  ❌ Error: {exc}"))
                print(color.dim("  The LLM provider may be unavailable. Try again later."))
                agent.messages = agent.messages[:-1] if agent.messages else agent.messages
                continue
            finally:
                _busy_set(False)      # R-F1310: idle at prompt is never a stall
    except KeyboardInterrupt:
        print("\n" + color.dim("  interrupted"))
    finally:
        _finalize(agent, ui, cfg, self_mode, last_task or "(interactive session)",
                  success=True, color=color)


def main(argv: list[str] | None = None) -> int:
    """R-F2298 — robust entry point. Wraps the CLI so a failure in agent build,
    a one-shot task, an early Ctrl+C, or a broken output pipe exits CLEANLY with a
    friendly message instead of dumping a raw traceback on the operator. The
    interactive REPL has its own per-turn Ctrl+C handling; this guards everything
    OUTSIDE it (agent build, one-shot run, argparse). Full traceback goes to
    ~/.aria/sessions/crash.log so a crash is still debuggable."""
    try:
        return _run_cli(argv)
    except KeyboardInterrupt:
        try:
            sys.stderr.write("\nInterrupted.\n")
        except Exception:  # noqa: BLE001
            pass
        return 130
    except BrokenPipeError:
        # Downstream reader (e.g. `aria … | head`) closed the pipe — exit quietly
        # (0), not with a traceback. Reassign the Python-level stdout stream so any
        # final shutdown flush can't re-raise; deliberately NOT os.dup2 on the real
        # fd (that corrupts a captured/redirected stdout).
        try:
            sys.stdout = open(os.devnull, "w")
        except Exception:  # noqa: BLE001
            pass
        return 0
    except SystemExit:
        raise  # argparse --help/--version/bad-args: exit as intended
    except Exception as exc:  # noqa: BLE001 — top-level guard: never crash raw
        import traceback
        tb = traceback.format_exc()
        crash_path = None
        try:
            log_dir = Path.home() / ".aria" / "sessions"
            log_dir.mkdir(parents=True, exist_ok=True)
            crash_path = log_dir / "crash.log"
            with crash_path.open("a", encoding="utf-8") as _f:
                _f.write(f"\n[{time.strftime('%Y-%m-%d %H:%M:%S')}] aria {__version__} crashed:\n{tb}\n")
        except Exception:  # noqa: BLE001
            crash_path = None
        try:
            sys.stderr.write(f"\naria: {type(exc).__name__}: {exc}\n")
            sys.stderr.write(f"Full details logged to {crash_path}\n" if crash_path else tb)
        except Exception:  # noqa: BLE001
            pass
        return 1


def _run_cli(argv: list[str] | None = None) -> int:
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
    parser.add_argument("--provider", default="", help="LLM provider override (aria-llm is the default; openai, groq, ollama, ... also work). DeepSeek was removed by R-F4370.")
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

    # R-F1383 — professional terminal: module logging (aria_service imports in
    # self-mode emit MASTERY/absorb walls) goes to ~/.aria/sessions/cli.log;
    # only ERROR+ reaches the screen. Must run BEFORE _build_agent imports the
    # brain modules so their import-time logging is already routed.
    _quiet_console_logging()

    oneshot_text = args.oneshot or (" ".join(args.task).strip() if args.task else "")
    interactive = not oneshot_text

    agent, ui, cfg, self_mode, guard = _build_agent(cwd, args, color, interactive)

    # R-F1141: start the stdin reader daemon thread for operator mid-task
    # interject. R-F1383: only needed as the NO-prompt_toolkit fallback — with
    # prompt_toolkit the input box itself stays live mid-task; the reader
    # stays paused so it can't steal keystrokes from the box.
    if interactive:
        _start_stdin_reader()
        # R-F2053: pause the reader ONLY when the anchored box will actually run
        # (it owns stdin). In basic line mode the reader must stay live so
        # mid-task typing works — pausing it on mere import-availability left
        # console-less terminals with no input at all.
        box_ok, _ = _probe_prompt_toolkit_capability()
        if box_ok:
            _pause_stdin_reader()

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
