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
import queue
import re
import shutil
import subprocess
import sys
import threading
import time
from collections import deque
from dataclasses import dataclass
from pathlib import Path

from .safety import WriteGuard

# Keep observations bounded so a single tool call can't blow the context window.
_MAX_READ_BYTES = 100_000
_MAX_OUTPUT_CHARS = 30_000
# R-F2095 — read_file paging. Files up to this size can be read/paged in full;
# bigger ones are refused with a pointer to offset/limit + grep (an OOM guard).
_HARD_READ_BYTES = 10_000_000
# Default line window per read_file call when the caller gives no explicit limit.
_DEFAULT_READ_LINES = 1500
_MAX_GREP_MATCHES = 200
_MAX_GLOB_RESULTS = 400

# Sentinel for non-Windows runs (no temp script to clean up).
_NO_TEMP_SCRIPT = ""

# R-F2163 — resolved PowerShell executable (cached). Prefer pwsh 7+ over the
# legacy powershell.exe 5.1.
_POWERSHELL_EXE: str | None = None


def _resolve_powershell() -> str:
    """Return the PowerShell executable to run `run` commands through. Prefers
    PowerShell 7+ (`pwsh`, the operator's primary shell) and falls back to
    Windows PowerShell 5.1 (`powershell`). Cached after first resolution.
    Override with ARIA_CODER_POWERSHELL."""
    global _POWERSHELL_EXE
    if _POWERSHELL_EXE is not None:
        return _POWERSHELL_EXE
    override = os.getenv("ARIA_CODER_POWERSHELL", "").strip()
    if override:
        _POWERSHELL_EXE = override
    elif shutil.which("pwsh"):
        _POWERSHELL_EXE = "pwsh"
    else:
        _POWERSHELL_EXE = "powershell"
    return _POWERSHELL_EXE


