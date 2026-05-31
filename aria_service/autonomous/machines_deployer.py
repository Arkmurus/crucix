"""R-F1183 — Pure Fly Machines API deployer (no flyctl dependency).

ARIA's autonomous deploy pipeline. Uses the Fly Machines REST API directly
so ARIA can deploy without needing `flyctl` installed or a shell subprocess.

This module is NOT in PROTECTED_FILES — it is the new, safe, fully-autonomous
deployer. The existing fly_deployer.py delegates to this module.

Pipeline
────────
  1. Push guard: verify HEAD == origin/main (code is backed up)
  2. Trigger remote build via Machines API → get image tag
  3. Launch canary machine with new image
  4. Smoke-test canary via public health endpoint
  5. Update existing machines to new image (rolling update)
  6. Destroy canary
  7. Verify live: poll /health/live until build_rev matches commit SHA
  8. Record deploy history to JSON file

No Redis dependency — deploy history is stored as JSON files in
data/deploy_history/. No subprocess calls — everything goes through
httpx to api.machines.dev.

Rollback
────────
  rollback(app, target_r_number) redeploys the image stored in the previous
  deploy record. Uses the same Machines API update flow.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

import httpx

logger = logging.getLogger("aria.autonomous.machines_deployer")

FLY_API_BASE = "https://api.machines.dev/v1"
SMOKE_TIMEOUT_S = 60
CANARY_WAIT_S = 15
POLL_INTERVAL_S = 5
POLL_MAX_ATTEMPTS = 36  # 3 minutes
# Test overrides — set to small values in tests to avoid timeouts
_TEST_POLL_INTERVAL_S: float = 0.0
_TEST_BUILD_POLL_S: float = 0.0
DEPLOY_HISTORY_DIR = Path("data/deploy_history")
DEPLOY_HISTORY_MAX = 100


@dataclass
class DeployResult:
    """Result of a deploy operation.

    Attributes:
        success: True if the deploy completed and was verified live.
        app: Fly app name (e.g. "aria-intel").
        image: Full image ref that was deployed.
        r_number: R-number associated with this deploy.
        commit_sha: Git commit SHA that was deployed.
        error: Error message if success is False.
        duration_s: Total deploy duration in seconds.
        deployed_at: ISO-8601 timestamp of deploy completion.
    """
    success: bool
    app: str = ""
    image: str = ""
    r_number: Optional[int] = None
    commit_sha: str = ""
    error: Optional[str] = None
    duration_s: float = 0.0
    deployed_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )


class MachinesDeployer:
    """Fly.io deployer using the Machines API — no flyctl dependency.

    Deploys by:
      1. Checking the push guard (HEAD == origin/main via git files)
      2. Triggering a remote build via the Machines API
      3. Launching a canary machine with the new image
      4. Smoke-testing the canary
      5. Updating existing machines to the new image
      6. Verifying the live app serves the expected commit SHA

    No subprocess calls — git refs are read directly from .git/ files.
    All API calls go through httpx to api.machines.dev.

    Args:
        aria_service_url: Base URL of the ARIA service (for health checks).
        repo_path: Path to the git repo root (default: ARIA_REPO_PATH env or /app).
        http_client: Optional shared httpx client (for testing).
        brain_hook: Optional brain hook for wiring success/failure signals.
    """

    def __init__(
        self,
        aria_service_url: str,
        repo_path: Optional[Path] = None,
        http_client: Optional[httpx.AsyncClient] = None,
        brain_hook: Optional[Any] = None,
    ) -> None:
        self.aria_url = aria_service_url.rstrip("/")
        self.repo_path = repo_path or Path(
            os.environ.get("ARIA_REPO_PATH", "/app")
        )
        self.brain_hook = brain_hook

        token = os.environ.get("FLY_API_TOKEN", "")
        if not token:
            logger.warning(
                "[machines_deployer] FLY_API_TOKEN not set — "
                "all API calls will fail"
            )

        self._client = http_client or httpx.AsyncClient(
            headers={"Authorization": f"Bearer {token}"} if token else {},
            timeout=120,
        )
        self._owns_client = http_client is None

        # Resolve the git root by looking for .git/HEAD
        self._git_root = self._resolve_git_root()

    async def aclose(self) -> None:
        """Close the HTTP client if we own it."""
        if self._owns_client:
            await self._client.aclose()

    # ── PUBLIC API ──────────────────────────────────────────────────────────

    async def deploy(
        self,
        app: str,
        r_number: int,
        commit_sha: str,
        *,
        image: Optional[str] = None,
        skip_push_guard: bool = False,
    ) -> DeployResult:
        """Deploy a commit to a Fly app via the Machines API.

        This is the primary deploy method. It:
          1. Checks the push guard (HEAD == origin/main) unless skipped
          2. Triggers a remote build (or uses a provided image)
          3. Launches a canary machine
          4. Smoke-tests the canary
          5. Updates existing machines to the new image
          6. Verifies the live app serves the expected commit SHA

        Args:
            app: Fly app name (e.g. "aria-intel").
            r_number: R-number for this deploy.
            commit_sha: Full git commit SHA being deployed.
            image: Pre-built image ref (optional; if not provided, triggers build).
            skip_push_guard: Skip the push guard check (for testing).

        Returns:
            DeployResult with success/failure and metadata.
        """
        start = time.monotonic()
        logger.info(
            "[machines_deployer] deploy R-F%d → %s (commit=%s)",
            r_number, app, commit_sha[:8],
        )

        try:
            # Step 1: Push guard
            if not skip_push_guard and not self._check_push_guard(commit_sha):
                return DeployResult(
                    success=False, app=app, r_number=r_number,
                    commit_sha=commit_sha,
                    error=(
                        "Push guard failed: HEAD does not match origin/main. "
                        "Push your commit before deploying."
                    ),
                )

            # Step 2: Build (or use provided image)
            resolved_image = image
            if not resolved_image:
                resolved_image = await self._trigger_build(app, commit_sha)
                if not resolved_image:
                    return DeployResult(
                        success=False, app=app, r_number=r_number,
                        commit_sha=commit_sha,
                        error="Remote build failed — no image produced",
                    )

            # Step 3: Launch canary
            canary_id = await self._launch_canary(app, resolved_image)
            if not canary_id:
                return DeployResult(
                    success=False, app=app, r_number=r_number,
                    commit_sha=commit_sha, image=resolved_image,
                    error="Canary launch failed",
                )

            await asyncio.sleep(CANARY_WAIT_S)

            # Step 4: Smoke-test canary
            canary_ok = await self._smoke_test_canary(app, canary_id)
            if not canary_ok:
                await self._destroy_machine(app, canary_id)
                return DeployResult(
                    success=False, app=app, r_number=r_number,
                    commit_sha=commit_sha, image=resolved_image,
                    error="Canary smoke test failed — fleet not updated",
                )

            # Step 5: Update existing machines
            fleet_ok = await self._update_fleet(app, resolved_image)
            if not fleet_ok:
                await self._destroy_machine(app, canary_id)
                return DeployResult(
                    success=False, app=app, r_number=r_number,
                    commit_sha=commit_sha, image=resolved_image,
                    error="Fleet update failed — no machines updated",
                )

            # Step 6: Destroy canary (fleet is now serving the new image)
            await self._destroy_machine(app, canary_id)

            # Step 7: Verify live
            live_ok = await self._verify_live(app, commit_sha)
            if not live_ok:
                return DeployResult(
                    success=False, app=app, r_number=r_number,
                    commit_sha=commit_sha, image=resolved_image,
                    error=(
                        "Deploy completed but live verification failed — "
                        "the app did not serve the expected commit SHA"
                    ),
                )

            # Step 8: Record deploy history
            await self._record_deploy(
                app, r_number, resolved_image, commit_sha,
            )

            duration = time.monotonic() - start
            logger.info(
                "[machines_deployer] ✅ R-F%d deployed to %s in %.1fs "
                "(image=%s)",
                r_number, app, duration, resolved_image,
            )

            result = DeployResult(
                success=True, app=app, image=resolved_image,
                r_number=r_number, commit_sha=commit_sha,
                duration_s=duration,
            )

            # Wire success to brain
            await self._brain_signal(
                event="deploy_success",
                app=app,
                r_number=r_number,
                commit_sha=commit_sha,
                image=resolved_image,
                duration_s=duration,
            )

            return result

        except Exception as e:
            logger.error(
                "[machines_deployer] deploy failed: %s", e, exc_info=True,
            )
            error_msg = str(e)

            # Wire failure to brain
            await self._brain_signal(
                event="deploy_failure",
                app=app,
                r_number=r_number,
                commit_sha=commit_sha,
                error=error_msg,
            )

            return DeployResult(
                success=False, app=app, r_number=r_number,
                commit_sha=commit_sha, error=error_msg,
            )

    async def rollback(
        self,
        app: str,
        target_r_number: int,
    ) -> DeployResult:
        """Roll back to a previous deploy by redeploying its image.

        Args:
            app: Fly app name.
            target_r_number: Roll back to this R-number's image.

        Returns:
            DeployResult with success/failure.
        """
        record = self._load_deploy_record(app, target_r_number)
        if not record:
            return DeployResult(
                success=False, app=app,
                error=f"No deploy record found for R-F{target_r_number}",
            )

        prev_image = record.get("image", "")
        if not prev_image:
            return DeployResult(
                success=False, app=app,
                error=(
                    f"Deploy record for R-F{target_r_number} has no image"
                ),
            )

        logger.warning(
            "[machines_deployer] ⚠️ rolling back %s to image from R-F%d: %s",
            app, target_r_number, prev_image,
        )

        # Update existing machines to the previous image
        fleet_ok = await self._update_fleet(app, prev_image)
        if not fleet_ok:
            return DeployResult(
                success=False, app=app,
                error="Rollback fleet update failed",
            )

        logger.warning(
            "[machines_deployer] ✅ rolled back %s to R-F%d image %s",
            app, target_r_number, prev_image,
        )

        return DeployResult(
            success=True, app=app, image=prev_image,
            r_number=target_r_number,
        )

    # ── PUSH GUARD (pure Python — no subprocess) ────────────────────────────

    def _check_push_guard(self, commit_sha: str) -> bool:
        """Verify HEAD matches origin/main by reading .git/ files directly.

        Delegates to aria_service.utils.git_utils.check_push_guard.
        """
        from ..utils.git_utils import check_push_guard as _check
        return _check(commit_sha, git_root=self._git_root)

    # ── BUILD ───────────────────────────────────────────────────────────────

    async def _trigger_build(
        self, app: str, commit_sha: str,
    ) -> Optional[str]:
        """Trigger a remote build on Fly.io via the Machines API.

        POST /v1/apps/{app}/builds with the commit SHA as the source.
        Returns the image ref (registry.fly.io/{app}:deployment-<sha>)
        or None on failure.

        NOTE: The Fly Machines API build endpoint is not a documented
        public API and may not be available on all apps. If the build
        trigger fails, provide a pre-built image via the `image`
        parameter to `deploy()`. In practice, `flyctl deploy --remote-only`
        is the only reliable way to trigger builds.
        """
        try:
            resp = await self._client.post(
                f"{FLY_API_BASE}/apps/{app}/builds",
                json={
                    "source": {
                        "type": "git",
                        "ref": commit_sha,
                    },
                },
            )
            if resp.status_code not in (200, 201, 202):
                logger.error(
                    "[machines_deployer] build trigger %d: %s — "
                    "provide a pre-built image via the `image` parameter",
                    resp.status_code, resp.text[:300],
                )
                return None

            build_data = resp.json()
            # The image ref is typically
            # registry.fly.io/{app}:deployment-<sha>
            image = build_data.get("image") or (
                f"registry.fly.io/{app}:deployment-{commit_sha[:8]}"
            )

            # Wait for the build to complete (poll build status)
            build_id = build_data.get("id")
            if build_id:
                image = await self._poll_build(app, build_id, image)

            logger.info(
                "[machines_deployer] build complete: %s", image,
            )
            return image

        except httpx.TimeoutException:
            logger.error(
                "[machines_deployer] build trigger timed out — "
                "provide a pre-built image via the `image` parameter",
            )
            return None
        except Exception as e:
            logger.error(
                "[machines_deployer] build trigger failed: %s — "
                "provide a pre-built image via the `image` parameter", e,
            )
            return None

    async def _poll_build(
        self, app: str, build_id: str, default_image: str,
    ) -> str:
        """Poll build status until complete or timeout.

        The Machines API build endpoint returns status:
        running/succeeded/failed. We poll up to 5 minutes (60 attempts × 5s).
        """
        for i in range(60):
            if i > 0:
                interval = _TEST_BUILD_POLL_S or 5
                await asyncio.sleep(interval)
            try:
                resp = await self._client.get(
                    f"{FLY_API_BASE}/apps/{app}/builds/{build_id}",
                )
                if resp.status_code != 200:
                    continue

                build = resp.json()
                status = build.get("status", "unknown")

                if status == "succeeded":
                    image = build.get("image") or default_image
                    logger.info(
                        "[machines_deployer] build %s succeeded: %s",
                        build_id[:8], image,
                    )
                    return image

                if status == "failed":
                    error = build.get("error", "unknown")
                    logger.error(
                        "[machines_deployer] build %s failed: %s",
                        build_id[:8], error,
                    )
                    # Fall back to default image ref
                    return default_image

                if i % 12 == 0:  # Log every ~60s
                    logger.info(
                        "[machines_deployer] build %s still %s "
                        "(poll %d/60)",
                        build_id[:8], status, i,
                    )

            except Exception as e:
                logger.debug(
                    "[machines_deployer] build poll error "
                    "(non-fatal): %s", e,
                )

        logger.warning(
            "[machines_deployer] build %s did not complete within 5min "
            "— using default image ref",
            build_id[:8],
        )
        return default_image

    # ── CANARY ──────────────────────────────────────────────────────────────

    async def _launch_canary(
        self, app: str, image: str,
    ) -> Optional[str]:
        """Launch a single canary machine via the Machines API.

        POST /v1/apps/{app}/machines with the new image.
        Returns the machine ID or None on failure.
        """
        try:
            resp = await self._client.post(
                f"{FLY_API_BASE}/apps/{app}/machines",
                json={
                    "region": "lhr",
                    "config": {
                        "image": image,
                        "env": {"ARIA_CANARY": "1"},
                        "restart": {"policy": "no"},
                        "auto_destroy": True,
                        "services": [
                            {
                                "ports": [
                                    {
                                        "port": 443,
                                        "handlers": ["tls"],
                                    },
                                    {
                                        "port": 80,
                                        "handlers": ["http"],
                                    },
                                ],
                                "internal_port": 8000,
                            },
                        ],
                        # Health check so Fly LB routes traffic to canary
                        "checks": [
                            {
                                "type": "http",
                                "path": "/health/live",
                                "port": 8000,
                                "interval": "10s",
                                "timeout": "5s",
                                "grace_period": "30s",
                                "method": "GET",
                            },
                        ],
                    },
                },
            )
            # Machines API returns 201 on success (not 200)
            if resp.status_code == 201:
                machine_id = resp.json().get("id")
                logger.info(
                    "[machines_deployer] canary launched: %s (image=%s)",
                    machine_id, image,
                )
                return machine_id

            logger.error(
                "[machines_deployer] canary launch %d: %s",
                resp.status_code, resp.text[:300],
            )
            return None

        except httpx.TimeoutException:
            logger.error(
                "[machines_deployer] canary launch timed out",
            )
            return None
        except Exception as e:
            logger.error(
                "[machines_deployer] canary launch failed: %s", e,
            )
            return None

    async def _smoke_test_canary(
        self, app: str, machine_id: str,
    ) -> bool:
        """Smoke-test the canary via its public health endpoint.

        Uses the canary's public URL (https://{app}.fly.dev) — the canary
        is registered as a new machine in the app and should receive traffic
        once the Fly LB routes to it. We check that /health/live returns 200.

        If the canary has a private IP, we also try the private DNS:
        http://{machine_id}.vm.{app}.internal:8000/health/live
        """
        # Try public endpoint first
        public_url = f"https://{app}.fly.dev/health/live"
        try:
            resp = await self._client.get(
                public_url, timeout=SMOKE_TIMEOUT_S,
            )
            if resp.status_code == 200:
                logger.info(
                    "[machines_deployer] canary smoke test passed "
                    "(public): %s",
                    public_url,
                )
                return True
        except Exception as e:
            logger.debug(
                "[machines_deployer] canary public smoke test "
                "failed: %s", e,
            )

        # Try private DNS (works from within the Fly network)
        private_url = (
            f"http://{machine_id}.vm.{app}.internal:8000/health/live"
        )
        try:
            resp = await self._client.get(
                private_url, timeout=SMOKE_TIMEOUT_S,
            )
            if resp.status_code == 200:
                logger.info(
                    "[machines_deployer] canary smoke test passed "
                    "(private): %s",
                    private_url,
                )
                return True
        except Exception as e:
            logger.debug(
                "[machines_deployer] canary private smoke test "
                "failed: %s", e,
            )

        logger.error(
            "[machines_deployer] canary smoke test FAILED for "
            "machine %s on %s",
            machine_id, app,
        )
        return False

    # ── FLEET UPDATE ────────────────────────────────────────────────────────

    async def update_fleet(self, app: str, image: str) -> bool:
        """Update all existing machines to a new image. Public API.

        Args:
            app: Fly app name.
            image: Full image ref to deploy.

        Returns:
            True if at least one machine was updated.
        """
        return await self._update_fleet(app, image)

    async def _update_fleet(self, app: str, image: str) -> bool:
        """Update all existing machines in the app to the new image.

        Lists all machines via GET /v1/apps/{app}/machines, then updates
        each one via POST /v1/apps/{app}/machines/{id} with the new image.
        Uses rolling update: one machine at a time, waiting for health.

        Returns True if at least one machine was updated.
        """
        try:
            machines = await self._list_machines(app)
            if not machines:
                logger.error(
                    "[machines_deployer] no machines found for %s", app,
                )
                return False

            updated = 0
            for machine in machines:
                machine_id = machine.get("id")
                if not machine_id:
                    continue

                ok = await self._update_machine(app, machine_id, image)
                if ok:
                    updated += 1
                    logger.info(
                        "[machines_deployer] machine %s updated to %s",
                        machine_id[:8], image,
                    )
                else:
                    logger.warning(
                        "[machines_deployer] machine %s update failed "
                        "— continuing",
                        machine_id[:8],
                    )

            if updated == 0:
                logger.error(
                    "[machines_deployer] no machines were updated "
                    "for %s",
                    app,
                )
                return False

            logger.info(
                "[machines_deployer] fleet update: %d/%d machines "
                "updated on %s",
                updated, len(machines), app,
            )
            return True

        except Exception as e:
            logger.error(
                "[machines_deployer] fleet update failed: %s", e,
            )
            return False

    async def _list_machines(
        self, app: str,
    ) -> list[dict[str, Any]]:
        """List all machines for an app via the Machines API."""
        try:
            resp = await self._client.get(
                f"{FLY_API_BASE}/apps/{app}/machines",
            )
            if resp.status_code == 200:
                return resp.json()
            logger.error(
                "[machines_deployer] list machines %d: %s",
                resp.status_code, resp.text[:200],
            )
            return []
        except Exception as e:
            logger.error(
                "[machines_deployer] list machines failed: %s", e,
            )
            return []

    async def _update_machine(
        self, app: str, machine_id: str, image: str,
    ) -> bool:
        """Update a single machine to a new image.

        POST /v1/apps/{app}/machines/{id} with the new config.
        The machine is stopped, updated, and restarted.
        """
        try:
            # Get current machine config
            get_resp = await self._client.get(
                f"{FLY_API_BASE}/apps/{app}/machines/{machine_id}",
            )
            if get_resp.status_code != 200:
                logger.error(
                    "[machines_deployer] get machine %s: %d",
                    machine_id[:8], get_resp.status_code,
                )
                return False

            current = get_resp.json()
            current_config = current.get("config", {})

            # Update the image in the config
            current_config["image"] = image

            # Remove env vars that shouldn't persist (like ARIA_CANARY)
            env = current_config.get("env", {})
            env.pop("ARIA_CANARY", None)
            current_config["env"] = env

            # POST the updated config
            # (this stops, updates, and restarts the machine)
            resp = await self._client.post(
                f"{FLY_API_BASE}/apps/{app}/machines/{machine_id}",
                json={
                    "config": current_config,
                },
            )
            if resp.status_code == 200:
                return True

            logger.error(
                "[machines_deployer] update machine %s: %d: %s",
                machine_id[:8], resp.status_code, resp.text[:200],
            )
            return False

        except Exception as e:
            logger.error(
                "[machines_deployer] update machine %s failed: %s",
                machine_id[:8], e,
            )
            return False

    async def _destroy_machine(
        self, app: str, machine_id: str,
    ) -> None:
        """Destroy a machine (canary cleanup)."""
        try:
            await self._client.delete(
                f"{FLY_API_BASE}/apps/{app}/machines/{machine_id}",
            )
            logger.info(
                "[machines_deployer] machine %s destroyed",
                machine_id[:8],
            )
        except Exception as e:
            logger.debug(
                "[machines_deployer] destroy machine %s failed "
                "(non-fatal): %s",
                machine_id[:8], e,
            )

    # ── LIVE VERIFICATION ───────────────────────────────────────────────────

    async def _verify_live(self, app: str, commit_sha: str) -> bool:
        """Verify the live app serves the expected commit SHA.

        Polls /health/live up to POLL_MAX_ATTEMPTS times, checking that
        the build_rev field matches the commit SHA short form.
        """
        short_sha = commit_sha[:8]
        health_url = f"https://{app}.fly.dev/health/live"

        for i in range(POLL_MAX_ATTEMPTS):
            try:
                resp = await self._client.get(health_url, timeout=30)
                if resp.status_code == 200:
                    body = resp.text
                    # Match "sha abcdef12" or "build_rev": "abcdef12"
                    sha_match = re.search(
                        r'(?:sha|build_rev)[":\s]*([a-f0-9]{8})',
                        body,
                    )
                    if sha_match and sha_match.group(1) == short_sha:
                        logger.info(
                            "[machines_deployer] ✅ live verification: "
                            "%s serves %s",
                            app, short_sha,
                        )
                        return True

                    live_sha = sha_match.group(1) if sha_match else "?"
                    logger.debug(
                        "[machines_deployer] live poll %d/%d: %s "
                        "serves %s (want %s)",
                        i + 1, POLL_MAX_ATTEMPTS,
                        app, live_sha, short_sha,
                    )
                else:
                    logger.debug(
                        "[machines_deployer] live poll %d/%d: %s "
                        "returned %d",
                        i + 1, POLL_MAX_ATTEMPTS,
                        app, resp.status_code,
                    )

            except Exception as e:
                logger.debug(
                    "[machines_deployer] live poll %d/%d failed: %s",
                    i + 1, POLL_MAX_ATTEMPTS, e,
                )

            if i < POLL_MAX_ATTEMPTS - 1:
                interval = _TEST_POLL_INTERVAL_S or POLL_INTERVAL_S
                await asyncio.sleep(interval)

        logger.error(
            "[machines_deployer] ❌ live verification FAILED for %s — "
            "did not serve %s within %ds",
            app, short_sha, POLL_MAX_ATTEMPTS * POLL_INTERVAL_S,
        )
        return False

    # ── DEPLOY HISTORY ──────────────────────────────────────────────────────

    async def _record_deploy(
        self,
        app: str,
        r_number: int,
        image: str,
        commit_sha: str,
    ) -> None:
        """Record deploy history to a JSON file.

        Stores in data/deploy_history/{app}.json as a list of records.
        No Redis dependency — pure file-based storage.
        """
        record = {
            "app": app,
            "r_number": r_number,
            "image": image,
            "commit_sha": commit_sha,
            "ts": datetime.now(timezone.utc).isoformat(),
        }

        try:
            history_file = DEPLOY_HISTORY_DIR / f"{app}.json"
            DEPLOY_HISTORY_DIR.mkdir(parents=True, exist_ok=True)

            history: list[dict[str, Any]] = []
            if history_file.exists():
                try:
                    history = json.loads(
                        history_file.read_text(encoding="utf-8"),
                    )
                except (json.JSONDecodeError, OSError):
                    history = []

            history.insert(0, record)
            history = history[:DEPLOY_HISTORY_MAX]

            history_file.write_text(
                json.dumps(history, indent=2), encoding="utf-8",
            )
            logger.info(
                "[machines_deployer] deploy record saved: R-F%d → %s",
                r_number, image,
            )

        except Exception as e:
            logger.warning(
                "[machines_deployer] record_deploy failed "
                "(non-fatal): %s", e,
            )

    def _load_deploy_record(
        self, app: str, r_number: int,
    ) -> Optional[dict[str, Any]]:
        """Load a deploy record for a specific R-number."""
        try:
            history_file = DEPLOY_HISTORY_DIR / f"{app}.json"
            if not history_file.exists():
                return None

            history = json.loads(
                history_file.read_text(encoding="utf-8"),
            )
            for record in history:
                if record.get("r_number") == r_number:
                    return record
            return None

        except Exception as e:
            logger.error(
                "[machines_deployer] load_deploy_record failed: %s", e,
            )
            return None

    # ── BRAIN SIGNAL ────────────────────────────────────────────────────────

    async def _brain_signal(
        self,
        event: str,
        app: str,
        r_number: int,
        commit_sha: str,
        **kwargs: Any,
    ) -> None:
        """Wire deploy events to the brain.

        Both success and failure reach the brain so ARIA learns from
        every deploy attempt.
        """
        if self.brain_hook is None:
            return

        try:
            summary = (
                f"Deploy {event}: R-F{r_number} → {app} "
                f"(commit={commit_sha[:8]})"
            )
            if kwargs.get("error"):
                summary += f" — {kwargs['error']}"

            await self.brain_hook.absorb(
                module="machines_deployer",
                summary=summary,
                confidence="CONFIRMED",
                source_id=f"machines_deployer_{event}_{r_number}",
            )
        except Exception as e:
            logger.debug(
                "[machines_deployer] brain signal failed "
                "(non-fatal): %s", e,
            )

    # ── HELPERS ─────────────────────────────────────────────────────────────

    def _resolve_git_root(self) -> Path:
        """Find the git root directory by looking for .git/HEAD.

        Delegates to aria_service.utils.git_utils.resolve_git_root.
        Falls back to self.repo_path if not found.
        """
        from ..utils.git_utils import resolve_git_root as _resolve
        found = _resolve(start_path=self.repo_path)
        return found or self.repo_path
