"""R-F802 — Zero-downtime Fly.io deployer for autonomous fixes.

Pipeline
────────
  1. git commit on a feature branch  (`aria-auto/r-f-NNNN`)
  2. fly remote build → registry.fly.io/<app>:<tag>
  3. Launch canary machine (1 instance, new image)
  4. Smoke test the canary via Machines API + private DNS
  5. Blue/green fleet swap (`flyctl deploy --strategy bluegreen`)
  6. Open GitHub PR (or merge to main if `ARIA_AUTO_MERGE=1` AND
     change is low-risk)
  7. Record deploy in `crucix:aria:deploy:r-f-NNNN` for rollback

Rollback
────────
  `rollback(r_number, app)` redeploys the image stored against the
  previous R-number. ~21s in practice (matches the bluegreen swap time
  observed on 2026-05-09).

PR-flow vs direct push
──────────────────────
  The default (`ARIA_AUTO_MERGE` unset) opens a GitHub PR via `gh pr
  create`. Operator (or a Claude review API in R-F805) merges.
  Setting `ARIA_AUTO_MERGE=1` re-enables the direct-to-main flow that
  R-F462 was concerned about — only do this once the Claude review hook
  is wired and proven on green for ≥7 days.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import subprocess
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

# R-F1342: bound git/build subprocesses so a hung one can't freeze the
# event loop (Pillar-1 invariant, mirrors R-F1341). All blocking subprocess
# work below also runs via asyncio.to_thread so it never blocks the loop.
_GIT_TIMEOUT_S = float(os.getenv("ARIA_GIT_TIMEOUT_S", "120"))

import httpx

logger = logging.getLogger("aria.autonomous.fly_deployer")

# R-F1320: wire module health to the brain
try:
    from aria_service.intel.engine_wiring import wire_success as _ws1320
    _ws1320(
        module="autonomous.fly_deployer",
        summary="Fly Deployer active",
        source_id="autonomous:fly_deployer:R-F1320",
    )
except Exception:
    pass

FLY_API_BASE = "https://api.machines.dev/v1"
SMOKE_TIMEOUT_S = 60
CANARY_WAIT_S = 15
DEPLOY_HISTORY_MAX = 100

DEPLOY_KEY_PREFIX = "crucix:aria:deploy:r-f-"
DEPLOY_HISTORY_KEY = "crucix:aria:deploy:history"


@dataclass
class DeployResult:
    success: bool
    app: str = ""
    image: str = ""
    r_number: Optional[int] = None
    pr_url: Optional[str] = None
    error: Optional[str] = None
    duration_s: float = 0.0
    deployed_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )


class FlyDeployer:
    """Fly.io deployer with canary + blue/green + PR-based merge."""

    def __init__(
        self,
        redis_client: Any,
        aria_service_url: str,
        repo_path: Optional[Path] = None,
        http_client: Optional[httpx.AsyncClient] = None,
    ) -> None:
        self.redis = redis_client
        self.aria_url = aria_service_url.rstrip("/")
        self.repo_path = repo_path or Path(
            os.environ.get("ARIA_REPO_PATH", "/app")
        )
        token = os.environ.get("FLY_API_TOKEN", "")
        self._client = http_client or httpx.AsyncClient(
            headers={"Authorization": f"Bearer {token}"} if token else {},
            timeout=120,
        )
        self._owns_client = http_client is None

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    # ── DEPLOY ───────────────────────────────────────────────────────────────

    async def deploy(
        self,
        workspace: Path,
        app: str,
        r_number: int,
        summary: str,
        code_changes: dict[str, str],
        auto_merge: Optional[bool] = None,
    ) -> DeployResult:
        """Run the full canary + blue/green + PR flow."""
        start = time.monotonic()
        logger.info("[fly_deployer] deploy R-F%d → %s", r_number, app)

        try:
            branch = f"aria-auto/r-f-{r_number}"

            # R-F1342: git is blocking I/O — run it off the event loop.
            if not await asyncio.to_thread(
                self._git_commit, code_changes, branch, r_number, summary
            ):
                return DeployResult(
                    success=False, app=app, r_number=r_number,
                    error="git commit failed",
                )

            image = await self._fly_build(app, branch)
            if not image:
                return DeployResult(
                    success=False, app=app, r_number=r_number,
                    error="fly build failed",
                )

            canary_id = await self._launch_canary(app, image)
            if not canary_id:
                return DeployResult(
                    success=False, app=app, r_number=r_number,
                    error="canary launch failed",
                )

            await asyncio.sleep(CANARY_WAIT_S)

            if not await self._smoke_test_canary(app, canary_id):
                await self._destroy_machine(app, canary_id)
                return DeployResult(
                    success=False, app=app, r_number=r_number,
                    error="canary smoke tests failed — fleet not updated",
                )

            if not await asyncio.to_thread(self._deploy_fleet, app, image):  # R-F1342
                await self._destroy_machine(app, canary_id)
                return DeployResult(
                    success=False, app=app, r_number=r_number,
                    error="fleet deploy failed",
                )

            # PR-based merge (default) OR direct push if explicitly enabled
            if auto_merge is None:
                auto_merge = (
                    os.environ.get("ARIA_AUTO_MERGE", "0").strip() == "1"
                )

            pr_url = await self._open_pr_or_merge(
                branch=branch, r_number=r_number, summary=summary,
                auto_merge=auto_merge,
            )

            await self._record_deploy(app, r_number, image, pr_url)

            duration = time.monotonic() - start
            logger.info(
                "[fly_deployer] ✅ R-F%d deployed in %.1fs (PR=%s)",
                r_number, duration, pr_url or "direct",
            )
            return DeployResult(
                success=True, app=app, image=image,
                r_number=r_number, pr_url=pr_url, duration_s=duration,
            )
        except Exception as e:
            logger.error("[fly_deployer] deploy failed: %s", e, exc_info=True)
            return DeployResult(
                success=False, app=app, r_number=r_number, error=str(e),
            )

    async def rollback(self, r_number: int, app: str) -> bool:
        """Roll back by redeploying the image that preceded `r_number`."""
        record = await self.redis.get(f"{DEPLOY_KEY_PREFIX}{r_number - 1}")
        if not record:
            logger.error("[fly_deployer] no previous deploy record for rollback")
            return False
        try:
            prev = json.loads(
                record.decode("utf-8") if isinstance(record, bytes) else record
            )
        except json.JSONDecodeError:
            return False
        prev_image = prev.get("image", "")
        if not prev_image:
            return False
        ok = await asyncio.to_thread(self._deploy_fleet, app, prev_image)  # R-F1342
        if ok:
            logger.warning(
                "[fly_deployer] ⚠️ rolled back past R-F%d to %s",
                r_number, prev_image,
            )
        return ok

    # ── GIT ──────────────────────────────────────────────────────────────────

    def _git_commit(
        self,
        code_changes: dict[str, str],
        branch: str,
        r_number: int,
        summary: str,
    ) -> bool:
        try:
            self._run_git(["checkout", "-B", branch])
            for filepath, content in code_changes.items():
                target = self.repo_path / filepath
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(content, encoding="utf-8")
            self._run_git(["add"] + list(code_changes.keys()))
            result = self._run_git([
                "-c", "commit.gpgsign=false",
                "commit", "-m",
                f"R-F{r_number}: {summary}\n\nAutonomously generated by ARIA-Coder",
                "--author", "ARIA-Coder <aria@arkmurus.com>",
            ])
            return result.returncode == 0
        except Exception as e:
            logger.error("[fly_deployer] git commit failed: %s", e)
            return False

    def _run_git(self, args: list[str]) -> subprocess.CompletedProcess:
        # R-F1342: bound every git op. Previously NO timeout → a hung git
        # (network, lock, prompt) blocked the caller forever; on the event
        # loop that is a blackout. TimeoutExpired is caught by callers.
        return subprocess.run(  # noqa: S603 — controlled args, repo path
            ["git"] + args,
            capture_output=True, text=True,
            cwd=str(self.repo_path), check=False,
            timeout=_GIT_TIMEOUT_S,
        )

    # ── FLY BUILD + DEPLOY ───────────────────────────────────────────────────

    async def _fly_build(self, app: str, branch: str) -> Optional[str]:
        try:
            # R-F1342: 300s build run off the event loop (was blocking it).
            result = await asyncio.to_thread(
                subprocess.run,  # noqa: S603
                [
                    "fly", "deploy", "--build-only",
                    "--app", app,
                    "--image-label", f"auto-{branch.replace('/', '-')}",
                ],
                capture_output=True, text=True, timeout=300, check=False,
            )
            if result.returncode != 0:
                logger.error("[fly_deployer] fly build failed: %s", result.stderr)
                return None
            m = re.search(r"(registry\.fly\.io/[^\s]+)", result.stdout)
            return m.group(1) if m else None
        except Exception as e:
            logger.error("[fly_deployer] fly_build error: %s", e)
            return None

    async def _launch_canary(self, app: str, image: str) -> Optional[str]:
        """Launch a single canary machine via the Machines API."""
        try:
            resp = await self._client.post(
                f"{FLY_API_BASE}/apps/{app}/machines",
                json={
                    "config": {
                        "image": image,
                        "env": {"ARIA_CANARY": "1"},
                        "restart": {"policy": "no"},
                        "auto_destroy": True,
                    }
                },
            )
            if resp.status_code == 200:
                return resp.json().get("id")
            logger.error(
                "[fly_deployer] canary launch %d: %s",
                resp.status_code, resp.text[:300],
            )
        except Exception as e:
            logger.error("[fly_deployer] canary launch failed: %s", e)
        return None

    async def _smoke_test_canary(self, app: str, machine_id: str) -> bool:
        """Hit the canary's private DNS (<machine-id>.vm.<app>.internal)."""
        canary_url = f"http://{machine_id}.vm.{app}.internal:8000/api/aria/health/live"
        try:
            resp = await self._client.get(canary_url, timeout=SMOKE_TIMEOUT_S)
            return resp.status_code == 200
        except Exception as e:
            logger.error("[fly_deployer] smoke test failed: %s", e)
            return False

    def _deploy_fleet(self, app: str, image: str) -> bool:
        try:
            result = subprocess.run(  # noqa: S603
                [
                    "fly", "deploy",
                    "--app", app,
                    "--image", image,
                    "--strategy", "bluegreen",
                ],
                capture_output=True, text=True, timeout=300, check=False,
            )
            return result.returncode == 0
        except Exception as e:
            logger.error("[fly_deployer] fleet deploy failed: %s", e)
            return False

    async def _destroy_machine(self, app: str, machine_id: str) -> None:
        try:
            await self._client.delete(
                f"{FLY_API_BASE}/apps/{app}/machines/{machine_id}"
            )
        except Exception as e:
            # R-F1614 make-loud: a swallowed canary cleanup failure leaves an
            # orphaned Fly machine RUNNING (burning $) while the deploy reports
            # success — wire it so the brain/coder can reconcile orphans.
            logger.error(
                "[fly_deployer] _destroy_machine failed — ORPHANED machine %s on %s may keep burning $: %s",
                machine_id, app, e,
            )
            try:
                from aria_service.intel.engine_wiring import wire_failure as _wf_dm
                _wf_dm(
                    module="fly_deployer._destroy_machine",
                    detail=f"orphaned Fly machine {machine_id} on {app} not destroyed (deploy reported success): {e}",
                    gap_type="orphaned_machine",
                    source="fly_deployer",
                )
            except Exception:
                pass

    # ── PR vs DIRECT ─────────────────────────────────────────────────────────

    async def _open_pr_or_merge(
        self,
        branch: str,
        r_number: int,
        summary: str,
        auto_merge: bool,
    ) -> Optional[str]:
        """Push branch to origin, then open PR OR squash-merge to main."""
        # R-F1342: all git is blocking I/O — run the whole sequence off the
        # event loop in one thread hop so it never freezes the loop.
        def _push_and_maybe_merge() -> None:
            self._run_git(["push", "--set-upstream", "origin", branch])
            if auto_merge:
                # Direct path — squash to main locally + push (R-F462 risk path)
                self._run_git(["checkout", "main"])
                self._run_git(["merge", "--squash", branch])
                self._run_git([
                    "-c", "commit.gpgsign=false",
                    "commit", "-m", f"R-F{r_number}: {summary} [auto-merged]",
                ])
                self._run_git(["push", "origin", "main"])
                self._run_git(["branch", "-D", branch])

        await asyncio.to_thread(_push_and_maybe_merge)
        if auto_merge:
            return None

        # PR path — open a draft PR for review (Claude review hook is R-F805)
        try:
            # R-F1342: gh subprocess off the event loop.
            result = await asyncio.to_thread(
                subprocess.run,  # noqa: S603
                [
                    "gh", "pr", "create",
                    "--title", f"R-F{r_number}: {summary}",
                    "--body",
                    f"Autonomously generated by ARIA-Coder for R-F{r_number}.\n\n"
                    f"Verify before merging. Per R-F462 self-written code "
                    f"requires operator review until Claude review hook (R-F805) "
                    f"is shipped and proven.",
                    "--draft",
                    "--base", "main",
                ],
                capture_output=True, text=True, timeout=60,
                cwd=str(self.repo_path), check=False,
            )
            if result.returncode == 0:
                m = re.search(r"(https://github\.com/\S+/pull/\d+)", result.stdout)
                return m.group(1) if m else result.stdout.strip()
        except Exception as e:
            logger.warning(
                "[fly_deployer] gh pr create failed (non-fatal): %s", e,
            )
        return None

    # ── DEPLOY HISTORY ───────────────────────────────────────────────────────

    async def _record_deploy(
        self, app: str, r_number: int, image: str, pr_url: Optional[str],
    ) -> None:
        record = json.dumps({
            "app": app,
            "r_number": r_number,
            "image": image,
            "pr_url": pr_url,
            "ts": datetime.now(timezone.utc).isoformat(),
        })
        try:
            await self.redis.set(f"{DEPLOY_KEY_PREFIX}{r_number}", record)
            await self.redis.lpush(DEPLOY_HISTORY_KEY, record)
            await self.redis.ltrim(DEPLOY_HISTORY_KEY, 0, DEPLOY_HISTORY_MAX - 1)
        except Exception as e:
            logger.warning("[fly_deployer] record_deploy failed: %s", e)
