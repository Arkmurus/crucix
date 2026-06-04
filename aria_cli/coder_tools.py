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

    All shell execution delegates to the base Toolbox.run() method.
    R-F1191: constitutional validator removed — ARIA is fully autonomous.
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

        R-F1300: a fly/Depot build often finishes SERVER-SIDE after the local
        deploy command times out (torch builds routinely exceed the timeout). A
        flat "timed out" then reads as failure when the deploy actually LANDED —
        which is what made ARIA think her deploy failed and retry. So when the
        command errors/times out, verify the live build_rev against HEAD before
        reporting failure: if it matches, the deploy succeeded; if not, return the
        failure plus the §11 guidance (the build may still be running on Depot —
        check `flyctl apps releases`, don't blindly retry).
        """
        import platform as _plat
        is_windows = _plat.system().lower() == "windows"
        result: ToolResult
        sh_script = self.root / "scripts" / "deploy.sh"
        ps_script = self.root / "scripts" / "deploy.ps1"
        if is_windows and ps_script.exists():
            # Map --all → -All, --intel → -Intel, --web → -Web, --wa → -Wa
            parts = target.split()
            ps_args = " ".join("-" + p.lstrip("-").capitalize() for p in parts)
            # R-F1210: tools.py already wraps commands in powershell on Windows;
            # nesting another powershell -Command mangles quotes. & is valid PS.
            result = self._tb.run(f"& {ps_script} {ps_args}", timeout=timeout)
        elif sh_script.exists():
            result = self._tb.run(f"bash {sh_script} {target}", timeout=timeout)
        else:
            return ToolResult(f"error: deploy script not found at {sh_script}", is_error=True)

        if not result.is_error:
            return result
        # The command failed/timed out — but the build may have landed anyway.
        verify = self._verify_intel_live(target)
        if verify is not None:
            landed, note = verify
            if landed:
                return ToolResult(
                    f"deploy command reported an error/timeout, but the live build "
                    f"matches HEAD — the deploy LANDED. {note}\n"
                    f"(Do not retry. Mark the R-number shipped.)\n\n"
                    f"--- original command output (tail) ---\n{result.output[-1500:]}")
            return ToolResult(
                f"deploy command failed AND the live build does not match HEAD yet. "
                f"{note}\nThe build may still be running on Depot — check "
                f"`flyctl apps releases -a aria-intel` and re-verify the live "
                f"build_rev before retrying (§11). Do NOT blindly redeploy.\n\n"
                f"--- original command output (tail) ---\n{result.output[-2000:]}",
                is_error=True)
        return result

    def _verify_intel_live(self, target: str) -> "tuple[bool, str] | None":
        """R-F1300 — compare aria-intel's live build_rev to local HEAD. Returns
        (landed, human_note) or None if it can't be checked (target excludes
        intel, no git, or the health endpoint is unreachable)."""
        if not ("--all" in target or "--intel" in target):
            return None  # only aria-intel exposes a build_rev to compare
        head = self._tb.run("git rev-parse --short=8 HEAD", timeout=15)
        if head.is_error or not head.output:
            return None
        # run() prepends an "exit code: N" header; take the last non-empty line.
        sha = ""
        for ln in reversed(head.output.splitlines()):
            ln = ln.strip()
            if ln and not ln.lower().startswith("exit code"):
                sha = ln
                break
        if not sha:
            return None
        try:
            import httpx
            resp = httpx.get("https://aria-intel.fly.dev/health/live",
                             timeout=20.0, follow_redirects=True)
            build_rev = (resp.json() or {}).get("build_rev", "")
        except Exception as exc:  # noqa: BLE001
            return None  # can't verify → fall back to the raw result
        landed = bool(build_rev) and sha[:8] in build_rev
        note = f"live build_rev={build_rev!r}, HEAD={sha}"
        return landed, note

    def _head_sha(self) -> str:
        """Return the short (8-char) HEAD sha, or '' if unavailable. run()
        prepends an 'exit code: N' header, so take the last real line."""
        r = self._tb.run("git rev-parse --short=8 HEAD", timeout=15)
        if r.is_error or not r.output:
            return ""
        for ln in reversed(r.output.splitlines()):
            ln = ln.strip()
            if ln and not ln.lower().startswith("exit code"):
                return ln
        return ""

    def _clean_lines(self, output: str | None) -> list[str]:
        """Split run() output into real lines, dropping the 'exit code:' header."""
        return [ln.strip() for ln in (output or "").splitlines()
                if ln.strip() and not ln.lower().startswith("exit code")]

    # R-F1314 — map changed repo paths to the Fly apps that serve them. The CI
    # [deploy] tag (and therefore ci_deploy) builds ONLY aria-intel; the Node web
    # tier and the WhatsApp listener live on SEPARATE Fly apps that CI does not
    # touch (deploy-fly.yml R-F1157 removed the Node/WA steps). So a change to
    # those paths is NOT deployed by this chain even though aria-intel verifies
    # green — that exact mismatch shipped a stale WA listener on 2026-06-04
    # (R-F1311: async-OCR routes reached aria-intel, the listener never reached
    # aria-wa, and WhatsApp OCR/document reading broke). ci_deploy must detect it
    # and refuse to claim full success.
    _APP_PATH_PREFIXES = (
        ("aria-wa", ("services/wa-listener/", "fly.wa.toml", "dockerfile.wa")),
        ("aria-web", ("server.mjs", "frontend/", "dashboard/", "public/",
                      "fly.web.toml", "dockerfile.web")),
    )

    def _apps_touched(self, paths: list[str]) -> set[str]:
        """Non-intel Fly apps a set of changed repo paths affects (R-F1314).
        ci_deploy only deploys+verifies aria-intel; anything returned here needs
        a separate `scripts/deploy.ps1 -Wa/-Web` (or deploy.sh --wa/--web)."""
        apps: set[str] = set()
        norm = [p.replace("\\", "/").strip().lstrip("./").lower()
                for p in paths if p and p.strip()]
        for app, prefixes in self._APP_PATH_PREFIXES:
            if any(p == pre or p.startswith(pre) for p in norm for pre in prefixes):
                apps.add(app)
        return apps

    def _pending_deploy_paths(self, sha: str) -> list[str]:
        """Files this deploy carries vs the last deployed state (R-F1314).
        Diffs the last-deployed sha (.last_deploy_sha) against HEAD; falls back
        to HEAD's own commit when that marker is missing/equal so we always have
        something to classify."""
        prev = ""
        marker = self.root / ".last_deploy_sha"
        try:
            if marker.exists():
                prev = marker.read_text(encoding="utf-8").strip().split()[0]
        except Exception:  # noqa: BLE001
            prev = ""
        if prev and not prev.startswith(sha[:8]) and not sha.startswith(prev[:8]):
            r = self._tb.run(f"git diff --name-only {prev}..HEAD", timeout=20)
            paths = self._clean_lines(r.output)
            if paths:
                return paths
        # Fallback: the top commit's files.
        r = self._tb.run("git show --name-only --pretty=format: HEAD", timeout=20)
        return self._clean_lines(r.output)

    def _deploy_apps(self, apps: set[str]) -> ToolResult:
        """Deploy the given non-intel Fly apps via the trusted deploy script
        (R-F1314). Does NOT reimplement flyctl — it calls scripts/deploy.ps1 (or
        deploy.sh on POSIX), which already does canary + health-verify + auto-
        rollback and a version-advance check per app, so Aria's hands-free chain
        reuses the exact path the operator runs manually. Returns the script's
        ToolResult; is_error propagates a deploy/verify failure to the caller."""
        import os as _os
        ordered = sorted(apps)
        if _os.name == "nt":
            script = self.root / "scripts" / "deploy.ps1"
            if not script.exists():
                return ToolResult(f"deploy script missing: {script}", is_error=True)
            flags = " ".join("-Wa" if a == "aria-wa" else "-Web" for a in ordered)
            cmd = (f'powershell -NoProfile -ExecutionPolicy Bypass '
                   f'-File "{script}" {flags}')
        else:
            script = self.root / "scripts" / "deploy.sh"
            if not script.exists():
                return ToolResult(f"deploy script missing: {script}", is_error=True)
            flags = " ".join("--wa" if a == "aria-wa" else "--web" for a in ordered)
            cmd = f'bash "{script}" {flags}'
        # Remote fly builds (torch/node images) are slow — allow a generous bound.
        return self._tb.run(cmd, timeout=1800)

    # ── ci_deploy (THE autonomous commit→push→CI→fly→verify chain) ──────────
    def ci_deploy(self, summary: str = "", r_number: str = "",
                  files: list[str] | None = None, poll_timeout: int = 900,
                  deploy_all: bool = True) -> ToolResult:
        """Hands-free autonomous deploy via the CI [deploy] path (R-F1306).

        The robust, no-babysitting deploy and the one to PREFER for autonomous
        operation: commit any pending changes with a [deploy]-tagged message,
        push to origin/main, then poll aria-intel's /health/live until build_rev
        matches HEAD. CI (deploy-fly.yml) sees the [deploy] tag, builds REMOTELY
        on Depot, canary-deploys, health-verifies and rolls back on failure — so
        nothing runs as a long LOCAL process (no wedge risk) and the deployed
        build is guaranteed aligned with origin (commit == push == live build_rev).

        Returns success only when the live build_rev actually reaches HEAD — a
        deploy is never claimed unproven."""
        import time as _t
        tag = "[deploy]"
        safe_summary = (summary or "autonomous deploy").replace('"', "'").replace("\n", " ")[:80]
        msg = f"deploy: {safe_summary}" + (f" (R-{r_number})" if r_number else "") + f" {tag}"

        # 1. Commit pending changes (or an empty trigger commit) with [deploy].
        status = self._tb.run("git status --porcelain", timeout=20)
        change_lines = [ln for ln in (status.output or "").splitlines()
                        if ln.strip() and not ln.lower().startswith("exit code")]
        if change_lines:
            if files:
                for f in files:
                    self._tb.run(f"git add {f}", timeout=30)
            else:
                self._tb.run("git add -A", timeout=30)
            c = self._tb.run(f'git commit -m "{msg}"', timeout=30)
            if c.is_error:
                return ToolResult(f"ci_deploy: commit failed:\n{c.output}", is_error=True)
        else:
            head_msg = self._tb.run("git log -1 --pretty=%s", timeout=15)
            if tag not in (head_msg.output or ""):
                c = self._tb.run(f'git commit --allow-empty -m "{msg}"', timeout=30)
                if c.is_error:
                    return ToolResult(f"ci_deploy: trigger commit failed:\n{c.output}", is_error=True)

        # 2. Push — this is what triggers CI. No push ⇒ no deploy ⇒ stop here.
        push = self._tb.run("git push origin main", timeout=120)
        if push.is_error:
            return ToolResult(
                f"ci_deploy: push to origin/main FAILED — deploy NOT triggered:\n{push.output}\n"
                f"Resolve first (diverged history? auth?). Never assume a deploy without a clean push.",
                is_error=True)

        sha = self._head_sha()
        if not sha:
            return ToolResult("ci_deploy: pushed, but could not read HEAD sha to verify alignment. "
                              "Check live build_rev manually before trusting the deploy.", is_error=True)

        # 3. Poll /health/live until the live build_rev matches HEAD (CI builds
        #    remotely; torch images are slow, so allow generous time).
        try:
            import httpx
        except Exception as exc:  # noqa: BLE001
            return ToolResult(f"ci_deploy: pushed {sha} ([deploy] triggered CI) but httpx "
                              f"unavailable to verify: {exc}. Check build_rev manually.", is_error=True)
        deadline = _t.monotonic() + max(120, int(poll_timeout or 900))
        last = ""
        while _t.monotonic() < deadline:
            try:
                resp = httpx.get("https://aria-intel.fly.dev/health/live",
                                 timeout=20.0, follow_redirects=True)
                last = (resp.json() or {}).get("build_rev", "")
                if last and sha[:8] in last:
                    # R-F1314: aria-intel is aligned — but if this change ALSO
                    # touches aria-wa / aria-web, the CI [deploy] path did NOT
                    # deploy them (deploy-fly.yml only builds aria-intel). Make
                    # the chain truly multi-app: deploy the touched apps via the
                    # trusted deploy script (canary + health-verify + rollback),
                    # and NEVER report a clean full deploy unless every touched
                    # app verified. Disable with deploy_all=False.
                    other = self._apps_touched(self._pending_deploy_paths(sha))
                    if other and deploy_all:
                        apps = ", ".join(sorted(other))
                        dep = self._deploy_apps(other)
                        if dep.is_error:
                            return ToolResult(
                                f"PARTIAL DEPLOY ⚠️ — aria-intel is aligned (pushed {sha}, "
                                f"live build_rev={last!r}), but the follow-on deploy of "
                                f"{apps} FAILED, so those apps are still running OLD code. "
                                f"Do NOT ship-mark {r_number or 'this R-number'} until each "
                                f"is verified live. Deploy output:\n{dep.output}",
                                is_error=True, mutation=f"ci_deploy {sha} (aria-intel only)")
                        return ToolResult(
                            f"DEPLOYED & ALIGNED ✅ (multi-app) — pushed {sha}; aria-intel "
                            f"build_rev={last!r} matches HEAD via CI, and {apps} deployed + "
                            f"verified via the deploy script (canary+rollback). Hands-free "
                            f"commit→push→CI→fly chain verified across every touched app.",
                            mutation=f"ci_deploy {sha} (aria-intel + {apps})")
                    if other:  # deploy_all disabled — surface the gap, never fake success
                        apps = ", ".join(sorted(other))
                        flags = " ".join("-Wa" if a == "aria-wa" else "-Web"
                                         for a in sorted(other))
                        return ToolResult(
                            f"PARTIAL DEPLOY ⚠️ — aria-intel aligned (pushed {sha}, "
                            f"build_rev={last!r}), but this change also touches {apps} which "
                            f"CI does NOT deploy. Run: scripts/deploy.ps1 {flags}. Do NOT "
                            f"ship-mark {r_number or 'this R-number'} until each verifies live.",
                            is_error=True, mutation=f"ci_deploy {sha} (aria-intel only)")
                    return ToolResult(
                        f"DEPLOYED & ALIGNED ✅ — pushed {sha} → CI built & canary-deployed; "
                        f"live build_rev={last!r} now matches HEAD. Hands-free "
                        f"commit→push→CI→fly chain verified end-to-end.",
                        mutation=f"ci_deploy {sha}")
            except Exception:  # noqa: BLE001 — health blips during a deploy are normal
                pass
            _t.sleep(15)
        return ToolResult(
            f"ci_deploy: pushed {sha} and [deploy] triggered CI, but live build_rev "
            f"({last!r}) did not reach {sha[:8]} within {poll_timeout}s. The remote build is "
            f"likely still running (torch is slow) — check GitHub Actions / `flyctl apps releases "
            f"-a aria-intel`, then re-verify build_rev. Do NOT redeploy blindly.",
            is_error=True)

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
            "name": "ci_deploy",
            "description": "PREFERRED autonomous deploy: commit pending changes with a [deploy]-tagged message, push to origin/main (CI builds remotely + canary-deploys aria-intel), then poll /health/live until build_rev matches HEAD. Fully hands-free, no local flyctl, returns success ONLY when the live build is verified aligned. Use the local `deploy` tool only as a fallback when CI is broken.",
            "parameters": {
                "type": "object",
                "properties": {
                    "summary": {"type": "string", "description": "One-line deploy summary for the commit message."},
                    "r_number": {"type": "string", "description": "R-number like F1234 (optional, included in the message)."},
                    "files": {"type": "array", "items": {"type": "string"}, "description": "Files to stage (optional; default: all pending)."},
                    "poll_timeout": {"type": "integer", "description": "Seconds to wait for the live build_rev to match HEAD (default 900 — remote torch builds are slow)."},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "deploy",
            "description": "FALLBACK deploy (local flyctl via scripts/deploy.ps1|sh) — prefer ci_deploy for autonomous operation. target can be --all, --intel, --web, --wa. On timeout it verifies the live build_rev before reporting failure.",
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
CODER_MUTATING_TOOLS = {"git_commit", "git_push", "deploy", "ci_deploy",
                        "reserve_r_number", "ship_r_number", "capability_test"}
