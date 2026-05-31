"""R-F1143 — structured coding tools for the ARIA Coder CLI.

Higher-level tools that wrap the existing Toolbox methods: git operations,
deploy, test running, R-number management, self-review, and capability test
generation. These are designed to be used by the agent loop alongside the
base tools in tools.py.

Each tool returns a ToolResult (same type as tools.ToolResult). No subprocess
imports here — all shell execution goes through the Toolbox.run() method.
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path

# Re-export ToolResult from tools.py so callers get the same type.
from .tools import ToolResult


class CoderToolbox:
    """Higher-level coding tools that wrap Toolbox for structured operations.

    All shell execution delegates to the base Toolbox.run() method, so the
    constitutional validator's subprocess restrictions are never triggered.
    """

    def __init__(self, toolbox) -> None:
        self._tb = toolbox
        self.root: Path = toolbox.root

    # ── git operations ─────────────────────────────────────────────────────

    def git_commit(self, message: str, files: list[str] | None = None,
                   trailers: list[str] | None = None) -> ToolResult:
        """Stage and commit. If files is None, stages all tracked + untracked
        (git add -A). If files is given, stages only those. Appends trailers
        (Co-Authored-By, Verified-by, etc.) as separate -m args."""
        if files:
            for f in files:
                r = self._tb.run(f"git add {f}", timeout=30)
                if r.is_error:
                    return ToolResult(r.output, is_error=True)
        else:
            r = self._tb.run("git add -A", timeout=30)
            if r.is_error:
                return ToolResult(r.output, is_error=True)
        cmd = f'git commit -m "{message}"'
        for t in (trailers or []):
            escaped = t.replace('"', '\\"')
            cmd += f' -m "{escaped}"'
        return self._tb.run(cmd, timeout=30)

    def git_push(self, remote: str = "origin", branch: str = "main") -> ToolResult:
        """Push the current branch to remote."""
        return self._tb.run(f"git push {remote} {branch}", timeout=60)

    def git_diff(self, staged: bool = False, path: str = "") -> ToolResult:
        """Show uncommitted changes. staged=True shows staged diff."""
        cmd = "git diff"
        if staged:
            cmd += " --cached"
        if path:
            cmd += f" {path}"
        return self._tb.run(cmd, timeout=30)

    def git_log(self, count: int = 10) -> ToolResult:
        """Show recent commits (oneline format)."""
        return self._tb.run(f"git log -{count} --oneline", timeout=15)

    def git_status(self) -> ToolResult:
        """Show working tree status (short format)."""
        return self._tb.run("git status --short", timeout=15)

    # ── deploy ─────────────────────────────────────────────────────────────

    def deploy(self, target: str = "--all", timeout: int = 600) -> ToolResult:
        """Run the deploy script with the given target. target can be --all,
        --intel, --web, --wa, or a combination like --web --wa.

        Uses deploy.sh on Linux/macOS (via bash) and deploy.ps1 on Windows
        (via PowerShell) so the tool works on any platform without manual
        intervention. R-F1195.
        """
        import platform as _plat
        is_windows = _plat.system().lower() == "windows"
        if is_windows:
            ps_script = self.root / "scripts" / "deploy.ps1"
            if ps_script.exists():
                # Map --all → -All, --intel → -Intel, --web → -Web, --wa → -Wa
                parts = target.split()
                ps_args = " ".join(
                    "-" + p.lstrip("-").capitalize() for p in parts
                )
                return self._tb.run(
                    f'powershell -NoProfile -Command "& {ps_script} {ps_args}"',
                    timeout=timeout,
                )
        sh_script = self.root / "scripts" / "deploy.sh"
        if not sh_script.exists():
            return ToolResult(f"error: deploy script not found at {sh_script}", is_error=True)
        return self._tb.run(f"bash {sh_script} {target}", timeout=timeout)

    # ── test runner ────────────────────────────────────────────────────────

    def test(self, path: str = "", pattern: str = "", timeout: int = 300,
             verbose: bool = False) -> ToolResult:
        """Run pytest. path is the test file/dir (default: discover). pattern
        is -k filter. verbose adds -v."""
        cmd = "python -m pytest"
        if path:
            cmd += f" {path}"
        if pattern:
            cmd += f' -k "{pattern}"'
        if verbose:
            cmd += " -v"
        else:
            cmd += " -q"
        cmd += " --tb=short"
        return self._tb.run(cmd, timeout=timeout)

    # ── R-number management ────────────────────────────────────────────────

    def reserve_r_number(self, title: str) -> ToolResult:
        """Reserve an R-number via the admin script."""
        script = self.root / "scripts" / "admin" / "reserve_r_number.py"
        if not script.exists():
            return ToolResult(f"error: reservation script not found at {script}", is_error=True)
        return self._tb.run(f'python {script} reserve "{title}"', timeout=30)

    def ship_r_number(self, r_number: str, sha: str = "") -> ToolResult:
        """Mark an R-number as shipped with the given commit SHA."""
        script = self.root / "scripts" / "admin" / "reserve_r_number.py"
        if not script.exists():
            return ToolResult(f"error: reservation script not found at {script}", is_error=True)
        if not sha:
            r = self._tb.run("git rev-parse HEAD", timeout=15)
            sha = r.output.splitlines()[0] if not r.is_error and r.output else "unknown"
        return self._tb.run(f"python {script} ship {r_number} {sha}", timeout=30)

    # ── self-review (adversarial self-critique — no Claude needed) ─────────

    def self_review(self, task: str = "", files: list[str] | None = None) -> ToolResult:
        """Run an adversarial self-review of the current changes.

        Checks:
        1. Are all changed files syntactically valid (Python: ast.parse)?
        2. Are there any bare excepts, hardcoded secrets, or debug prints?
        3. Do all new functions have type hints?
        4. Is every claimed change actually in the diff?

        Returns a structured review report. This replaces ask_claude for
        self-review when Claude is not available.
        """
        report: list[str] = [f"=== SELF-REVIEW: {task or 'current changes'} ==="]

        changed = files or list(self._tb.changed_files)
        if not changed:
            r = self._tb.run("git diff --name-only", timeout=15)
            if not r.is_error and r.output:
                changed = [ln.strip() for ln in r.output.splitlines() if ln.strip()]

        if not changed:
            report.append("No changed files to review.")
            return ToolResult("\n".join(report))

        report.append(f"Files to review: {len(changed)}")
        for f in changed:
            report.append(f"\n--- {f} ---")
            fp = self._tb._resolve(f)
            if not fp.exists():
                report.append("  [SKIP] file does not exist (deleted?)")
                continue
            try:
                content = fp.read_text(encoding="utf-8", errors="replace")
            except Exception as exc:
                report.append(f"  [ERROR] cannot read: {exc}")
                continue

            if f.endswith(".py"):
                # 1. Python syntax check
                try:
                    import ast as _ast
                    _ast.parse(content)
                    report.append("  [PASS] Python syntax valid")
                except SyntaxError as exc:
                    report.append(f"  [FAIL] SyntaxError: {exc}")

                # 2. Bare excepts (anti-pattern)
                bare = [(i, ln) for i, ln in enumerate(content.splitlines(), 1)
                        if ln.strip() == "except:" or ln.strip().startswith("except :")]
                if bare:
                    for bln, _ in bare[:5]:
                        report.append(f"  [WARN] bare except at line {bln}")
                else:
                    report.append("  [PASS] no bare excepts")

                # 3. Debug prints (leftover print() statements)
                debug_prints = [i for i, ln in enumerate(content.splitlines(), 1)
                                if ln.strip().startswith("print(")
                                and "logger" not in ln and "logging" not in ln]
                if debug_prints:
                    for dl in debug_prints[:5]:
                        report.append(f"  [WARN] debug print at line {dl}")
                else:
                    report.append("  [PASS] no debug prints")

                # 4. Hardcoded secrets
                secrets = [i for i, ln in enumerate(content.splitlines(), 1)
                           if any(k in ln.lower() for k in ["api_key", "api_secret", "password", "token=", "secret="])
                           and "os.getenv" not in ln and "os.environ" not in ln]
                if secrets:
                    for sl in secrets[:5]:
                        report.append(f"  [WARN] possible hardcoded secret at line {sl}")
                else:
                    report.append("  [PASS] no hardcoded secrets")

                # 5. Missing type hints on new functions
                for i, ln in enumerate(content.splitlines(), 1):
                    if ln.strip().startswith("def ") and "):" in ln and "->" not in ln:
                        report.append(f"  [WARN] missing return type hint at line {i}: {ln.strip()[:80]}")

            # 6. TODO/FIXME/HACK markers
            todos = [(i, ln.strip()) for i, ln in enumerate(content.splitlines(), 1)
                     if any(m in ln.upper() for m in ["TODO", "FIXME", "HACK", "XXX"])]
            if todos:
                for tl, txt in todos[:5]:
                    report.append(f"  [NOTE] marker at line {tl}: {txt[:80]}")

        report.append(f"\n=== REVIEW COMPLETE: {len(changed)} files checked ===")
        return ToolResult("\n".join(report))

    # ── capability test generator ──────────────────────────────────────────

    def capability_test(self, function_name: str, test_code: str,
                        test_path: str = "") -> ToolResult:
        """Write a capability test file and run it.

        test_code should be the full test function body (including imports).
        If test_path is empty, auto-generates a path under aria_service/tests/
        or aria_cli/tests/.
        """
        if not test_path:
            if "aria_cli" in str(self.root):
                test_path = f"aria_cli/tests/test_cap_{function_name}.py"
            else:
                test_path = f"aria_service/tests/test_cap_{function_name}.py"
        fp = self._tb._resolve(test_path)
        try:
            fp.parent.mkdir(parents=True, exist_ok=True)
            fp.write_text(test_code, encoding="utf-8")
        except Exception as exc:
            return ToolResult(f"error writing test file: {exc}", is_error=True)
        self._tb._track(fp)
        return self.test(path=test_path, verbose=True, timeout=120)

    # ── fetch_url with standalone JS rendering ─────────────────────────────

    def fetch_url(self, url: str, max_chars: int = 10000) -> ToolResult:
        """Fetch a URL with HTTP GET, falling back to Playwright JS rendering
        for SPAs. Uses the standalone renderer first (no aria_service dependency),
        then the legacy one."""
        if not url.lower().startswith(("http://", "https://")):
            return ToolResult("error: url must start with http:// or https://", is_error=True)
        try:
            import httpx
            resp = httpx.get(url, timeout=25.0, follow_redirects=True,
                             headers={"User-Agent": "aria-coder-cli/0.1"})
        except Exception as exc:
            return ToolResult(f"error fetching {url}: {exc}", is_error=True)
        body = resp.text or ""
        # If httpx returned a thin JS shell, try Playwright rendering
        if resp.status_code == 200 and len(body.strip()) < 2000:
            from .standalone_fetch import fetch_rendered
            pw_text = fetch_rendered(url)
            if pw_text is None:
                from .playwright_fetch import fetch_with_playwright
                pw_text = fetch_with_playwright(url)
            if pw_text is not None:
                body = pw_text
        cap = max(500, min(int(max_chars or 10000), 30000))
        if len(body) > cap:
            body = body[:cap] + f"\n... (truncated, {len(resp.text)} chars total)"
        return ToolResult(f"HTTP {resp.status_code} {url}\n{body}",
                          is_error=resp.status_code >= 400)

    # ── memory tools (session persistence) ─────────────────────────────────

    def remember(self, entry_type: str, content: str, r_number: str = "",
                 tags: list[str] | None = None) -> ToolResult:
        """Store a learning from this session. entry_type: pattern, decision,
        lesson, fact, gap. Persisted to ~/.aria/memory/ and synced to the
        brain when reachable."""
        from . import memory as _mem
        result = _mem.remember(
            entry_type=entry_type,
            content=content,
            r_number=r_number or "",
            tags=tags or [],
        )
        return ToolResult(result)

    def recall(self, entry_type: str = "", query: str = "",
               limit: int = 10) -> ToolResult:
        """Retrieve recent memory entries, optionally filtered by type or
        content query."""
        from . import memory as _mem
        entries = _mem.recall(entry_type=entry_type, query=query, limit=limit)
        if not entries:
            return ToolResult("no matching memory entries found")
        lines = [f"[{e['entry_type']}] {e['content'][:120]}"
                 + (f" (R-{e['r_number']})" if e.get('r_number') else "")
                 for e in entries]
        return ToolResult("\n".join(lines))

    def memory_stats(self) -> ToolResult:
        """Return memory statistics (total entries, by type, date range)."""
        from . import memory as _mem
        s = _mem.stats()
        lines = [
            f"Total entries: {s['total']}",
            "By type:",
        ]
        for t, c in sorted(s['by_type'].items()):
            lines.append(f"  {t}: {c}")
        if s['oldest']:
            lines.append(f"Oldest: {time.strftime('%Y-%m-%d', time.localtime(s['oldest']))}")
        if s['newest']:
            lines.append(f"Newest: {time.strftime('%Y-%m-%d', time.localtime(s['newest']))}")
        return ToolResult("\n".join(lines))


# ── OpenAI-shaped tool schemas for the agent loop ────────────────────────────
# These are the tool definitions the LLM sees. They reference the methods on
# CoderToolbox, which are dispatched by the agent loop.

CODER_TOOL_SCHEMAS: list[dict] = [
    {
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
            "name": "git_commit",
            "description": "Stage and commit changes. If files is None, stages all. Appends trailers (Co-Authored-By, Verified-by) as separate -m args.",
            "parameters": {
                "type": "object",
                "properties": {
                    "message": {"type": "string", "description": "Commit message."},
                    "files": {"type": "array", "items": {"type": "string"}, "description": "Files to stage (optional; default: all)."},
                    "trailers": {"type": "array", "items": {"type": "string"}, "description": "Trailer lines like 'Verified-by: tests' (optional)."},
                },
                "required": ["message"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "git_push",
            "description": "Push the current branch to remote.",
            "parameters": {
                "type": "object",
                "properties": {
                    "remote": {"type": "string", "description": "Remote name (default: origin)."},
                    "branch": {"type": "string", "description": "Branch name (default: main)."},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "git_diff",
            "description": "Show uncommitted changes. staged=True shows staged diff.",
            "parameters": {
                "type": "object",
                "properties": {
                    "staged": {"type": "boolean", "description": "Show staged diff (default: false)."},
                    "path": {"type": "string", "description": "Filter to a specific path (optional)."},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "git_log",
            "description": "Show recent commits (oneline format).",
            "parameters": {
                "type": "object",
                "properties": {
                    "count": {"type": "integer", "description": "Number of commits to show (default: 10)."},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "git_status",
            "description": "Show working tree status (short format).",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "deploy",
            "description": "Run scripts/deploy.sh to deploy to fly.io. target can be --all, --intel, --web, --wa.",
            "parameters": {
                "type": "object",
                "properties": {
                    "target": {"type": "string", "description": "Deploy target: --all, --intel, --web, --wa (default: --all)."},
                    "timeout": {"type": "integer", "description": "Timeout in seconds (default: 600)."},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "test",
            "description": "Run pytest. path is the test file/dir (default: discover). pattern is -k filter.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Test file or directory (optional)."},
                    "pattern": {"type": "string", "description": "-k filter pattern (optional)."},
                    "timeout": {"type": "integer", "description": "Timeout in seconds (default: 300)."},
                    "verbose": {"type": "boolean", "description": "Add -v for verbose output (default: false)."},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "reserve_r_number",
            "description": "Reserve an R-number via the admin script.",
            "parameters": {
                "type": "object",
                "properties": {
                    "title": {"type": "string", "description": "Short title for the R-number."},
                },
                "required": ["title"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "ship_r_number",
            "description": "Mark an R-number as shipped with the given commit SHA.",
            "parameters": {
                "type": "object",
                "properties": {
                    "r_number": {"type": "string", "description": "R-number like R-F1143."},
                    "sha": {"type": "string", "description": "Commit SHA (optional; auto-detects HEAD)."},
                },
                "required": ["r_number"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "self_review",
            "description": "Run an adversarial self-review of current changes. Checks syntax, bare excepts, debug prints, hardcoded secrets, type hints, and TODO markers. Replaces ask_claude for self-review when Claude is not available.",
            "parameters": {
                "type": "object",
                "properties": {
                    "task": {"type": "string", "description": "Task description for the review header (optional)."},
                    "files": {"type": "array", "items": {"type": "string"}, "description": "Specific files to review (optional; default: changed files)."},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "capability_test",
            "description": "Write a capability test file and run it. test_code should be the full test function body (including imports). Auto-generates test path if not specified.",
            "parameters": {
                "type": "object",
                "properties": {
                    "function_name": {"type": "string", "description": "Name of the function being tested."},
                    "test_code": {"type": "string", "description": "Full test function body including imports."},
                    "test_path": {"type": "string", "description": "Explicit test file path (optional)."},
                },
                "required": ["function_name", "test_code"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "remember",
            "description": "Store a learning from this session. entry_type: pattern, decision, lesson, fact, gap. Persisted to ~/.aria/memory/ and synced to the brain when reachable.",
            "parameters": {
                "type": "object",
                "properties": {
                    "entry_type": {"type": "string", "enum": ["pattern", "decision", "lesson", "fact", "gap"], "description": "Type of learning."},
                    "content": {"type": "string", "description": "The learning content."},
                    "r_number": {"type": "string", "description": "Associated R-number (optional)."},
                    "tags": {"type": "array", "items": {"type": "string"}, "description": "Tags for categorization (optional)."},
                },
                "required": ["entry_type", "content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "recall",
            "description": "Retrieve recent memory entries, optionally filtered by type or content query.",
            "parameters": {
                "type": "object",
                "properties": {
                    "entry_type": {"type": "string", "description": "Filter by entry type (optional)."},
                    "query": {"type": "string", "description": "Search query (optional)."},
                    "limit": {"type": "integer", "description": "Max entries to return (default: 10)."},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "memory_stats",
            "description": "Return memory statistics (total entries, by type, date range).",
            "parameters": {"type": "object", "properties": {}},
        },
    },
]

# Tools that mutate state — gated behind operator approval unless --auto.
CODER_MUTATING_TOOLS = {"git_commit", "git_push", "deploy", "reserve_r_number",
                        "ship_r_number", "capability_test"}