def _cleanup_temp_script(path: str) -> None:
    """Remove a temp .ps1 script created by run() on Windows."""
    if path and path != _NO_TEMP_SCRIPT:
        try:
            os.unlink(path)
        except Exception:  # noqa: BLE001 — best-effort cleanup
            pass


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
        # R-F1030: live-output callback — run() calls this for each output line so
        # the UI can show progress during long commands (never silent).
        self.on_output = None
        # R-F1298: background command registry (Claude-Code run_in_background
        # parity). Maps a bg id → state dict {proc, command, buffer, cursor,
        # lines_total, temp_script, lock}. Scoped to this Toolbox (session), so
        # processes are tied to the session that started them.
        self._bg: dict[str, dict] = {}
        self._bg_counter = 0
        self._bg_lock = threading.Lock()

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
        # R-F2095 — PAGED read. Was: read_bytes()[:100_000] then slice lines — which
        # (a) capped the RAW read at 100 KB so offset/limit could never reach past the
        # first ~2000 lines, and (b) truncated SILENTLY, so the agent got a partial
        # file thinking it was complete and could refactor on missing code. Now: page
        # by LINES over the whole file (bounded by a 10 MB OOM guard), and ALWAYS tell
        # the agent the window + total + how to read the rest.
        p = self._resolve(path)
        if not p.exists():
            return ToolResult(f"error: file not found: {path}", is_error=True)
        if p.is_dir():
            return ToolResult(f"error: {path} is a directory (use list_dir)", is_error=True)
        try:
            size = p.stat().st_size
        except Exception:  # noqa: BLE001
            size = 0
        if size > _HARD_READ_BYTES:
            return ToolResult(
                f"error: {path} is {size // 1024} KB — too large to read in full. "
                f"Page it with read_file(\"{path}\", offset=N, limit=M), or use grep/run "
                f"to inspect specific parts.", is_error=True)
        try:
            text = p.read_text(encoding="utf-8", errors="replace")
        except Exception as exc:  # noqa: BLE001
            return ToolResult(f"error reading {path}: {exc}", is_error=True)
        all_lines = text.splitlines()
        total = len(all_lines)
        start = max(0, offset)
        window = limit if (limit and limit > 0) else _DEFAULT_READ_LINES
        end = min(total, start + window)
        sel = all_lines[start:end]
        numbered = "\n".join(f"{i + start + 1}\t{ln}" for i, ln in enumerate(sel))
        if end < total:
            hint = (f"\n\n[read_file: showing lines {start + 1}-{end} of {total} — "
                    f"NOT the whole file. Continue with read_file(\"{path}\", offset={end}).]")
        elif start > 0:
            hint = f"\n\n[read_file: showing lines {start + 1}-{end} of {total} (end of file).]"
        else:
            hint = ""  # whole (small) file shown — no marker noise
        return ToolResult((numbered or "(empty file)") + hint)

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
    # Common ripgrep --type names → glob equivalents for the pure-python path.
    _TYPE_GLOBS = {
        "py": "*.py", "js": "*.js", "ts": "*.ts", "tsx": "*.tsx", "jsx": "*.jsx",
        "go": "*.go", "rust": "*.rs", "java": "*.java", "c": "*.c", "cpp": "*.cpp",
        "json": "*.json", "yaml": "*.yml", "yml": "*.yml", "md": "*.md",
        "html": "*.html", "css": "*.css", "sh": "*.sh", "toml": "*.toml",
    }

    def grep(self, pattern: str, path: str = ".", glob: str = "",
             type: str = "", output_mode: str = "content",
             case_insensitive: bool = False, context: int = 0,
             before: int = 0, after: int = 0, head_limit: int = 0) -> ToolResult:
        """Search file contents with a regex — Claude-Code-parity grep (R-F1297).

        output_mode: 'content' (file:line:match, default) | 'files_with_matches'
        (matching paths only) | 'count' (per-file match counts). context (-C) adds
        N lines on each side and overrides before/after (-B/-A). case_insensitive,
        glob ('*.py'), and type ('py','js',…) filter the search; head_limit caps
        the result-line count (0 → default 200). Prefers ripgrep, falls back to a
        pure-Python walk so it works everywhere."""
        base = self._resolve(path)
        limit = head_limit if head_limit and head_limit > 0 else _MAX_GREP_MATCHES
        a = context or after
        b = context or before
        mode = (output_mode or "content").strip().lower()
        if mode not in {"content", "files_with_matches", "count"}:
            mode = "content"

        rg = shutil.which("rg")
        if rg:
            cmd = [rg, "-S"]
            if case_insensitive:
                cmd.append("-i")
            if glob:
                cmd += ["-g", glob]
            if type:
                cmd += ["-t", type]
            if mode == "files_with_matches":
                cmd.append("-l")
            elif mode == "count":
                cmd.append("-c")
            else:  # content
                cmd += ["-n", "--no-heading"]
                if a:
                    cmd += ["-A", str(a)]
                if b:
                    cmd += ["-B", str(b)]
            cmd += [pattern, str(base)]
            try:
                proc = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
                out = proc.stdout
            except Exception:  # noqa: BLE001 — fall through to python
                out = None
            if out is not None:
                all_lines = out.splitlines()
                lines = all_lines[:limit]
                more = "" if len(all_lines) <= limit else f"\n... (truncated at {limit})"
                return ToolResult(("\n".join(lines) + more) or f"no matches for {pattern}")

        # Pure-python fallback (honours every option above).
        flags = re.IGNORECASE if case_insensitive else 0
        try:
            rx = re.compile(pattern, flags)
        except re.error as exc:
            return ToolResult(f"invalid regex: {exc}", is_error=True)
        name_filter = glob or self._TYPE_GLOBS.get((type or "").lower(), "")
        skip = {".git", "node_modules", "__pycache__", ".venv"}
        results: list[str] = []
        file_hits: dict[str, int] = {}
        for root, dirs, files in os.walk(base):
            dirs[:] = [d for d in dirs if d not in skip]
            for fn in files:
                if name_filter and not fnmatch.fnmatch(fn, name_filter):
                    continue
                fp = Path(root) / fn
                try:
                    text_lines = fp.read_text(encoding="utf-8", errors="ignore").splitlines()
                except Exception:  # noqa: BLE001
                    continue
                rel = fp.relative_to(self.root).as_posix()
                hits = [i for i, ln in enumerate(text_lines) if rx.search(ln)]
                if not hits:
                    continue
                if mode == "files_with_matches":
                    results.append(rel)
                elif mode == "count":
                    file_hits[rel] = len(hits)
                else:  # content (with optional context)
                    emitted: set[int] = set()
                    for i in hits:
                        for j in range(max(0, i - b), min(len(text_lines), i + a + 1)):
                            if j in emitted:
                                continue
                            emitted.add(j)
                            sep = ":" if j == i else "-"
                            results.append(f"{rel}:{j + 1}{sep}{text_lines[j][:300]}")
                if len(results) >= limit:
                    break
            if len(results) >= limit:
                break
        if mode == "count":
            lines = [f"{p}:{c}" for p, c in sorted(file_hits.items())][:limit]
            return ToolResult("\n".join(lines) or f"no matches for {pattern}")
        truncated = "\n... (truncated)" if len(results) > limit else ""
        return ToolResult("\n".join(results[:limit]) + truncated or f"no matches for {pattern}")

    # ── run (shell) ───────────────────────────────────────────────────────
    @staticmethod
    def _kill_tree(proc: "subprocess.Popen") -> None:
        """R-F1027 — kill a process AND all descendants, so an orphaned
        grandchild (e.g. a python spawned by powershell, or a hung pytest)
        cannot linger and wedge the agent. Windows: taskkill /T /F; POSIX:
        kill the process group."""
        try:
            if sys.platform == "win32":
                # R-F2095 — 15s→5s: bound how long a wedged taskkill can block the
                # kill path (on timeout the except below falls back to proc.kill()).
                subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                               capture_output=True, timeout=5)
            else:
                import os as _os
                import signal as _signal
                try:
                    _os.killpg(_os.getpgid(proc.pid), _signal.SIGKILL)
                except ProcessLookupError:
                    pass
        except Exception:  # noqa: BLE001 — last resort
            try:
                proc.kill()
            except Exception:
                pass

    def _spawn(self, command: str, workdir: Path) -> "tuple[subprocess.Popen, str]":
        """Launch a shell command, returning (Popen, temp_script_path). Shared by
        foreground run() and background runs (R-F1298) so the Windows quoting fix
        lives in one place. Uses the platform shell — PowerShell on Windows,
        /bin/sh elsewhere — and a process group so the whole tree can be killed.

        Windows (R-F1211): PowerShell -Command mangles double-quotes (e.g. -k
        "pattern"); write the command to a temp .ps1 and use -File, which reads
        the script literally. $LASTEXITCODE is propagated so the child's real exit
        code surfaces (it stays $null → exit 0 for pure cmdlets)."""
        if sys.platform == "win32":
            import tempfile
            tmp = tempfile.NamedTemporaryFile(
                mode="w", suffix=".ps1", delete=False, encoding="utf-8",
            )
            # R-F2163: force UTF-8 console output inside the script so child output
            # (emoji, box glyphs, accented text) survives the pipe instead of
            # raising UnicodeDecodeError in the reader thread (the pipe is decoded
            # as UTF-8 below). Idempotent and harmless for ASCII output.
            tmp.write("$OutputEncoding = [Console]::OutputEncoding = "
                      "[System.Text.Encoding]::UTF8\n")
            # R-F2368 — PowerShell call-operator for quoted-path commands. A .ps1
            # line that STARTS with a double-quoted string (e.g. the R-F2060
            # `"C:\...\python.exe" -m pytest`) is parsed as a STRING EXPRESSION,
            # so the next token (`-m`) raises "ParserError: Unexpected token '-m'
            # in expression or statement" and the command never runs — silently
            # breaking the coder's own test()/quoted-path run() self-verification
            # on Windows since R-F2060 (2026-06-27). To EXECUTE a quoted path,
            # PowerShell needs the call operator `&`. Prepend it ONLY when the
            # command leads with a quote (normal `git status` / `python -m x`
            # parse fine as commands and must stay untouched). Works in both
            # pwsh 7 and Windows PowerShell 5.1.
            _ps_command = command
            if _ps_command.lstrip().startswith('"'):
                _ps_command = "& " + _ps_command
            tmp.write(_ps_command)
            # R-F4342 (C-286) — $LASTEXITCODE IS ONLY SET BY NATIVE EXECUTABLES,
            # so the old `if ($null -ne $LASTEXITCODE) { exit $LASTEXITCODE }`
            # fell through to exit 0 for every FAILING cmdlet — including a
            # command that does not exist. Measured on this box:
            #     definitely-not-a-command-xyz  -> exit code: 0, is_error=False
            #     R1: pytest --version          -> exit code: 0, is_error=False
            # ARIA was told a command SUCCEEDED when PowerShell never found it.
            # A coding agent that cannot tell "not found" from "worked" cannot
            # verify anything — §3/§23 defeated at the tool layer, and the same
            # absence-reads-as-success shape §1 records three times over.
            #
            # $? DOES capture it (CommandNotFoundException is a non-terminating
            # error), and it MUST be read on the very next line: any statement in
            # between resets it, the $LASTEXITCODE assignment included.
            # A native exe still wins, so a real `pytest` exit 1 stays 1 and
            # never degrades into a generic 1-from-$?.
            #
            # Deliberately biased toward FALSE FAILURE over false success: a
            # spurious non-zero makes her look again; a spurious zero makes her
            # certify a fix that never ran.
            tmp.write("\n$__aria_ok = $?\n"
                      "$__aria_rc = $LASTEXITCODE\n"
                      "if ($null -ne $__aria_rc) { exit $__aria_rc }\n"
                      "if (-not $__aria_ok) { exit 1 }\n"
                      "exit 0\n")
            tmp.close()
            # R-F2163: prefer PowerShell 7+ (pwsh) — the operator's primary shell —
            # over Windows PowerShell 5.1 (powershell.exe), which lacks && / ||
            # chaining, ternary, and null-coalescing. Fall back to 5.1 if pwsh
            # isn't installed. Resolved once and cached.
            exe = _resolve_powershell()
            argv = [
                exe, "-NoProfile", "-NonInteractive",
                "-ExecutionPolicy", "Bypass", "-File", tmp.name,
            ]
            popen_kwargs = {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP}
            temp_script = tmp.name
        else:
            argv = command
            popen_kwargs = {"shell": True, "start_new_session": True}
            temp_script = _NO_TEMP_SCRIPT
        proc = subprocess.Popen(
            # R-F2163: decode the pipe as UTF-8 (errors=replace) rather than the
            # Windows locale default (cp1252), which silently corrupted/dropped
            # non-ASCII child output.
            argv, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, bufsize=1, encoding="utf-8", errors="replace",
            cwd=str(workdir), **popen_kwargs,
        )
        return proc, temp_script

    def run(self, command: str, timeout: int = 300, cwd: str = "",
            run_in_background: bool = False) -> ToolResult:
        workdir = self._resolve(cwd) if cwd else self.root
        # R-F1298: background mode — start the process, stream into a ring buffer,
        # and return immediately so the model can keep working (dev servers, long
        # builds/watchers). Read it later with read_output, stop it with
        # kill_command. Claude-Code run_in_background parity.
        if run_in_background:
            return self._start_background(command, workdir)
        # We use Popen (not subprocess.run) so that on timeout we can kill the
        # WHOLE process tree: subprocess.run's timeout only kills the direct child,
        # leaving orphaned grandchildren running (that orphaned-process pattern
        # wedged ARIA's loop).
        try:
            proc, _temp_script = self._spawn(command, workdir)
        except Exception as exc:  # noqa: BLE001
            return ToolResult(f"error running command: {exc}", is_error=True)

        # R-F1030: drain output on a reader thread and stream each line to the UI
        # live (on_output), so a long command (pytest, build, deploy) shows
        # progress instead of a silent cursor. The main loop enforces the timeout
        # and kills the whole tree if it's exceeded.
        q: "queue.Queue" = queue.Queue()

        def _reader():
            try:
                for line in proc.stdout:  # type: ignore[union-attr]
                    q.put(line)
            except Exception:  # noqa: BLE001
                pass
            finally:
                q.put(None)

        threading.Thread(target=_reader, daemon=True).start()

        captured: list[str] = []
        total = 0
        truncated = False
        deadline = time.monotonic() + timeout
        timed_out = False
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                timed_out = True
                break
            try:
                line = q.get(timeout=min(remaining, 0.5))
            except queue.Empty:
                continue  # lets us re-check the deadline (and keeps the UI ticking)
            if line is None:
                break  # process output closed → command finished
            if total < _MAX_OUTPUT_CHARS:
                captured.append(line)
                total += len(line)
            else:
                truncated = True
            if self.on_output is not None:
                try:
                    self.on_output(line.rstrip("\n"))
                except Exception:  # noqa: BLE001 — UI must never break the run
                    pass

        if timed_out:
            self._kill_tree(proc)
            out = "".join(captured).strip()
            tail = ("\n" + out[-2000:]) if out else ""
            _cleanup_temp_script(_temp_script)
            return ToolResult(
                f"error: command timed out after {timeout}s (process tree killed){tail}",
                is_error=True, mutation=f"ran (timeout): {command[:80]}")

        try:
            proc.wait(timeout=5)
        except Exception:  # noqa: BLE001
            self._kill_tree(proc)
        returncode = proc.returncode if proc.returncode is not None else -1

        _cleanup_temp_script(_temp_script)

        out = "".join(captured).strip()
        if truncated or len(out) > _MAX_OUTPUT_CHARS:
            out = out[:_MAX_OUTPUT_CHARS] + "\n... (truncated)"
        header = f"exit code: {returncode}"
        return ToolResult(f"{header}\n{out}" if out else header,
                          is_error=returncode != 0,
                          mutation=f"ran: {command[:80]}")

    # ── background commands (R-F1298 — run_in_background parity) ─────────────
    def _start_background(self, command: str, workdir: Path) -> ToolResult:
        """Start a command detached, draining its output into a bounded ring
        buffer on a reader thread. Returns a bg id the model uses with
        read_output / kill_command / list_background."""
        try:
            proc, temp_script = self._spawn(command, workdir)
        except Exception as exc:  # noqa: BLE001
            return ToolResult(f"error starting background command: {exc}", is_error=True)
        with self._bg_lock:
            self._bg_counter += 1
            bg_id = f"bg{self._bg_counter}"
        state: dict = {
            "proc": proc,
            "command": command,
            "buffer": deque(maxlen=2000),  # tail of recent output lines
            "cursor": 0,                    # absolute index already returned
            "lines_total": 0,               # absolute count ever produced
            "temp_script": temp_script,
            "lock": threading.Lock(),
        }

        def _reader() -> None:
            try:
                for line in proc.stdout:  # type: ignore[union-attr]
                    with state["lock"]:
                        state["buffer"].append(line.rstrip("\n"))
                        state["lines_total"] += 1
            except Exception:  # noqa: BLE001 — reader must never raise
                pass
            finally:
                _cleanup_temp_script(temp_script)

        threading.Thread(target=_reader, daemon=True).start()
        self._bg[bg_id] = state
        return ToolResult(
            f"started in background as {bg_id} (pid {proc.pid}): {command[:80]}\n"
            f"Use read_output('{bg_id}') for new output, kill_command('{bg_id}') to stop it.",
            mutation=f"started background {bg_id}: {command[:80]}")

    def read_output(self, bg_id: str, max_lines: int = 0) -> ToolResult:
        """Return output produced by a background command SINCE THE LAST read,
        plus its run/exit status. New output only — call repeatedly to follow a
        log. The ring buffer keeps the last ~2000 lines; older unread lines are
        reported as lost rather than silently dropped."""
        state = self._bg.get(bg_id)
        if state is None:
            return ToolResult(
                f"error: no background command '{bg_id}'. Use list_background.",
                is_error=True)
        proc = state["proc"]
        with state["lock"]:
            total = state["lines_total"]
            buffered = list(state["buffer"])
            first_abs = total - len(buffered)
            start = max(0, state["cursor"] - first_abs)
            new_lines = buffered[start:]
            dropped = max(0, first_abs - state["cursor"])
            state["cursor"] = total
        head = ""
        if max_lines and max_lines > 0 and len(new_lines) > max_lines:
            head = f"... ({len(new_lines) - max_lines} earlier new lines omitted)\n"
            new_lines = new_lines[-max_lines:]
        rc = proc.poll()
        status = "running" if rc is None else f"exited (code {rc})"
        drop_note = (f"[{dropped} line(s) lost to ring-buffer overflow]\n"
                     if dropped else "")
        body = "\n".join(new_lines)
        if not body:
            body = "(no new output)" if rc is None else "(no further output)"
        return ToolResult(f"[{bg_id}] {status}\n{drop_note}{head}{body}")

    def kill_command(self, bg_id: str) -> ToolResult:
        """Stop a background command (kills the whole process tree)."""
        state = self._bg.get(bg_id)
        if state is None:
            return ToolResult(f"error: no background command '{bg_id}'.", is_error=True)
        proc = state["proc"]
        if proc.poll() is not None:
            return ToolResult(f"[{bg_id}] already exited (code {proc.returncode}).")
        self._kill_tree(proc)
        _cleanup_temp_script(state.get("temp_script", ""))
        return ToolResult(f"[{bg_id}] killed.", mutation=f"killed background {bg_id}")

    def list_background(self) -> ToolResult:
        """List background commands started this session and their status."""
        if not self._bg:
            return ToolResult("no background commands.")
        lines = []
        for bg_id, state in self._bg.items():
            rc = state["proc"].poll()
            status = "running" if rc is None else f"exited({rc})"
            lines.append(f"{bg_id}: {status}  {state['command'][:70]}")
        return ToolResult("\n".join(lines))

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
        # If httpx returned a thin JS shell (short body, no meaningful text),
        # try Playwright for JS-rendered content
        if resp.status_code == 200 and len(body.strip()) < 2000:
            from .playwright_fetch import fetch_with_playwright
            pw_text = fetch_with_playwright(url)
            if pw_text is not None:
                body = pw_text
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
            "description": "Search file contents with a regular expression. Uses ripgrep when available. output_mode 'content' returns file:line:match (with optional context lines), 'files_with_matches' returns matching paths, 'count' returns per-file match counts.",
            "parameters": {
                "type": "object",
                "properties": {
                    "pattern": {"type": "string"},
                    "path": {"type": "string", "description": "File or directory to search (optional)."},
                    "glob": {"type": "string", "description": "Filename filter, e.g. '*.py' (optional)."},
                    "type": {"type": "string", "description": "File-type filter, e.g. 'py', 'js', 'json' (optional)."},
                    "output_mode": {"type": "string", "enum": ["content", "files_with_matches", "count"], "description": "Result shape (default 'content')."},
                    "case_insensitive": {"type": "boolean", "description": "Case-insensitive match (default false)."},
                    "context": {"type": "integer", "description": "Lines of context on each side of a match (content mode); overrides before/after."},
                    "before": {"type": "integer", "description": "Lines of context before each match (content mode)."},
                    "after": {"type": "integer", "description": "Lines of context after each match (content mode)."},
                    "head_limit": {"type": "integer", "description": "Cap the number of result lines (default 200)."},
                },
                "required": ["pattern"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "run",
            "description": "Run a shell command in the working directory (PowerShell on Windows, sh elsewhere). Use for running tests, git, builds, package managers — anything the operator could type. Returns exit code + combined stdout/stderr. Set run_in_background=true for long-lived processes (dev servers, watchers): it returns a bg id immediately and you read its output later with read_output.",
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {"type": "string"},
                    "timeout": {"type": "integer", "description": "Seconds before kill (default 300; use 600+ for deploys). Ignored when run_in_background is true."},
                    "cwd": {"type": "string", "description": "Sub-directory to run in (optional)."},
                    "run_in_background": {"type": "boolean", "description": "Start detached and return immediately with a bg id (default false). Use for servers/watchers/long tasks you want to keep working alongside."},
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
        # R-F2368 — restore fetch_url to the general toolbox schema. The handler
        # (Toolbox.fetch_url, ~line 660) and the CLI dispatch (cli.py:1608) both
        # exist, but the schema was dropped (stale "already in coder_tools" note) —
        # so the general (non-coder) agent had a LIVE handler it could never call.
        # coder_tools already advertises it; this restores parity so both modes can
        # pull docs / API responses / raw files.
        "type": "function",
        "function": {
            "name": "fetch_url",
            "description": "HTTP GET a URL and return its text (docs, an API response, a raw file). Falls back to Playwright JS rendering for SPAs. Read-only.",
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
            "name": "read_output",
            "description": "Read NEW output from a background command (started via run with run_in_background=true) since your last read, plus its run/exit status. Call repeatedly to follow a server log or wait for a build to finish.",
            "parameters": {
                "type": "object",
                "properties": {
                    "bg_id": {"type": "string", "description": "The background id returned by run, e.g. 'bg1'."},
                    "max_lines": {"type": "integer", "description": "Cap the new lines returned (0 = all new lines)."},
                },
                "required": ["bg_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "kill_command",
            "description": "Stop a background command (kills the whole process tree). Use to shut down a dev server you started.",
            "parameters": {
                "type": "object",
                "properties": {
                    "bg_id": {"type": "string", "description": "The background id, e.g. 'bg1'."},
                },
                "required": ["bg_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_background",
            "description": "List background commands started this session and whether each is still running.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    # fetch_url removed here because it's already defined in coder_tools.py
    # to avoid "Tool names must be unique" error with DeepSeek API
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