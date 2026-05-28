"""R-F988 — the tool set that gives the ARIA Coder CLI its Claude-Code-style
abilities: read, write, edit, list, glob, grep, and run shell commands, all
rooted at the directory the CLI was launched in.

Each tool returns a plain string observation (the model reads it back). Tools
never raise into the agent loop — failures are returned as error strings so the
model can recover, exactly like Claude Code's own tools.
"""
from __future__ import annotations

import fnmatch
import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from .safety import WriteGuard

# Keep observations bounded so a single tool call can't blow the context window.
_MAX_READ_BYTES = 100_000
_MAX_OUTPUT_CHARS = 30_000
_MAX_GREP_MATCHES = 200
_MAX_GLOB_RESULTS = 400


@dataclass
class ToolResult:
    output: str
    is_error: bool = False
    # A short, human-readable description of any mutation (for the approval UI
    # and the session change-log). Empty for read-only tools.
    mutation: str = ""


class Toolbox:
    """Filesystem + shell tools scoped to ``root`` (the launch directory)."""

    def __init__(self, root: Path, guard: WriteGuard,
                 bridge_base: Path | None = None) -> None:
        self.root = root.resolve()
        self.guard = guard
        self.changed_files: list[str] = []
        self.plan: list[dict] = []
        # Base dir for the Claude<->ARIA mailbox (the repo root in self-mode);
        # None disables the bridge tools.
        self.bridge_base = bridge_base

    # ── path helpers ──────────────────────────────────────────────────────
    def _resolve(self, path: str) -> Path:
        p = Path(path)
        if not p.is_absolute():
            p = self.root / p
        return p

    def _display(self, p: Path) -> str:
        # Normalise to forward slashes so paths read the same on every platform
        # and the model can re-reference them without backslash-escaping.
        try:
            return p.resolve().relative_to(self.root).as_posix()
        except ValueError:
            return str(p)

    # ── read_file ─────────────────────────────────────────────────────────
    def read_file(self, path: str, offset: int = 0, limit: int = 0) -> ToolResult:
        p = self._resolve(path)
        if not p.exists():
            return ToolResult(f"error: file not found: {path}", is_error=True)
        if p.is_dir():
            return ToolResult(f"error: {path} is a directory (use list_dir)", is_error=True)
        try:
            data = p.read_bytes()[:_MAX_READ_BYTES]
            text = data.decode("utf-8", errors="replace")
        except Exception as exc:  # noqa: BLE001
            return ToolResult(f"error reading {path}: {exc}", is_error=True)
        lines = text.splitlines()
        if offset or limit:
            start = max(0, offset)
            end = start + limit if limit else len(lines)
            lines = lines[start:end]
        else:
            start = 0
        numbered = "\n".join(f"{i + start + 1}\t{ln}" for i, ln in enumerate(lines))
        return ToolResult(numbered or "(empty file)")

    # ── write_file ────────────────────────────────────────────────────────
    def write_file(self, path: str, content: str) -> ToolResult:
        p = self._resolve(path)
        old = ""
        if p.exists():
            try:
                old = p.read_text(encoding="utf-8", errors="replace")
            except Exception:
                old = ""
        verdict = self.guard.review(str(p), old, content)
        if not verdict.allowed:
            return ToolResult(f"BLOCKED: {verdict.reason}", is_error=True)
        try:
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(content, encoding="utf-8")
        except Exception as exc:  # noqa: BLE001
            return ToolResult(f"error writing {path}: {exc}", is_error=True)
        self._track(p)
        warn = ("  [warn] " + "; ".join(verdict.warnings)) if verdict.warnings else ""
        verb = "created" if not old else "overwrote"
        return ToolResult(
            f"{verb} {self._display(p)} ({content.count(chr(10)) + 1} lines){warn}",
            mutation=f"{verb} {self._display(p)}",
        )

    # ── edit_file ─────────────────────────────────────────────────────────
    def edit_file(self, path: str, old_string: str, new_string: str,
                  replace_all: bool = False) -> ToolResult:
        p = self._resolve(path)
        if not p.exists():
            return ToolResult(f"error: file not found: {path}", is_error=True)
        try:
            content = p.read_text(encoding="utf-8")
        except Exception as exc:  # noqa: BLE001
            return ToolResult(f"error reading {path}: {exc}", is_error=True)
        count = content.count(old_string)
        if count == 0:
            return ToolResult(
                f"error: old_string not found in {path}. Read the file first and "
                f"match exactly (including whitespace).", is_error=True)
        if count > 1 and not replace_all:
            return ToolResult(
                f"error: old_string appears {count} times in {path}; make it "
                f"unique or pass replace_all=true.", is_error=True)
        new_content = content.replace(old_string, new_string)
        verdict = self.guard.review(str(p), content, new_content)
        if not verdict.allowed:
            return ToolResult(f"BLOCKED: {verdict.reason}", is_error=True)
        try:
            p.write_text(new_content, encoding="utf-8")
        except Exception as exc:  # noqa: BLE001
            return ToolResult(f"error writing {path}: {exc}", is_error=True)
        self._track(p)
        warn = ("  [warn] " + "; ".join(verdict.warnings)) if verdict.warnings else ""
        n = count if replace_all else 1
        return ToolResult(
            f"edited {self._display(p)} ({n} replacement{'s' if n != 1 else ''}){warn}",
            mutation=f"edited {self._display(p)}",
        )

    # ── list_dir ──────────────────────────────────────────────────────────
    def list_dir(self, path: str = ".") -> ToolResult:
        p = self._resolve(path)
        if not p.exists():
            return ToolResult(f"error: not found: {path}", is_error=True)
        if not p.is_dir():
            return ToolResult(f"error: not a directory: {path}", is_error=True)
        try:
            entries = sorted(p.iterdir(), key=lambda e: (e.is_file(), e.name.lower()))
        except Exception as exc:  # noqa: BLE001
            return ToolResult(f"error listing {path}: {exc}", is_error=True)
        lines = []
        for e in entries:
            if e.name in {".git", "__pycache__", "node_modules", ".venv"}:
                lines.append(f"{e.name}/  (skipped)")
                continue
            lines.append(f"{e.name}/" if e.is_dir() else e.name)
        return ToolResult("\n".join(lines) or "(empty directory)")

    # ── glob ──────────────────────────────────────────────────────────────
    def glob(self, pattern: str, path: str = ".") -> ToolResult:
        base = self._resolve(path)
        try:
            matches = sorted(m.relative_to(self.root).as_posix() for m in base.glob(pattern)
                             if ".git" not in m.parts and "node_modules" not in m.parts
                             and "__pycache__" not in m.parts and ".venv" not in m.parts)
        except Exception as exc:  # noqa: BLE001
            return ToolResult(f"error globbing {pattern}: {exc}", is_error=True)
        if not matches:
            return ToolResult(f"no files match {pattern}")
        truncated = len(matches) > _MAX_GLOB_RESULTS
        shown = matches[:_MAX_GLOB_RESULTS]
        out = "\n".join(shown)
        if truncated:
            out += f"\n... ({len(matches) - _MAX_GLOB_RESULTS} more)"
        return ToolResult(out)

    # ── grep ──────────────────────────────────────────────────────────────
    def grep(self, pattern: str, path: str = ".", glob: str = "") -> ToolResult:
        # Prefer ripgrep when available (fast, respects .gitignore); fall back
        # to a pure-Python walk so the tool works everywhere.
        rg = shutil.which("rg")
        base = self._resolve(path)
        if rg:
            cmd = [rg, "-n", "--no-heading", "-S", pattern, str(base)]
            if glob:
                cmd[1:1] = ["-g", glob]
            try:
                proc = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
                out = proc.stdout
            except Exception:  # noqa: BLE001 — fall through to python
                out = None
            if out is not None:
                lines = out.splitlines()[:_MAX_GREP_MATCHES]
                return ToolResult("\n".join(lines) or f"no matches for {pattern}")
        # Pure-python fallback
        try:
            rx = re.compile(pattern)
        except re.error as exc:
            return ToolResult(f"invalid regex: {exc}", is_error=True)
        results: list[str] = []
        for root, dirs, files in os.walk(base):
            dirs[:] = [d for d in dirs if d not in {".git", "node_modules", "__pycache__", ".venv"}]
            for fn in files:
                if glob and not fnmatch.fnmatch(fn, glob):
                    continue
                fp = Path(root) / fn
                try:
                    for i, line in enumerate(fp.read_text(encoding="utf-8", errors="ignore").splitlines(), 1):
                        if rx.search(line):
                            results.append(f"{fp.relative_to(self.root).as_posix()}:{i}:{line[:300]}")
                            if len(results) >= _MAX_GREP_MATCHES:
                                return ToolResult("\n".join(results) + "\n... (truncated)")
                except Exception:  # noqa: BLE001
                    continue
        return ToolResult("\n".join(results) or f"no matches for {pattern}")

    # ── run (shell) ───────────────────────────────────────────────────────
    def run(self, command: str, timeout: int = 300, cwd: str = "") -> ToolResult:
        workdir = self._resolve(cwd) if cwd else self.root
        # Use the platform shell — PowerShell on Windows, /bin/sh elsewhere —
        # so the model can use the same commands the operator would.
        try:
            if sys.platform == "win32":
                # Propagate the child command's real exit code: a bare
                # `powershell -Command` returns 0/1 for its own success, not the
                # exit code of the program it ran — which the model needs to know
                # whether tests/builds actually passed. $LASTEXITCODE is set by
                # the last native exe; it stays $null (→ exit 0) for pure cmdlets.
                wrapped = f"{command}\nif ($null -ne $LASTEXITCODE) {{ exit $LASTEXITCODE }}"
                full = ["powershell", "-NoProfile", "-NonInteractive", "-Command", wrapped]
                proc = subprocess.run(full, capture_output=True, text=True,
                                      timeout=timeout, cwd=str(workdir))
            else:
                proc = subprocess.run(command, shell=True, capture_output=True,
                                      text=True, timeout=timeout, cwd=str(workdir))
        except subprocess.TimeoutExpired:
            return ToolResult(f"error: command timed out after {timeout}s", is_error=True)
        except Exception as exc:  # noqa: BLE001
            return ToolResult(f"error running command: {exc}", is_error=True)
        out = (proc.stdout or "") + (("\n[stderr]\n" + proc.stderr) if proc.stderr else "")
        out = out.strip()
        if len(out) > _MAX_OUTPUT_CHARS:
            out = out[:_MAX_OUTPUT_CHARS] + f"\n... (truncated, {len(out)} chars total)"
        header = f"exit code: {proc.returncode}"
        return ToolResult(f"{header}\n{out}" if out else header,
                          is_error=proc.returncode != 0,
                          mutation=f"ran: {command[:80]}")

    # ── update_plan (multi-step planning, like a Claude Code todo list) ─────
    def update_plan(self, plan: list) -> ToolResult:
        if not isinstance(plan, list):
            return ToolResult("error: plan must be a list of steps", is_error=True)
        norm: list[dict] = []
        for item in plan:
            if isinstance(item, str):
                norm.append({"step": item, "status": "pending"})
            elif isinstance(item, dict):
                norm.append({
                    "step": str(item.get("step") or item.get("title") or "").strip(),
                    "status": str(item.get("status") or "pending").strip().lower(),
                })
        norm = [p for p in norm if p["step"]]
        if not norm:
            return ToolResult("error: plan has no steps", is_error=True)
        self.plan = norm
        return ToolResult(self.render_plan())

    def render_plan(self) -> str:
        sym = {"completed": "[x]", "in_progress": "[~]", "pending": "[ ]"}
        return "\n".join(f"{sym.get(p['status'], '[ ]')} {p['step']}" for p in self.plan)

    # ── fetch_url (read a web page / API, like Claude Code's WebFetch) ───────
    def fetch_url(self, url: str, max_chars: int = 10000) -> ToolResult:
        if not url.lower().startswith(("http://", "https://")):
            return ToolResult("error: url must start with http:// or https://", is_error=True)
        try:
            import httpx  # already a dependency; lazy import keeps startup light
            resp = httpx.get(url, timeout=25.0, follow_redirects=True,
                             headers={"User-Agent": "aria-coder-cli/0.1"})
        except Exception as exc:  # noqa: BLE001
            return ToolResult(f"error fetching {url}: {exc}", is_error=True)
        body = resp.text or ""
        cap = max(500, min(int(max_chars or 10000), _MAX_OUTPUT_CHARS))
        if len(body) > cap:
            body = body[:cap] + f"\n... (truncated, {len(resp.text)} chars total)"
        return ToolResult(f"HTTP {resp.status_code} {url}\n{body}",
                          is_error=resp.status_code >= 400)

    # ── Claude back-door (ask_claude / check_claude) ────────────────────────
    def ask_claude(self, question: str, wait_seconds: int = 0) -> ToolResult:
        if not self.bridge_base:
            return ToolResult("error: the Claude bridge is only available in "
                              "self-mode (inside the crucix repo).", is_error=True)
        from . import bridge
        msg = bridge.send(self.bridge_base, frm="aria", to="claude",
                          text=question, kind="question")
        wait = max(0, min(int(wait_seconds or 0), 120))
        if wait:
            reply = bridge.wait_for_reply(self.bridge_base, reader="aria",
                                          reply_to_id=msg["id"], timeout=wait)
            if reply:
                return ToolResult(f"Claude replied: {reply['text']}")
            return ToolResult(
                f"Question sent to Claude (id {msg['id']}), but no reply within "
                f"{wait}s — Claude may not be active right now. Continue with your "
                f"best judgement and call check_claude later for the answer.")
        return ToolResult(
            f"Question sent to Claude (id {msg['id']}). Claude answers when active "
            f"in this folder — call check_claude later to read the reply.")

    def check_claude(self) -> ToolResult:
        if not self.bridge_base:
            return ToolResult("error: the Claude bridge is only available in "
                              "self-mode (inside the crucix repo).", is_error=True)
        from . import bridge
        new = bridge.read_new(self.bridge_base, reader="aria")
        if not new:
            return ToolResult("No new messages from Claude.")
        lines = []
        for m in new:
            tag = "reply" if m.get("reply_to") else m.get("kind", "note")
            lines.append(f"[Claude {tag}] {m.get('text', '')}")
        return ToolResult("\n".join(lines))

    def _track(self, p: Path) -> None:
        d = self._display(p)
        if d not in self.changed_files:
            self.changed_files.append(d)


