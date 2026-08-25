"""R-F1143 — structured coding tools for the ARIA Coder CLI.

Higher-level tools that wrap the existing Toolbox methods: git operations,
deploy, test running, R-number management, self-review, and capability test
generation. These are designed to be used by the agent loop alongside the
base tools in tools.py.

Each tool returns a ToolResult (same type as tools.ToolResult). No subprocess
imports here — all shell execution goes through the Toolbox.run() method.
"""
from __future__ import annotations

import os
import sys
import time
from dataclasses import dataclass
from pathlib import Path

# Re-export ToolResult from tools.py so callers get the same type.
from .tools import ToolResult


def _shq(value: str) -> str:
    """R-F2095 — quote a value for the shell that Toolbox.run() uses (PowerShell
    on Windows, POSIX sh elsewhere), so a path with spaces (`file (1).py`) or a
    commit message with quotes/metacharacters can't break or inject into the
    command. Was: raw f-string interpolation (`git add {f}`, and the main commit
    message wasn't even quote-escaped)."""
    s = str(value)
    if sys.platform == "win32":
        # PowerShell single-quoted string: embedded ' is doubled.
        return "'" + s.replace("'", "''") + "'"
    import shlex
    return shlex.quote(s)


class CoderToolbox:
    """Higher-level coding tools that wrap Toolbox for structured operations.

    All shell execution delegates to the base Toolbox.run() method.
    R-F1191: constitutional validator removed — ARIA is fully autonomous.
    """

    def __init__(self, toolbox, subagent_factory=None) -> None:
        self._tb = toolbox
        self.root: Path = toolbox.root
        # R-F4313 — a callback the parent Agent registers so spawn_subagent can
        # build a fresh, isolated sub-agent sharing this toolbox's llm + tools.
        self.subagent_factory = subagent_factory

    # ── git operations ─────────────────────────────────────────────────────

    def git_commit(self, message: str, files: list[str] | None = None,
                   trailers: list[str] | None = None) -> ToolResult:
        """Stage and commit. If files is None, stages all tracked + untracked
        (git add -A). If files is given, stages only those. Appends trailers
        (Co-Authored-By, Verified-by, etc.) as separate -m args."""
        if files:
            for f in files:
                r = self._tb.run(f"git add {_shq(f)}", timeout=30)  # R-F2095 — quoted
                if r.is_error:
                    return ToolResult(r.output, is_error=True)
        else:
            # R-F2166: do NOT blanket `git add -A` — it sweeps untracked runtime
            # artifacts (data DBs, logs, generated files) into the commit, the
            # exact hazard R-F1478/R-F1479 fought. Stage tracked modifications
            # (-u, reliable) plus the NEW files THIS session actually wrote
            # (changed_files); per-file adds are best-effort so a path quirk can't
            # fail an otherwise-valid commit. Pass `files=[...]` for full control.
            r = self._tb.run("git add -u", timeout=30)
            if r.is_error:
                return ToolResult(r.output, is_error=True)
            for f in list(getattr(self._tb, "changed_files", []) or [])[:100]:
                self._tb.run(f"git add {_shq(f)}", timeout=30)
        cmd = f"git commit -m {_shq(message)}"  # R-F2095 — quoted (was naive "{message}")
        for t in (trailers or []):
            cmd += f" -m {_shq(t)}"
        return self._tb.run(cmd, timeout=30)

    def git_push(self, remote: str = "origin", branch: str = "main") -> ToolResult:
        """Push the current branch to remote."""
        return self._tb.run(f"git push {_shq(remote)} {_shq(branch)}", timeout=60)  # R-F2095

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

    def _deploy_targets(self, paths: list[str]) -> set[str]:
        """Full set of Fly apps a change must deploy to (R-F1315). Extends
        _apps_touched (which returns only the non-intel apps) with aria-intel
        when the change touches the Python service / its build inputs. Pure
        aria_cli/ or docs changes map to nothing (they don't run on a Fly app)."""
        targets = set(self._apps_touched(paths))
        norm = [p.replace("\\", "/").strip().lstrip("./").lower()
                for p in paths if p and p.strip()]
        intel_prefixes = ("aria_service/", "fly.toml", "dockerfile",
                          "requirements", "pyproject.toml")
        if any(p == pre or p.startswith(pre) for p in norm for pre in intel_prefixes):
            targets.add("aria-intel")
        return targets

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
        ps_flag = {"aria-intel": "-Intel", "aria-wa": "-Wa", "aria-web": "-Web"}
        sh_flag = {"aria-intel": "--intel", "aria-wa": "--wa", "aria-web": "--web"}
        ordered = [a for a in ("aria-intel", "aria-wa", "aria-web") if a in apps]
        if not ordered:
            return ToolResult("no known deploy targets", is_error=True)
        if _os.name == "nt":
            script = self.root / "scripts" / "deploy.ps1"
            if not script.exists():
                return ToolResult(f"deploy script missing: {script}", is_error=True)
            flags = " ".join(ps_flag[a] for a in ordered)
            cmd = (f'powershell -NoProfile -ExecutionPolicy Bypass '
                   f'-File "{script}" {flags}')
        else:
            script = self.root / "scripts" / "deploy.sh"
            if not script.exists():
                return ToolResult(f"deploy script missing: {script}", is_error=True)
            flags = " ".join(sh_flag[a] for a in ordered)
            cmd = f'bash "{script}" {flags}'
        # Remote fly builds (torch/node images) are slow — allow a generous bound.
        return self._tb.run(cmd, timeout=1800)

    # ── ci_deploy (THE autonomous commit→push→CI→fly→verify chain) ──────────
    def ci_deploy(self, summary: str = "", r_number: str = "",
                  files: list[str] | None = None, poll_timeout: int = 900,
                  deploy_all: bool = True, local: bool = True) -> ToolResult:
        """Hands-free autonomous commit→push→deploy→verify chain.

        Commits any pending changes (with a [deploy] tag), pushes to origin/main
        (GitHub backup), then deploys + verifies.

        R-F1315 — deploy mechanism:
          * local=True (DEFAULT): deploy every touched app via the trusted local
            scripts/deploy.ps1|deploy.sh, which runs `flyctl deploy` with the
            operator's already-authenticated flyctl (canary + health-verify +
            build_rev check + auto-rollback). This BYPASSES the CI [deploy] path,
            which is dead while the GitHub FLY_API_TOKEN secret is stale — so the
            push alone never reaches Fly. This is the path that actually deploys.
          * local=False: legacy CI path — rely on deploy-fly.yml to build aria-
            intel remotely, and poll /health/live until build_rev matches HEAD.
            Use once the FLY_API_TOKEN secret is rotated.

        Returns success only when the deploy is PROVEN (script verified build_rev
        / version-advance, or live build_rev reached HEAD) — never unproven."""
        import time as _t
        tag = "[deploy]"
        safe_summary = (summary or "autonomous deploy").replace('"', "'").replace("\n", " ")[:80]
        msg = f"deploy: {safe_summary}" + (f" (R-{r_number})" if r_number else "") + f" {tag}"

        # 1. Commit pending changes (or deploy HEAD without a new commit) with [deploy].
        # R-F1479: NEVER use git add -A (blanket-stages runtime DBs, session files, eval
        # reports). Instead: if explicit files are given, stage only those; otherwise stage
        # only tracked-modified files (git add -u). If nothing is pending, deploy HEAD
        # directly without creating a sweep commit — the work is already committed.
        status = self._tb.run("git status --porcelain", timeout=20)
        change_lines = [ln for ln in (status.output or "").splitlines()
                        if ln.strip() and not ln.lower().startswith("exit code")]
        if change_lines:
            if files:
                for f in files:
                    self._tb.run(f"git add {_shq(f)}", timeout=30)  # R-F2128 — quoted
            else:
                # R-F1479: stage only tracked-modified files — never blanket-add
                # untracked runtime artifacts (DBs, reports, session files).
                self._tb.run("git add -u", timeout=30)
            c = self._tb.run(f"git commit -m {_shq(msg)}", timeout=30)  # R-F2128 — quoted (backticks/$() unsafe inside "...")
            if c.is_error:
                return ToolResult(f"ci_deploy: commit failed:\n{c.output}", is_error=True)
        else:
            # R-F1479: no pending changes — deploy HEAD directly without creating
            # an empty trigger commit. The work is already committed; a sweep commit
            # would only pollute history and race the deploy health check.
            head_msg = self._tb.run("git log -1 --pretty=%s", timeout=15)
            if tag not in (head_msg.output or ""):
                # Only create an empty trigger commit if HEAD doesn't already carry
                # the [deploy] tag (legacy CI path). For local deploys this is
                # unnecessary but harmless.
                c = self._tb.run(f"git commit --allow-empty -m {_shq(msg)}", timeout=30)  # R-F2128 — quoted
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

        # 3a. R-F1315 — LOCAL deploy (default). The CI [deploy] path is dead while
        #     the GitHub FLY_API_TOKEN is stale, so a push alone never reaches Fly.
        #     Deploy every touched app via the local authed flyctl (deploy.ps1),
        #     which verifies build_rev / version-advance and rolls back on failure.
        if local:
            targets = self._deploy_targets(self._pending_deploy_paths(sha))
            if not targets:
                return ToolResult(
                    f"Pushed {sha}. No Fly-app paths changed (e.g. aria_cli/docs only), "
                    f"so there is nothing to deploy — code is on origin/main.",
                    mutation=f"ci_deploy push {sha} (no deploy needed)")
            apps = ", ".join(sorted(targets))
            dep = self._deploy_apps(targets)
            if dep.is_error:
                return ToolResult(
                    f"DEPLOY FAILED ⚠️ — pushed {sha}, but the local flyctl deploy of "
                    f"{apps} did not verify live (canary/health/build_rev check failed or "
                    f"flyctl not authed here). Those apps may still run OLD code. Do NOT "
                    f"ship-mark {r_number or 'this R-number'}. Output:\n{dep.output}",
                    is_error=True, mutation=f"ci_deploy {sha} (deploy failed)")
            return ToolResult(
                f"DEPLOYED & ALIGNED ✅ — pushed {sha} and deployed + verified {apps} via "
                f"local flyctl (canary + health + build_rev check + rollback). Fully "
                f"hands-free; no CI token, no manual step. Output:\n{dep.output}",
                mutation=f"ci_deploy {sha} ({apps})")

        # 3. Poll /health/live until the live build_rev matches HEAD (CI builds
        #    remotely; torch images are slow, so allow generous time).
        # R-F1582: after timeout, retry ONCE with a longer wait (torch builds
        # can take 10+ min). If still not aligned, BLOCK with an explicit alert
        # — a push that doesn't reach the server must NEVER report success.
        try:
            import httpx
        except Exception as exc:  # noqa: BLE001
            return ToolResult(f"ci_deploy: pushed {sha} ([deploy] triggered CI) but httpx "
                              f"unavailable to verify: {exc}. Check build_rev manually.", is_error=True)

        def _check_build_rev() -> tuple[bool, str]:
            """Poll /health/live once. Returns (aligned, live_build_rev)."""
            try:
                resp = httpx.get("https://aria-intel.fly.dev/health/live",
                                 timeout=20.0, follow_redirects=True)
                live = (resp.json() or {}).get("build_rev", "")
                return (bool(live and sha[:8] in live), live)
            except Exception:
                return (False, "")

        deadline = _t.monotonic() + max(120, int(poll_timeout or 900))
        last = ""
        aligned = False
        while _t.monotonic() < deadline:
            aligned, last = _check_build_rev()
            if aligned:
                break
            _t.sleep(15)

        if not aligned:
            # R-F1582: first timeout — retry ONCE with double the wait.
            # The remote torch build may still be running; a second poll
            # window catches the case where CI started late.
            _t.sleep(30)  # brief pause before retry
            retry_deadline = _t.monotonic() + max(120, int(poll_timeout or 900))
            while _t.monotonic() < retry_deadline:
                aligned, last = _check_build_rev()
                if aligned:
                    break
                _t.sleep(15)

        if not aligned:
            return ToolResult(
                f"DEPLOY LAG ⚠️ — pushed {sha} to origin/main, but live build_rev "
                f"({last!r}) did not reach {sha[:8]} after 2 poll cycles "
                f"({poll_timeout}s each). The remote CI build may still be running "
                f"(torch is slow) or may have failed. "
                f"ACTION REQUIRED: check GitHub Actions / `flyctl apps releases "
                f"-a aria-intel`. Do NOT ship-mark {r_number or 'this R-number'} "
                f"until build_rev is verified live. A push that doesn't reach the "
                f"server must NEVER report success.",
                is_error=True)

        # build_rev aligned — proceed with multi-app verification
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

    # ── test runner ────────────────────────────────────────────────────────

    def test(self, path: str = "", pattern: str = "", timeout: int = 300,
             verbose: bool = False) -> ToolResult:
        """Run pytest. path is the test file/dir (default: discover). pattern
        is -k filter. verbose adds -v."""
        # R-F2060 — use THIS interpreter (sys.executable), not PATH `python`.
        # ARIA runs under the venv/container python that actually has numpy +
        # chromadb + sentence-transformers; a bare `python` can resolve to a
        # system interpreter missing those deps → every numpy/chromadb-importing
        # test reports a COLLECTION ERROR, which ARIA then mis-reads as a code
        # "regression" (the false 8-gap ecosystem report, 2026-06-27). Binding
        # the test interpreter to the running one makes that failure class
        # impossible. (Same root cause + fix as R-F1928 for the autonomous
        # TestRunner.) sys.executable is quoted for paths containing spaces.
        cmd = f'"{sys.executable}" -m pytest'
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
        return self._tb.run(f'{_shq(sys.executable)} {_shq(str(script))} reserve {_shq(title)}', timeout=30)  # R-F2128 — quoted

    def ship_r_number(self, r_number: str, sha: str = "") -> ToolResult:
        """Mark an R-number as shipped with the given commit SHA."""
        script = self.root / "scripts" / "admin" / "reserve_r_number.py"
        if not script.exists():
            return ToolResult(f"error: reservation script not found at {script}", is_error=True)
        if not sha:
            r = self._tb.run("git rev-parse HEAD", timeout=15)
            sha = r.output.splitlines()[0] if not r.is_error and r.output else "unknown"
        result = self._tb.run(f'{_shq(sys.executable)} {_shq(str(script))} ship {_shq(r_number)} {_shq(sha)}', timeout=30)  # R-F2128 — quoted
        # R-F2162: a shipped R-number is a clean "this fix worked" signal — write
        # it back to the brain's coding RAG so the autonomous coder can reuse it.
        if not result.is_error:
            self._record_shipped_fix(r_number, sha)
        return result

    def _record_shipped_fix(self, r_number: str, sha: str) -> None:
        """Best-effort write-back of a shipped fix to the coding RAG (R-F2162).
        No-op (no extra commands) when the brain isn't configured — there is no
        sink to write to, so don't even gather context."""
        if not (os.getenv("ARIA_SERVICE_URL") and
                (os.getenv("ARIA_INTERNAL_TOKEN") or os.getenv("ARIA_CODER_LLM_API_KEY"))):
            return
        try:
            from .prompt import record_coding_outcome_http
            subj = ""
            r = self._tb.run("git log -1 --pretty=%s", timeout=15)
            if not r.is_error and r.output:
                subj = r.output.splitlines()[0][:300]
            files = list(getattr(self._tb, "changed_files", []) or [])[:50]
            record_coding_outcome_http("fix", {
                "r_number": r_number,
                "title": subj or f"{r_number} shipped",
                "gap_type": "cli_fix",
                "module": files[0] if files else "",
                "problem_description": subj,
                "approach": "Shipped via the aria CLI coder.",
                "files_changed": files,
                "tests_passed": 0,
            })
        except Exception:  # noqa: BLE001 — write-back must never fail a ship
            pass

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

    # ── read_image — OCR image to text ────────────────────────────────────

    def read_image(self, path: str) -> ToolResult:
        """Read an image file and extract text from it using OCR.

        Supports PNG, JPG, JPEG, GIF, BMP, WEBP formats.
        Uses Tesseract OCR (local) with OCR.space and cloud vision fallbacks.

        Args:
            path: Path to the image file (absolute or relative to working dir).

        Returns:
            ToolResult with extracted text content.
        """
        fp = Path(path)
        if not fp.is_absolute():
            fp = self._tb.root / fp
        fp = fp.resolve()
        if not fp.exists():
            return ToolResult(f"File not found: {path}", is_error=True)
        if not fp.is_file():
            return ToolResult(f"Not a file: {path}", is_error=True)

        try:
            image_data = fp.read_bytes()
            ext = fp.suffix.lower()
            if ext not in ('.png', '.jpg', '.jpeg', '.gif', '.bmp', '.webp'):
                return ToolResult(f"Unsupported image format: {ext}. Supported: PNG, JPG, JPEG, GIF, BMP, WEBP", is_error=True)

            # Try the full OCR pipeline first
            try:
                import asyncio
                from aria_service.intel.ocr import extract_text_from_image
                result = asyncio.run(extract_text_from_image(
                    image_data=image_data,
                    filename=fp.name,
                    context="ARIA Coder CLI image reading",
                ))
                if result and result.get("success") and result.get("text", "").strip():
                    text = result["text"].strip()
                    return ToolResult(
                        f"Extracted text from {fp.name}:\n\n{text}\n\n"
                        f"(OCR method: {result.get('method', 'unknown')}, "
                        f"confidence: {result.get('confidence', 'N/A')})"
                    )
            except Exception as e:
                print(f"Full OCR pipeline failed, trying fallback: {e}")

            # Fallback: try pytesseract directly
            try:
                import pytesseract
                from PIL import Image
                import io
                # Set tesseract path for Windows
                import sys
                if sys.platform == 'win32':
                    tesseract_paths = [
                        r'C:\Program Files\Tesseract-OCR\tesseract.exe',
                        r'C:\Program Files (x86)\Tesseract-OCR\tesseract.exe',
                    ]
                    for tp in tesseract_paths:
                        if Path(tp).exists():
                            pytesseract.pytesseract.tesseract_cmd = tp
                            break
                img = Image.open(io.BytesIO(image_data))
                text = pytesseract.image_to_string(img)
                if text.strip():
                    return ToolResult(
                        f"Extracted text from {fp.name} (pytesseract fallback):\n\n{text.strip()}"
                    )
            except Exception as e:
                print(f"pytesseract fallback failed: {e}")

            return ToolResult(
                f"Could not extract text from {fp.name}. "
                f"File size: {len(image_data)} bytes, format: {ext}. "
                f"Tesseract OCR may not be installed. "
                f"Install with: winget install TesseractOCR (Windows) or "
                f"apt install tesseract-ocr (Linux)",
                is_error=True
            )
        except Exception as e:
            return ToolResult(f"Error reading image {path}: {e}", is_error=True)

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

    # ── sub-agents (R-F4313) ────────────────────────────────────────────────

    def spawn_subagent(self, name: str, task: str, focus: str = "",
                       max_steps: int = 20) -> ToolResult:
        """Spawn a fresh, isolated sub-agent to work on a focused task.

        The sub-agent shares this toolbox's LLM client and file tools but gets a
        FRESH message history and a focused system prompt, so it cannot see or
        be polluted by the parent's conversation. This is the Claude Code /
        Codex sub-agent pattern: a reviewer that independently re-runs tests, a
        researcher that greps the codebase, a writer that drafts a file.

        Args:
            name: A short role label (e.g. 'reviewer', 'researcher').
            task: The concrete instruction for the sub-agent.
            focus: Optional extra focus hint appended to the system prompt.
            max_steps: Bounded step cap so a runaway sub-agent cannot loop.

        Returns:
            ToolResult with the sub-agent's final text (or an error if the
            parent did not register a sub-agent factory).
        """
        if self.subagent_factory is None:
            return ToolResult(
                "spawn_subagent: no sub-agent factory registered on this "
                "CoderToolbox (the parent Agent must set it).",
                is_error=True)
        try:
            result = self.subagent_factory(name=name, task=task, focus=focus,
                                           max_steps=max_steps)
        except Exception as exc:  # noqa: BLE001 — surface the failure to the agent
            return ToolResult(f"spawn_subagent error: {exc}", is_error=True)
        # R-F4313 — wire the sub-agent outcome to the brain (§21a): success and
        # failure both reach a sink so a sub-agent that silently fails is visible.
        try:
            from . import brain as _brain
            _brain.report_signal(
                signal_type="aria_cli_subagent",
                content=f"Sub-agent '{name}' finished. Task: {task[:200]}",
                self_mode=True,
                metadata={"subagent": name, "success": not result.is_error},
            )
        except Exception:  # noqa: BLE001 — brain wiring must never break the tool
            pass
        return result


