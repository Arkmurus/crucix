"""R-F988 — ``aria`` command-line entry point.

Run ``aria`` inside any project directory for an interactive coding session, or
``aria -p "task"`` / ``aria "task"`` for a one-shot run. ARIA detects whether
she is inside her own ecosystem (the crucix repo) and, if so, turns on the
constitutional guard and brain wiring; everywhere else she behaves as a general
Claude-Code-style coding agent.
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from . import __version__
from . import brain as brain_mod
from .agent import Agent, AgentUI
from .llm import LLMClient, LLMConfig
from .prompt import build_system_prompt
from .safety import WriteGuard
from .tools import ToolResult, Toolbox


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


class TerminalUI(AgentUI):
    def __init__(self, *, auto_approve: bool, interactive: bool, color: _Color) -> None:
        self.auto_approve = auto_approve
        self.interactive = interactive
        self.c = color
        self.approve_all = False

    def assistant(self, text: str) -> None:
        print("\n" + self.c.cyan("aria") + "  " + text)

    def tool_call(self, name: str, args: dict) -> None:
        detail = self._summarize(name, args)
        print(self.c.dim(f"  - {name}({detail})"))

    def tool_result(self, name: str, result: ToolResult) -> None:
        if result.is_error:
            head = result.output.splitlines()[0] if result.output else ""
            print(self.c.red(f"    -> {head[:200]}"))
            return
        if name in {"update_plan", "ask_claude", "check_claude"}:
            for ln in result.output.splitlines():
                print(self.c.dim(f"    {ln}"))

    def info(self, text: str) -> None:
        print(self.c.dim("  " + text))

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
        repo_root = cwd  # forced self-mode without detection

    # When editing ARIA's own ecosystem, make the repo importable so the
    # constitutional validator (which lives in aria_service) always loads — the
    # installed `aria` console script does not put the repo root on sys.path, so
    # without this the self-mode constitution would silently degrade to the
    # truncation guard alone.
    if repo_root is not None and str(repo_root) not in sys.path:
        sys.path.insert(0, str(repo_root))

    resolver = _repo_relative_resolver(repo_root) if repo_root else None
    guard = WriteGuard(self_mode=self_mode, repo_relative_resolver=resolver)
    # The Claude<->ARIA mailbox lives at the repo root, so the bridge tools are
    # available whenever ARIA is working in her own ecosystem.
    toolbox = Toolbox(root=cwd, guard=guard, bridge_base=repo_root)

    # In self-mode, feed the CLI from the repo's own .env (same file the server
    # reads) so the operator's DEEPSEEK_API_KEY is picked up automatically.
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
    agent = Agent(llm=llm, toolbox=toolbox, system_prompt=system_prompt, ui=ui,
                  auto_approve=args.auto)

    # Back door: surface any messages Claude left for ARIA into this session so
    # she sees them up front (CLAUDE.md §21 — agents working together).
    if repo_root is not None:
        try:
            from . import bridge
            pending = bridge.read_new(repo_root, reader="aria")
        except Exception:  # noqa: BLE001 — bridge must never block startup
            pending = []
        if pending:
            note = ("Claude Code (the operator's other agent in this repo) left "
                    "you the message(s) below. This IS Claude's guidance — treat "
                    "it as delivered and act on it; you do NOT need to call "
                    "check_claude for these (check_claude is only for replies that "
                    "arrive later in this session):\n"
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
    print(color.dim(
        f"  tokens:  in={agent.llm.total_input_tokens} out={agent.llm.total_output_tokens}"))
    agent.llm.close()


def _repl(agent: Agent, ui: TerminalUI, cfg: LLMConfig, self_mode: bool,
          guard: WriteGuard, cwd: Path, color: _Color) -> None:
    _banner(color, cfg, self_mode, guard, cwd, auto_approve=agent.auto_approve)
    print(color.dim("  Type a task. Commands: /confirm /changes /claude /reset /help /exit\n"))
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
            if line in {"/confirm", "/auto"}:
                # /confirm toggles the approval gate; /auto kept as an alias.
                ui.auto_approve = agent.auto_approve = not agent.auto_approve
                state = "autonomous (free rein)" if agent.auto_approve else "confirm each action"
                print(color.yellow(f"  approval: {state}"))
                continue
            if line == "/changes":
                ch = agent.toolbox.changed_files
                print(color.dim("  " + (", ".join(ch) if ch else "no files changed yet")))
                continue
            if line == "/reset":
                agent.messages = agent.messages[:1]  # keep system prompt
                print(color.dim("  conversation reset"))
                continue
            last_task = line
            result = agent.run_turn(line)
            # The reason for an aborted turn (step cap / LLM error) is already
            # surfaced via ui.info inside run_turn, so no opaque marker here.
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
    # Back-compat: --auto/--yolo used to enable autonomy; that is now the default,
    # so the flag is accepted but a no-op.
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

    # Full autonomy by default (free rein); --confirm opts back into prompts.
    args.auto = not args.confirm

    # The model's output (and ARIA's own glyphs) is UTF-8; the Windows console
    # is often cp1252, which raises UnicodeEncodeError on chars like arrows.
    # Reconfigure to UTF-8 with replacement so output never crashes the agent.
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:  # noqa: BLE001 — older/odd streams: best effort
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

    _banner(color, cfg, self_mode, guard, cwd, auto_approve=agent.auto_approve)
    print()
    result = agent.run_turn(oneshot_text)
    _finalize(agent, ui, cfg, self_mode, oneshot_text,
              success=not result.aborted, color=color)
    return 1 if result.aborted else 0


if __name__ == "__main__":
    sys.exit(main())
