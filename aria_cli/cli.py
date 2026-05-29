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
"""
from __future__ import annotations

import argparse
import os
import sys
import threading
import time
from pathlib import Path

from . import __version__
from . import brain as brain_mod
from .agent import Agent, AgentUI
from .llm import LLMClient, LLMConfig
from .prompt import build_system_prompt
from .safety import WriteGuard
from .tools import ToolResult, Toolbox


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


class TerminalUI(AgentUI):
    """Claude-Code-parity terminal UI.

    R-F1045 features:
      - Step counter: "Step 2/5" during multi-tool turns
      - Command echo: "$ pytest -v" before running
      - Live output: streaming with truncation preview
      - Diff preview: shows +/- lines before writes
      - Progress bar: [===>>> ] for long operations
      - Error recovery: suggests next steps after errors
      - Thinking trace: shows what ARIA is doing
      - Session log: persistent log file
    """

    def __init__(self, *, auto_approve: bool, interactive: bool, color: _Color) -> None:
        self.auto_approve = auto_approve
        self.interactive = interactive
        self.c = color
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

    def start_session(self) -> None:
        """Called at session start to create the log file."""
        self._session_log = _session_log_path()
        _append_log(self._session_log, f"ARIA Coder v{__version__}")
        _append_log(self._session_log, f"Started: {time.strftime('%Y-%m-%d %H:%M:%S')}")
        _append_log(self._session_log, f"Directory: {Path.cwd()}")
        _append_log(self._session_log, "─" * 60)

    def _log(self, text: str) -> None:
        if self._session_log:
            _append_log(self._session_log, text)

    def assistant(self, text: str) -> None:
        self._log(f"[aria] {text[:200]}")
        print("\n" + self.c.cyan("aria") + "  " + text)

    # ── live token streaming (never silent) ──────────────────────────────────
    def stream_delta(self, text: str) -> None:
        if not self._stream_active:
            self.thinking_stop()
            sys.stdout.write("\n" + self.c.cyan("aria") + "  ")
            self._stream_active = True
            self._streamed_this_turn = True
        sys.stdout.write(text)
        sys.stdout.flush()

    def stream_end(self) -> None:
        if self._stream_active:
            sys.stdout.write("\n")
            sys.stdout.flush()
            self._stream_active = False

    # ── step tracking (Claude-Code parity) ──────────────────────────────────
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
        detail = self._summarize(name, args)
        prefix = self._step_prefix()
        if prefix:
            line = f"  {prefix} {name}({detail})"
        else:
            line = f"  - {name}({detail})"

        # Command echo for run() — show the actual command
        if name == "run":
            cmd = args.get("command", "")
            self._last_command = cmd[:200]
            print(self.c.dim(f"  $ {cmd[:200]}"))
            self._log(f"[cmd] $ {cmd[:200]}")
        else:
            print(self.c.dim(line))
            self._log(f"[tool] {name}({detail})")

    def tool_result(self, name: str, result: ToolResult) -> None:
        if result.is_error:
            head = result.output.splitlines()[0] if result.output else ""
            print(self.c.red(f"    -> {head[:200]}"))
            self._log(f"[error] {head[:200]}")

            # R-F1045: error recovery suggestion
            suggestion = self._error_suggestion(name, result.output)
            if suggestion:
                print(self.c.yellow(f"    {suggestion}"))
            return

        # Show result preview for long outputs
        if name == "run" and result.output:
            lines = result.output.splitlines()
            if len(lines) > 5:
                preview = "\n".join(lines[:3])
                tail = f"... ({len(lines) - 3} more lines)"
                print(self.c.dim(f"    {preview}"))
                print(self.c.dim(f"    {tail}"))
                self._log(f"[result] {len(lines)} lines, exit 0")
            else:
                for ln in lines:
                    print(self.c.dim(f"    {ln}"))
                self._log(f"[result] {len(lines)} lines, exit 0")

        # Show diff preview for write_file / edit_file
        if name in ("write_file", "edit_file") and result.mutation:
            print(self.c.green(f"    {result.mutation}"))
            self._log(f"[write] {result.mutation}")

        if name in {"update_plan", "ask_claude", "check_claude"}:
            for ln in result.output.splitlines():
                print(self.c.dim(f"    {ln}"))

    def _error_suggestion(self, name: str, output: str) -> str:
        """Suggest recovery steps after common errors."""
        out_lower = output.lower()
        if "module not found" in out_lower or "import error" in out_lower or "no module" in out_lower:
            return "💡 Try: pip install <missing-package> or check import paths"
        if "syntax error" in out_lower or "invalid syntax" in out_lower:
            return "💡 Try: check the file for syntax issues and fix them"
        if "timeout" in out_lower or "timed out" in out_lower:
            return "💡 Try: increase timeout or check network connectivity"
        if "connection" in out_lower or "refused" in out_lower or "reset" in out_lower:
            return "💡 Try: check the service is running and reachable"
        if "permission" in out_lower or "denied" in out_lower:
            return "💡 Try: check file permissions or run with appropriate privileges"
        if "not found" in out_lower and "command" in out_lower:
            return "💡 Try: install the required tool or check PATH"
        if "assert" in out_lower and "error" in out_lower:
            return "💡 Try: check the assertion condition — the test expectation may need updating"
        return ""

    def info(self, text: str) -> None:
        self._log(f"[info] {text}")
        print(self.c.dim("  " + text))

    # ── always-on activity indicator ────────────────────────────────────────
    def thinking_start(self, label: str = "thinking") -> None:
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
            print(self.c.dim(f"  * {label}..."), flush=True)

    def _spin(self) -> None:
        frames = "|/-\\"
        i = 0
        while self._spin_stop is not None and not self._spin_stop.is_set():
            elapsed = int(time.monotonic() - self._spin_start)
            # Show what ARIA is doing, not just a spinner
            if self._last_output:
                tail = f"  {self._last_output[:54]}"
            elif self._last_command:
                tail = f"  {self._last_command[:54]}"
            else:
                tail = ""
            prefix = self._step_prefix()
            if prefix:
                label = f"{prefix} {self._spin_label}"
            else:
                label = self._spin_label
            line = f"  {frames[i % 4]} {label} ({elapsed}s){tail}"
            sys.stdout.write("\r\033[K" + self.c.dim(line[:120]))
            sys.stdout.flush()
            i += 1
            self._spin_stop.wait(0.2)
        sys.stdout.write("\r\033[K")
        sys.stdout.flush()

    def thinking_stop(self) -> None:
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
        """Show a progress bar like Claude Code: [===>>> ] 45%."""
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
        root=cwd, self_mode=self_mode, constitution_active=guard.constitution_active,
        repo_root=repo_root)
    ui = TerminalUI(auto_approve=args.auto, interactive=interactive, color=color)
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
    guard_state = "constitution + truncation" if guard.constitution_active else "truncation only"
    approval = color.green("autonomous (free rein)") if auto_approve else "confirm each action"
    print(color.bold("ARIA Coder") + color.dim(f" v{__version__}"))
    print(color.dim(f"  dir:      {cwd}"))
    print(color.dim(f"  mode:     {mode}    guard: {guard_state}    brain: {brain}"))
    print(color.dim(f"  model:    {cfg.provider}/{cfg.model}    approval: ") + approval)


def _finalize(agent: Agent, ui: TerminalUI, cfg: LLMConfig, self_mode: bool,
              task: str, success: bool, color: _Color) -> None:
    changed = agent.toolbox.changed_files
    if changed:
        print(color.dim("\n  changed: " + ", ".join(changed)))
    status = brain_mod.report_session(
        task=task, success=success, changed_files=changed,
        summary=agent.messages[-1].get("content", "")[:600] if agent.messages else "",
        self_mode=self_mode)
    print(color.dim(f"  {status}"))

    # R-F1045: session summary
    in_tok = agent.llm.total_input_tokens
    out_tok = agent.llm.total_output_tokens
    total_tok = in_tok + out_tok
    print(color.dim(f"  tokens:  in={in_tok} out={out_tok} total={total_tok}"))

    # Log final summary
    ui._log(f"─" * 60)
    ui._log(f"Completed: {time.strftime('%Y-%m-%d %H:%M:%S')}")
    ui._log(f"Success: {success}")
    ui._log(f"Files changed: {len(changed)}")
    ui._log(f"Tokens: {total_tok}")
    if ui._session_log:
        print(color.dim(f"  session:  {ui._session_log}"))

    agent.llm.close()


def _repl(agent: Agent, ui: TerminalUI, cfg: LLMConfig, self_mode: bool,
          guard: WriteGuard, cwd: Path, color: _Color) -> None:
    ui.start_session()
    _banner(color, cfg, self_mode, guard, cwd, auto_approve=agent.auto_approve)
    print(color.dim("  Commands: /confirm /changes /claude /session /reset /help /exit\n"))
    last_task = ""
    try:
        while True:
            try:
                line = input(color.bold("you> ")).strip()
            except EOFError:
                break
            if not line:
                continue
            if line in {"/exit", "/quit"}:
                break
            if line == "/help":
                print(color.dim(
                    "  /confirm — toggle asking before edits/commands (default: autonomous)\n"
                    "  /changes — list files changed this session\n"
                    "  /claude — read new messages from Claude Code\n"
                    "  /session — show session log path\n"
                    "  /reset — clear the conversation history\n"
                    "  /exit — quit"))
                continue
            if line == "/claude":
                try:
                    from . import bridge
                    msgs = bridge.read_new(cwd, reader="aria") if find_repo_root(cwd) else []
                except Exception:  # noqa: BLE001
                    msgs = []
                if msgs:
                    for m in msgs:
                        print(color.cyan(f"  [Claude] {m.get('text', '')}"))
                else:
                    print(color.dim("  no new messages from Claude"))
                continue
            if line == "/session":
                if ui._session_log:
                    print(color.dim(f"  session log: {ui._session_log}"))
                else:
                    print(color.dim("  no session log (session not started)"))
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