# ── OpenAI-shaped tool schemas advertised to the model ──────────────────────
TOOL_SCHEMAS: list[dict] = [
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "Read a text file. Returns content with 1-based line numbers. Use offset/limit for large files.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "File path, relative to the working directory or absolute."},
                    "offset": {"type": "integer", "description": "0-based line to start from (optional)."},
                    "limit": {"type": "integer", "description": "Max lines to read (optional)."},
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "write_file",
            "description": "Create a new file or fully overwrite an existing one. For edits to an existing file prefer edit_file. A truncation guard blocks overwrites that collapse a file to under half its size.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "content": {"type": "string", "description": "Full file content."},
                },
                "required": ["path", "content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "edit_file",
            "description": "Replace an exact string in a file. old_string must match exactly (including whitespace) and be unique unless replace_all is true.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "old_string": {"type": "string"},
                    "new_string": {"type": "string"},
                    "replace_all": {"type": "boolean", "description": "Replace every occurrence (default false)."},
                },
                "required": ["path", "old_string", "new_string"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_dir",
            "description": "List the entries of a directory (defaults to the working directory).",
            "parameters": {
                "type": "object",
                "properties": {"path": {"type": "string"}},
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "glob",
            "description": "Find files matching a glob pattern, e.g. '**/*.py'.",
            "parameters": {
                "type": "object",
                "properties": {
                    "pattern": {"type": "string"},
                    "path": {"type": "string", "description": "Base directory (optional)."},
                },
                "required": ["pattern"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "grep",
            "description": "Search file contents with a regular expression. Returns file:line:match. Uses ripgrep when available.",
            "parameters": {
                "type": "object",
                "properties": {
                    "pattern": {"type": "string"},
                    "path": {"type": "string"},
                    "glob": {"type": "string", "description": "Filename filter, e.g. '*.py' (optional)."},
                },
                "required": ["pattern"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "run",
            "description": "Run a shell command in the working directory (PowerShell on Windows, sh elsewhere). Use for running tests, git, builds, package managers — anything the operator could type. Returns exit code + combined stdout/stderr.",
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {"type": "string"},
                    "timeout": {"type": "integer", "description": "Seconds before kill (default 300; use 600+ for deploys)."},
                    "cwd": {"type": "string", "description": "Sub-directory to run in (optional)."},
                },
                "required": ["command"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "update_plan",
            "description": "Maintain a visible step-by-step plan for multi-step work (like a todo list). Call it at the start of a non-trivial task and update statuses as you go. Keep exactly one step 'in_progress'.",
            "parameters": {
                "type": "object",
                "properties": {
                    "plan": {
                        "type": "array",
                        "description": "Ordered steps.",
                        "items": {
                            "type": "object",
                            "properties": {
                                "step": {"type": "string"},
                                "status": {"type": "string", "enum": ["pending", "in_progress", "completed"]},
                            },
                            "required": ["step", "status"],
                        },
                    },
                },
                "required": ["plan"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "fetch_url",
            "description": "HTTP GET a URL and return its text (docs, an API response, a raw file). Read-only.",
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {"type": "string"},
                    "max_chars": {"type": "integer", "description": "Max chars to return (optional)."},
                },
                "required": ["url"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "ask_claude",
            "description": "Ask Claude Code (the operator's other coding agent working in this same repo) a question — e.g. for guidance on the north star, a design decision, a tricky bug, or a review. Claude answers asynchronously when active. Set wait_seconds to block briefly for a near-live reply.",
            "parameters": {
                "type": "object",
                "properties": {
                    "question": {"type": "string"},
                    "wait_seconds": {"type": "integer", "description": "Seconds to wait for a reply (0 = fire-and-forget; max 120)."},
                },
                "required": ["question"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "check_claude",
            "description": "Read any new messages or answers Claude Code has sent you in this repo.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
]

# Tools that mutate state (disk or system) — gated behind operator approval
# unless --auto is passed.
MUTATING_TOOLS = {"write_file", "edit_file", "run"}