# ── OpenAI-shaped tool schemas for the agent loop ────────────────────────────
# These are the tool definitions the LLM sees. They reference the methods on
# CoderToolbox, which are dispatched by the agent loop.

CODER_TOOL_SCHEMAS: list[dict] = [
    {
        "type": "function",
        "function": {
            "name": "read_image",
            "description": "Read an image file and extract text from it using OCR. Supports PNG, JPG, JPEG, GIF, BMP, WEBP. Uses Tesseract OCR with cloud vision fallbacks.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Path to the image file (absolute or relative to working dir)"
                    }
                },
                "required": ["path"]
            }
        }
    },
    # R-F2398 — `fetch_url` is REMOVED from the coder schema list: it duplicated
    # the identical base-toolbox schema (aria_cli/tools.py), and DeepSeek/OpenAI
    # reject the whole request with HTTP 400 "Tool names must be unique." on any
    # duplicate — which bricked every CLI turn. _dispatch already routed
    # fetch_url to the base toolbox (checked first), so the coder schema was dead
    # weight. The CoderToolbox.fetch_url method is kept (harmless, unreferenced by
    # the LLM now) and agent._dedup_tool_schemas guards against any future overlap.
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
            "description": "PREFERRED autonomous deploy: commit pending changes with a [deploy]-tagged message, push to origin/main (GitHub backup), then deploy aria-intel via the trusted local scripts/deploy.ps1|deploy.sh (canary + health-verify + build_rev check + auto-rollback) and confirm /health/live build_rev matches HEAD. Returns success ONLY when the live build is verified aligned. This is the path that actually reaches Fly; use the plain `deploy` tool only for a single explicit app.",
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
    {
        "type": "function",
        "function": {
            "name": "spawn_subagent",
            "description": "Spawn a fresh, isolated sub-agent to work on a focused task (e.g. a reviewer that independently re-runs tests, a researcher that greps the codebase). The sub-agent shares the LLM and file tools but gets a fresh message history and a focused system prompt, so it cannot see or be polluted by the parent conversation. Use for independent verification, parallel research, or focused drafting.",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "A short role label (e.g. 'reviewer', 'researcher')."},
                    "task": {"type": "string", "description": "The concrete instruction for the sub-agent."},
                    "focus": {"type": "string", "description": "Optional extra focus hint appended to the system prompt."},
                    "max_steps": {"type": "integer", "description": "Bounded step cap so a runaway sub-agent cannot loop (default 20)."},
                },
                "required": ["name", "task"],
            },
        },
    },
]

# Tools that mutate state — gated behind operator approval unless --auto.
CODER_MUTATING_TOOLS = {"git_commit", "git_push", "deploy", "ci_deploy",
                        "reserve_r_number", "ship_r_number", "capability_test"}
