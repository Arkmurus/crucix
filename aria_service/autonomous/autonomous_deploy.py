"""R-F1185 — ARIA Autonomous Deployment Engine.

Merged design: your architecture (SQLite DB, webhooks, blockchain stub,
FastAPI endpoints, DeploymentStatus enum) on top of the correct Machines
API client (MachinesDeployer) with push guard, no subprocess, async.

Features:
  - Self-deployment with commit tracking
  - Automatic verification of live deployments
  - Health check monitoring with auto-rollback
  - Automatic rollback on failure
  - Blockchain anchoring for immutable proof (stub)
  - Webhook notifications
  - Full deployment history (SQLite)
  - FastAPI endpoints for status/trigger/rollback/history
  - Push guard (HEAD must match origin/main)
  - No subprocess calls (constitutional validator compliant)
  - No Redis dependency

Usage:
    from aria_service.autonomous.autonomous_deploy import (
        AutonomousDeployEngine, add_deployment_endpoints
    )

    engine = AutonomousDeployEngine()
    add_deployment_endpoints(app)

    # In lifespan:
    task = asyncio.create_task(engine.health_loop_async())
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import sqlite3
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Optional

import httpx

from .machines_deployer import MachinesDeployer
from ..intel.wire import fail_wire  # R-F1789 §21 brain-wiring

logger = logging.getLogger("aria.autonomous.autonomous_deploy")

# R-F1320: wire module health to the brain
try:
    from aria_service.intel.engine_wiring import wire_success as _ws1320
    _ws1320(
        module="autonomous.autonomous_deploy",
        summary="Autonomous Deploy active",
        source_id="autonomous:autonomous_deploy:R-F1320",
    )
except Exception:
    pass

# Default paths
DEFAULT_DB_PATH = Path("/app/data/deployments.db")
DEFAULT_HEALTH_INTERVAL_S = 60


# ── Configuration ──────────────────────────────────────────────────────────

@dataclass
class DeployConfig:
    """Deployment configuration loaded from environment variables."""
    app_name: str = "aria-intel"
    fly_api_token: str = ""
    webhook_url: str = ""
    health_check_path: str = "/health/live"
    health_check_timeout: int = 60
    deploy_timeout: int = 300
    rollback_on_failure: bool = True
    max_retries: int = 3
    blockchain_enabled: bool = False
    blockchain_rpc_url: str = ""
    blockchain_private_key: str = ""
    blockchain_contract_address: str = ""
    db_path: Path = DEFAULT_DB_PATH

    @classmethod
    def from_env(cls) -> DeployConfig:
        """Load configuration from environment variables."""
        return cls(
            app_name=os.environ.get("FLY_APP_NAME", "aria-intel"),
            fly_api_token=os.environ.get("FLY_API_TOKEN", ""),
            webhook_url=os.environ.get("DEPLOY_WEBHOOK_URL", ""),
            health_check_path=os.environ.get(
                "HEALTH_CHECK_PATH", "/health/live",
            ),
            health_check_timeout=int(
                os.environ.get("HEALTH_CHECK_TIMEOUT", "60"),
            ),
            deploy_timeout=int(os.environ.get("DEPLOY_TIMEOUT", "300")),
            rollback_on_failure=os.environ.get(
                "ROLLBACK_ON_FAILURE", "true",
            ).lower() == "true",
            max_retries=int(os.environ.get("MAX_RETRIES", "3")),
            blockchain_enabled=os.environ.get(
                "BLOCKCHAIN_ENABLED", "false",
            ).lower() == "true",
            blockchain_rpc_url=os.environ.get("BLOCKCHAIN_RPC_URL", ""),
            blockchain_private_key=os.environ.get(
                "BLOCKCHAIN_PRIVATE_KEY", "",
            ),
            blockchain_contract_address=os.environ.get(
                "BLOCKCHAIN_CONTRACT_ADDRESS", "",
            ),
            db_path=Path(
                os.environ.get("DEPLOY_DB_PATH", "/app/data/deployments.db"),
            ),
        )


# ── Deployment State ───────────────────────────────────────────────────────

class DeploymentStatus(Enum):
    """Status of a deployment."""
    PENDING = "pending"
    DEPLOYING = "deploying"
    SUCCESS = "success"
    FAILED = "failed"


@dataclass
class DeploymentRecord:
    """Record of a single deployment."""
    id: str
    commit_hash: str
    commit_message: str
    image_tag: str
    status: DeploymentStatus
    started_at: datetime
    completed_at: Optional[datetime] = None
    duration_seconds: Optional[float] = None
    error_message: Optional[str] = None
    verification_hash: Optional[str] = None
    blockchain_tx: Optional[str] = None


@dataclass
class LiveVersion:
    """What's currently running on Fly.io."""
    commit_hash: str
    image_tag: str
    deployed_at: datetime
    release_version: int
    machine_count: int
    health_status: str


# ── Deployment Database (SQLite) ───────────────────────────────────────────

class DeploymentDatabase:
    """Persistent storage for deployment history using SQLite."""

    def __init__(self, db_path: Path = DEFAULT_DB_PATH) -> None:
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _init_db(self) -> None:
        with sqlite3.connect(str(self.db_path)) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS deployments (
                    id TEXT PRIMARY KEY,
                    commit_hash TEXT NOT NULL,
                    commit_message TEXT,
                    image_tag TEXT NOT NULL,
                    status TEXT NOT NULL,
                    started_at TEXT NOT NULL,
                    completed_at TEXT,
                    duration_seconds REAL,
                    error_message TEXT,
                    verification_hash TEXT,
                    blockchain_tx TEXT
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS version_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    commit_hash TEXT NOT NULL,
                    image_tag TEXT NOT NULL,
                    deployed_at TEXT NOT NULL,
                    release_version INTEGER,
                    health_status TEXT,
                    verified INTEGER DEFAULT 0
                )
            """)
            conn.commit()

    @fail_wire(module="autonomous_deploy", gap_type="agent_cycle_failure")
    def save_deployment(self, record: DeploymentRecord) -> None:
        with sqlite3.connect(str(self.db_path)) as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO deployments
                (id, commit_hash, commit_message, image_tag, status,
                 started_at, completed_at, duration_seconds, error_message,
                 verification_hash, blockchain_tx)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record.id,
                    record.commit_hash,
                    record.commit_message,
                    record.image_tag,
                    record.status.value,
                    record.started_at.isoformat(),
                    record.completed_at.isoformat()
                    if record.completed_at else None,
                    record.duration_seconds,
                    record.error_message,
                    record.verification_hash,
                    record.blockchain_tx,
                ),
            )
            conn.commit()

    @fail_wire(module="autonomous_deploy", gap_type="agent_cycle_failure")
    def get_deployment(
        self, deploy_id: str,
    ) -> Optional[DeploymentRecord]:
        with sqlite3.connect(str(self.db_path)) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.execute(
                "SELECT * FROM deployments WHERE id = ?", (deploy_id,),
            )
            row = cursor.fetchone()
            if row:
                return DeploymentRecord(
                    id=row["id"],
                    commit_hash=row["commit_hash"],
                    commit_message=row["commit_message"],
                    image_tag=row["image_tag"],
                    status=DeploymentStatus(row["status"]),
                    started_at=datetime.fromisoformat(row["started_at"]),
                    completed_at=(
                        datetime.fromisoformat(row["completed_at"])
                        if row["completed_at"] else None
                    ),
                    duration_seconds=row["duration_seconds"],
                    error_message=row["error_message"],
                    verification_hash=row["verification_hash"],
                    blockchain_tx=row["blockchain_tx"],
                )
            return None

    @fail_wire(module="autonomous_deploy", gap_type="agent_cycle_failure")
    def save_version_history(
        self, live: LiveVersion, verified: bool = True,
    ) -> None:
        with sqlite3.connect(str(self.db_path)) as conn:
            conn.execute(
                """
                INSERT INTO version_history
                (commit_hash, image_tag, deployed_at, release_version,
                 health_status, verified)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    live.commit_hash,
                    live.image_tag,
                    live.deployed_at.isoformat(),
                    live.release_version,
                    live.health_status,
                    1 if verified else 0,
                ),
            )
            conn.commit()

    @fail_wire(module="autonomous_deploy", gap_type="agent_cycle_failure")
    def get_deployment_history(
        self, limit: int = 20,
    ) -> list[dict[str, Any]]:
        with sqlite3.connect(str(self.db_path)) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.execute(
                """
                SELECT * FROM version_history
                ORDER BY deployed_at DESC
                LIMIT ?
                """,
                (limit,),
            )
            return [dict(row) for row in cursor.fetchall()]


# ── Webhook Notifier ───────────────────────────────────────────────────────

class WebhookNotifier:
    """Sends webhook notifications for deployment events."""

    def __init__(self, webhook_url: str = "") -> None:
        self.webhook_url = webhook_url
        self._client = httpx.AsyncClient(timeout=5)

    @fail_wire(module="autonomous_deploy", gap_type="agent_cycle_failure")
    async def aclose(self) -> None:
        await self._client.aclose()

    @fail_wire(module="autonomous_deploy", gap_type="agent_cycle_failure")
    async def send(
        self, event: str, data: dict[str, Any],
    ) -> None:
        """Send a webhook notification (fire-and-forget)."""
        if not self.webhook_url:
            return
        # R-F1869 (audit DD-08): webhook_url comes from DEPLOY_WEBHOOK_URL — a
        # poisoned env value (169.254.169.254 cloud-metadata, fdaa::1 / RFC-1918
        # internal services) would turn deploy notifications into an SSRF that
        # leaks operational state in the payload. Validate before POSTing.
        try:
            from ..intel.url_safety import is_safe_url_async
            ok, reason = await is_safe_url_async(self.webhook_url)
            if not ok:
                logger.warning("[webhook] refusing unsafe webhook_url: %s", reason)
                return
        except Exception as e:
            logger.warning("[webhook] url safety check failed, refusing: %s", e)
            return
        try:
            payload = {
                "event": event,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "data": data,
            }
            await self._client.post(
                self.webhook_url,
                json=payload,
            )
        except Exception as e:
            logger.debug(
                "[webhook] send failed (non-fatal): %s", e,
            )


# ── Blockchain Anchoring (Stub) ────────────────────────────────────────────

class BlockchainAnchoring:
    """Anchor deployment proofs to blockchain (stub).

    This is a forward-looking feature. When a smart contract is deployed,
    replace the mock with real web3 calls.
    """

    def __init__(
        self,
        rpc_url: str = "",
        private_key: str = "",
        contract_address: str = "",
    ) -> None:
        self.rpc_url = rpc_url
        self.private_key = private_key
        self.contract_address = contract_address
        self._web3_available = False
        self._account = None

        if rpc_url and private_key:
            try:
                from web3 import Web3
                w3 = Web3(Web3.HTTPProvider(rpc_url))
                if w3.is_connected():
                    self._account = w3.eth.account.from_key(private_key)
                    self._web3_available = True
            except ImportError:
                logger.info(
                    "[blockchain] web3 not installed — "
                    "blockchain anchoring disabled",
                )

    @fail_wire(module="autonomous_deploy", gap_type="agent_cycle_failure")
    def anchor_deployment(
        self,
        commit_hash: str,
        image_tag: str,
        verification_hash: str,
    ) -> Optional[str]:
        """Anchor deployment proof to blockchain.

        Returns a transaction hash string, or None if anchoring is
        not available. Currently returns a mock hash derived from
        the deployment data.
        """
        if not self._web3_available:
            # Return a deterministic mock hash for audit trail
            combined = f"{commit_hash}:{image_tag}:{verification_hash}"
            mock_tx = (
                f"0x{hashlib.sha256(combined.encode()).hexdigest()[:64]}"
            )
            logger.info(
                "[blockchain] mock anchor: %s... (web3 not connected)",
                mock_tx[:16],
            )
            return mock_tx

        # Real web3 anchoring — replace with smart contract call
        try:
            combined = f"{commit_hash}:{image_tag}:{verification_hash}"
            hash_bytes = hashlib.sha256(combined.encode()).digest()
            # R-F1220: smart contract integration deferred — no contract deployed yet.
            # The hash is computed and logged so it can be anchored retroactively.
            # When a contract is deployed, replace this comment with:
            #   await contract.recordDeployment(hash_bytes)
            tx_hash = f"0x{hash_bytes.hex()[:64]}"
            logger.info(
                "[blockchain] anchored: %s", tx_hash[:16],
            )
            return tx_hash
        except Exception as e:
            logger.error(
                "[blockchain] anchoring failed: %s", e,
            )
            return None


# ── Health Checker ─────────────────────────────────────────────────────────

class HealthChecker:
    """Monitors service health during deployment."""

    def __init__(
        self, app_url: str, health_path: str = "/health/live",
    ) -> None:
        self.app_url = app_url.rstrip("/")
        self.health_path = health_path
        self.full_url = f"{self.app_url}{health_path}"

    @fail_wire(module="autonomous_deploy", gap_type="agent_cycle_failure")
    async def check(
        self, timeout: int = 30,
    ) -> tuple[bool, dict[str, Any]]:
        """Check if service is healthy."""
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                resp = await client.get(
                    self.full_url,
                    headers={"Accept": "application/json"},
                )
                if resp.status_code == 200:
                    try:
                        data = resp.json()
                    except Exception:
                        data = {"status": "ok"}
                    return True, data
                return False, {
                    "status": "unhealthy",
                    "code": resp.status_code,
                }
        except httpx.TimeoutException:
            return False, {
                "status": "timeout",
                "error": f"Timeout after {timeout}s",
            }
        except httpx.ConnectError as e:
            return False, {
                "status": "connection_error",
                "error": str(e),
            }
        except Exception as e:
            return False, {
                "status": "error",
                "error": str(e),
            }

    @fail_wire(module="autonomous_deploy", gap_type="agent_cycle_failure")
    async def wait_for_health(
        self, timeout: int = 60, interval: int = 5,
    ) -> tuple[bool, dict[str, Any]]:
        """Wait for service to become healthy."""
        start_time = time.monotonic()
        last_error: Optional[dict[str, Any]] = None
        while time.monotonic() - start_time < timeout:
            healthy, data = await self.check(timeout=10)
            if healthy:
                return True, data
            last_error = data
            await asyncio.sleep(interval)
        return False, {
            "status": "timeout",
            "error": f"Health check failed after {timeout}s",
            "last_error": last_error,
        }


# ── Core Deployment Engine ─────────────────────────────────────────────────

class AutonomousDeployEngine:
    """Main deployment engine — makes ARIA fully autonomous.

    Combines:
      - MachinesDeployer (correct Machines API client, push guard)
      - DeploymentDatabase (SQLite-backed history)
      - WebhookNotifier (event notifications)
      - BlockchainAnchoring (immutable proof, stub)
      - HealthChecker (async health monitoring)
    """

    def __init__(
        self,
        config: Optional[DeployConfig] = None,
        machines_deployer: Optional[MachinesDeployer] = None,
    ) -> None:
        self.config = config or DeployConfig.from_env()
        self.db = DeploymentDatabase(self.config.db_path)

        # Use provided MachinesDeployer or create one with brain_hook
        self._machines = machines_deployer or MachinesDeployer(
            aria_service_url=f"https://{self.config.app_name}.fly.dev",
            brain_hook=self._try_get_brain_hook(),
        )

        # Resolve app URL
        self.app_url = f"https://{self.config.app_name}.fly.dev"

        self.health_checker = HealthChecker(
            self.app_url, self.config.health_check_path,
        )
        self.webhook = WebhookNotifier(self.config.webhook_url)

        # Optional blockchain
        if self.config.blockchain_enabled:
            self.blockchain = BlockchainAnchoring(
                self.config.blockchain_rpc_url,
                self.config.blockchain_private_key,
                self.config.blockchain_contract_address,
            )
        else:
            self.blockchain = None

        self._deploy_lock = asyncio.Lock()

    @staticmethod
    def _try_get_brain_hook() -> Any:
        """Try to import and return the brain_hook module.

        Returns None if brain_hook is not available (e.g. during tests).
        """
        try:
            from ..intel import brain_hook as _bh  # noqa: F811
            return _bh
        except (ImportError, ValueError):
            # ValueError can occur during pytest collection when
            # the package context is not fully initialized
            return None

    @fail_wire(module="autonomous_deploy", gap_type="agent_cycle_failure")
    async def aclose(self) -> None:
        """Close all HTTP clients and release resources."""
        await self._machines.aclose()
        await self.webhook.aclose()

    # ── Git helpers (pure Python, no subprocess) ───────────────────────────

    @fail_wire(module="autonomous_deploy", gap_type="agent_cycle_failure")
    def get_current_commit(self) -> tuple[str, str]:
        """Get current git commit hash and message.

        Delegates to aria_service.utils.git_utils.get_current_commit.
        Falls back to 'unknown' if git is not available.
        """
        from ..utils.git_utils import get_current_commit as _get
        return _get()

    # ── Live version ───────────────────────────────────────────────────────

    @fail_wire(module="autonomous_deploy", gap_type="agent_cycle_failure")
    async def get_live_version(self) -> LiveVersion:
        """Get what's actually running on Fly.io."""
        try:
            # Get current release info via Machines API
            machines = await self._list_machines()
            machine_count = len(machines)

            # Try to get release version from the first machine
            release_version = 0
            if machines and isinstance(machines, list) and len(machines) > 0:
                raw_ver = machines[0].get("version", 0)
                release_version = int(raw_ver) if raw_ver else 0

            # Check health
            healthy, health_data = await self.health_checker.check(
                timeout=10,
            )

            # Extract commit hash from health endpoint
            commit_hash = health_data.get(
                "build_rev", health_data.get("sha", "unknown"),
            )
            # Strip "sha " prefix if present
            if commit_hash.startswith("sha "):
                commit_hash = commit_hash[4:]

            image_tag = health_data.get("image_tag", commit_hash)

            return LiveVersion(
                commit_hash=commit_hash,
                image_tag=image_tag,
                deployed_at=datetime.now(timezone.utc),
                release_version=release_version,
                machine_count=machine_count,
                health_status="healthy" if healthy else "unhealthy",
            )
        except Exception as e:
            logger.warning(
                "[deploy_engine] get_live_version failed: %s", e,
            )
            return LiveVersion(
                commit_hash="unknown",
                image_tag="unknown",
                deployed_at=datetime.now(timezone.utc),
                release_version=0,
                machine_count=0,
                health_status="unknown",
            )

    async def _list_machines(self) -> list[dict[str, Any]]:
        """List machines via MachinesDeployer."""
        return await self._machines._list_machines(self.config.app_name)

    # ── Verification ───────────────────────────────────────────────────────

    @fail_wire(module="autonomous_deploy", gap_type="agent_cycle_failure")
    async def verify_deployment(
        self, expected_commit: str, timeout: int = 120,
    ) -> tuple[bool, dict[str, Any]]:
        """Verify that the expected commit is live.

        Compares short SHA forms (first 8 chars) to handle both
        full and abbreviated commit references.
        """
        expected_short = expected_commit[:8]
        start_time = time.monotonic()
        while time.monotonic() - start_time < timeout:
            live = await self.get_live_version()
            live_short = live.commit_hash[:8]
            if (
                live_short == expected_short
                and live.health_status == "healthy"
            ):
                return True, {
                    "verified": True,
                    "live_commit": live.commit_hash,
                    "expected_commit": expected_commit,
                    "health": live.health_status,
                    "verification_time": (
                        datetime.now(timezone.utc).isoformat()
                    ),
                }
            await asyncio.sleep(5)

        live = await self.get_live_version()
        return False, {
            "verified": False,
            "live_commit": live.commit_hash,
            "expected_commit": expected_commit,
            "health": live.health_status,
            "error": f"Timeout waiting for commit {expected_commit}",
        }

    # ── Deploy ─────────────────────────────────────────────────────────────

    @fail_wire(module="autonomous_deploy", gap_type="agent_cycle_failure")
    async def _await_dd_quiescence(self, max_wait_s: float = 900.0, poll_s: float = 15.0) -> None:
        """R-F3010 — deploy-coordination: wait (bounded) for in-flight user DDs to
        finish before deploying, so an aria-intel restart doesn't kill a running DD.
        Best-effort: after ``max_wait_s`` it proceeds anyway (a stuck DD must never
        block deploys forever — R-F3009 resumes it post-restart). Never raises."""
        try:
            from ..intel.dd_orchestrator import dd_inflight_count
        except Exception:
            return
        deadline = time.time() + max_wait_s
        waited = False
        try:
            while dd_inflight_count() > 0 and time.time() < deadline:
                waited = True
                logger.info(
                    "[R-F3010] deferring deploy — %d DD(s) in-flight; waiting up to %ds…",
                    dd_inflight_count(), int(max_wait_s),
                )
                await asyncio.sleep(poll_s)
        except Exception as _e:  # noqa: BLE001 — coordination is best-effort
            logger.debug("[R-F3010] dd-quiescence wait error (non-fatal): %s", _e)
            return
        if waited:
            logger.info("[R-F3010] DD(s) cleared or wait bound reached — proceeding with deploy")

    @fail_wire(module="autonomous_deploy", gap_type="agent_cycle_failure")
    async def deploy(
        self,
        commit_hash: Optional[str] = None,
        commit_message: Optional[str] = None,
        force: bool = False,
        r_number: Optional[int] = None,
    ) -> DeploymentRecord:
        """Main deployment function.

        Args:
            commit_hash: Git commit to deploy (auto-detected if None).
            commit_message: Commit message (auto-detected if None).
            force: Skip the "already deployed" check.
            r_number: R-number for this deploy (optional).

        Returns:
            DeploymentRecord with the result.
        """
        async with self._deploy_lock:
            # R-F3010 — deploy-coordination: a deploy restarts aria-intel and would kill
            # any in-flight user DD. Defer (bounded) until DDs finish so a deploy never
            # interrupts a running DD; the R-F3009 resume is the backstop past the bound.
            if not force:
                await self._await_dd_quiescence()
            # Get commit info
            if not commit_hash:
                detected_hash, detected_message = self.get_current_commit()
                commit_hash = detected_hash
                commit_message = commit_message or detected_message
            commit_message = commit_message or "Autonomous deployment"

            # Check if already deployed
            if not force:
                live = await self.get_live_version()
                if live.commit_hash == commit_hash:
                    logger.info(
                        "[deploy_engine] commit %s already live — skipping",
                        commit_hash,
                    )
                    return DeploymentRecord(
                        id=commit_hash,
                        commit_hash=commit_hash,
                        commit_message=commit_message,
                        image_tag=commit_hash,
                        status=DeploymentStatus.SUCCESS,
                        started_at=datetime.now(timezone.utc),
                        completed_at=datetime.now(timezone.utc),
                        duration_seconds=0,
                        verification_hash=(
                            self._generate_verification_hash(commit_hash)
                        ),
                    )

            # Create deployment record
            deploy_id = f"deploy_{int(time.time())}_{commit_hash}"
            record = DeploymentRecord(
                id=deploy_id,
                commit_hash=commit_hash,
                commit_message=commit_message,
                image_tag=commit_hash,  # Will be updated with real image ref after deploy
                status=DeploymentStatus.PENDING,
                started_at=datetime.now(timezone.utc),
            )
            self.db.save_deployment(record)

            # Send notification
            await self.webhook.send("deployment_started", {
                "commit": commit_hash,
                "message": commit_message,
                "deploy_id": deploy_id,
            })

            try:
                # Start deployment
                record.status = DeploymentStatus.DEPLOYING
                self.db.save_deployment(record)
                start_ts = time.monotonic()

                # Use MachinesDeployer for the actual deploy
                deploy_result = await self._machines.deploy(
                    app=self.config.app_name,
                    r_number=r_number or 0,
                    commit_sha=commit_hash,
                    skip_push_guard=force,
                )

                duration = time.monotonic() - start_ts
                record.duration_seconds = duration
                record.completed_at = datetime.now(timezone.utc)

                if deploy_result.success:
                    # Verify deployment
                    verified, verify_data = await self.verify_deployment(
                        commit_hash,
                    )

                    if verified:
                        record.status = DeploymentStatus.SUCCESS
                        record.verification_hash = (
                            self._generate_verification_hash(commit_hash)
                        )

                        # Store the real image ref from the deploy result
                        if deploy_result.image:
                            record.image_tag = deploy_result.image

                        # Anchor to blockchain if enabled
                        if self.blockchain:
                            tx_hash = self.blockchain.anchor_deployment(
                                commit_hash,
                                record.image_tag,
                                record.verification_hash or "",
                            )
                            record.blockchain_tx = tx_hash

                        # Save to version history
                        live = await self.get_live_version()
                        self.db.save_version_history(live, verified=True)

                        # Send success notification
                        await self.webhook.send("deployment_success", {
                            "commit": commit_hash,
                            "deploy_id": deploy_id,
                            "duration": duration,
                            "verification_hash": record.verification_hash,
                            "blockchain_tx": record.blockchain_tx,
                        })

                        logger.info(
                            "[deploy_engine] ✅ deployed %s in %.1fs",
                            commit_hash, duration,
                        )
                    else:
                        record.status = DeploymentStatus.FAILED
                        record.error_message = (
                            f"Verification failed: "
                            f"{verify_data.get('error', 'Unknown')}"
                        )
                        await self._handle_failed_deployment(record)
                else:
                    record.status = DeploymentStatus.FAILED
                    record.error_message = (
                        f"Deploy failed: {deploy_result.error}"
                    )
                    await self._handle_failed_deployment(record)

            except Exception as e:
                record.status = DeploymentStatus.FAILED
                record.error_message = str(e)
                await self._handle_failed_deployment(record)

            self.db.save_deployment(record)
            return record

    async def _handle_failed_deployment(
        self, record: DeploymentRecord,
    ) -> None:
        """Handle failed deployment with optional rollback."""
        logger.error(
            "[deploy_engine] ❌ deployment %s failed: %s",
            record.id, record.error_message,
        )

        await self.webhook.send("deployment_failed", {
            "commit": record.commit_hash,
            "deploy_id": record.id,
            "error": record.error_message,
        })

        if self.config.rollback_on_failure:
            logger.warning(
                "[deploy_engine] rolling back after failed deployment",
            )
            await self.rollback()

    # ── Rollback ───────────────────────────────────────────────────────────

    @fail_wire(module="autonomous_deploy", gap_type="agent_cycle_failure")
    async def rollback(
        self, target_version: Optional[int] = None,
    ) -> bool:
        """Rollback to previous version.

        Uses the MachinesDeployer public API to update machines to the
        previous image. If target_version is None, rolls back to the most
        recent successful deploy in the database.

        The image_tag stored in version_history is the real Fly image ref
        (e.g. "registry.fly.io/aria-intel:deployment-abc12345"), not just
        the commit hash — so rollback actually deploys a real image.
        """
        try:
            if target_version is None:
                # Find the most recent successful deploy
                history = self.db.get_deployment_history(limit=5)
                if len(history) < 2:
                    logger.warning(
                        "[deploy_engine] no previous version to rollback to",
                    )
                    return False
                # Index 0 is current, index 1 is previous
                prev = history[1]
                target_image = prev.get("image_tag", "")
            else:
                # Find by release version
                history = self.db.get_deployment_history(limit=50)
                prev = next(
                    (h for h in history
                     if h.get("release_version") == target_version),
                    None,
                )
                if not prev:
                    return False
                target_image = prev.get("image_tag", "")

            if not target_image:
                logger.warning(
                    "[deploy_engine] no image tag found for rollback target",
                )
                return False

            logger.warning(
                "[deploy_engine] rolling back to image: %s",
                target_image,
            )

            # Use MachinesDeployer public API to update fleet
            fleet_ok = await self._machines.update_fleet(
                self.config.app_name, target_image,
            )
            if not fleet_ok:
                return False

            # Wait for health
            healthy, _ = await self.health_checker.wait_for_health(
                timeout=60,
            )
            return healthy

        except Exception as e:
            logger.error("[deploy_engine] rollback failed: %s", e)
            return False

    # ── Verification hash ──────────────────────────────────────────────────

    @staticmethod
    def _generate_verification_hash(commit_hash: str) -> str:
        """Generate a verification hash for the deployment."""
        data = f"{commit_hash}:{datetime.now(timezone.utc).isoformat()}"
        return hashlib.sha256(data.encode()).hexdigest()[:16]

    # ── Status ─────────────────────────────────────────────────────────────

    @fail_wire(module="autonomous_deploy", gap_type="agent_cycle_failure")
    async def get_status(self) -> dict[str, Any]:
        """Get complete deployment status."""
        live = await self.get_live_version()
        history = self.db.get_deployment_history(10)
        return {
            "current": {
                "commit_hash": live.commit_hash,
                "image_tag": live.image_tag,
                "deployed_at": live.deployed_at.isoformat(),
                "release_version": live.release_version,
                "health_status": live.health_status,
                "machine_count": live.machine_count,
            },
            "history": history,
            "app_url": self.app_url,
            "last_check": datetime.now(timezone.utc).isoformat(),
        }

    # ── Health monitoring loop (async) ─────────────────────────────────────

    @fail_wire(module="autonomous_deploy", gap_type="agent_cycle_failure")
    async def health_loop_async(
        self, interval: int = DEFAULT_HEALTH_INTERVAL_S,
    ) -> None:
        """Background health monitoring loop.

        Runs as an asyncio task. Checks health every `interval` seconds.
        If unhealthy, sends a webhook warning and attempts rollback.
        """
        logger.info(
            "[deploy_engine] health monitoring started (interval=%ds)",
            interval,
        )
        while True:
            try:
                await asyncio.sleep(interval)
                live = await self.get_live_version()

                if live.health_status == "unhealthy":
                    logger.warning(
                        "[deploy_engine] health warning: %s",
                        live.health_status,
                    )
                    await self.webhook.send("health_warning", {
                        "status": live.health_status,
                        "commit": live.commit_hash,
                        "timestamp": (
                            datetime.now(timezone.utc).isoformat()
                        ),
                    })
                    # Attempt rollback
                    await self.rollback()

            except asyncio.CancelledError:
                logger.info("[deploy_engine] health loop cancelled")
                break
            except Exception as e:
                logger.error(
                    "[deploy_engine] health check failed: %s", e,
                )


# ── FastAPI Integration Endpoints ──────────────────────────────────────────

@fail_wire(module="autonomous_deploy", gap_type="agent_cycle_failure")
def add_deployment_endpoints(
    app: Any,
    engine: Optional[AutonomousDeployEngine] = None,
) -> AutonomousDeployEngine:
    """Add deployment endpoints to a FastAPI app.

    Adds:
      GET  /api/aria/deploy/status    — current deployment status
      GET  /api/aria/deploy/live      — what's running now
      POST /api/aria/deploy/trigger   — trigger a new deployment
      POST /api/aria/deploy/rollback  — rollback to previous version
      GET  /api/aria/deploy/history   — deployment history
      GET  /api/aria/version          — simple version info

    Args:
        app: FastAPI app instance.
        engine: Optional existing engine instance. If not provided,
                a new one is created. Pass the same engine you use
                for the health loop so endpoints and monitoring share
                the same state.

    Returns:
        The AutonomousDeployEngine instance (for lifespan wiring).
    """
    engine = engine or AutonomousDeployEngine()

    @app.get("/api/aria/deploy/status")
    async def get_deploy_status():
        """Get current deployment status."""
        return await engine.get_status()

    @app.get("/api/aria/deploy/live")
    async def get_live_version():
        """Get what's actually running on Fly.io."""
        live = await engine.get_live_version()
        return {
            "commit_hash": live.commit_hash,
            "image_tag": live.image_tag,
            "deployed_at": live.deployed_at.isoformat(),
            "release_version": live.release_version,
            "health": live.health_status,
            "verified": live.commit_hash != "unknown",
        }

    @app.post("/api/aria/deploy/trigger")
    async def trigger_deployment(force: bool = False):
        """Trigger a new deployment."""
        record = await engine.deploy(force=force)
        return {
            "deploy_id": record.id,
            "commit_hash": record.commit_hash,
            "status": record.status.value,
            "duration": record.duration_seconds,
            "verification_hash": record.verification_hash,
            "blockchain_tx": record.blockchain_tx,
        }

    @app.post("/api/aria/deploy/rollback")
    async def trigger_rollback(version: Optional[int] = None):
        """Rollback to previous version.

        If version is None, rolls back to the most recent successful
        deploy. If version is 0 or a positive integer, rolls back to
        that specific release version.
        """
        success = await engine.rollback(version)
        return {
            "success": success,
            "rolled_back_to": (
                version if version is not None else "previous"
            ),
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

    @app.get("/api/aria/deploy/history")
    async def get_deployment_history(limit: int = 20):
        """Get deployment history."""
        return engine.db.get_deployment_history(limit)

    @app.get("/api/aria/version")
    async def get_version():
        """Simple version endpoint for verification."""
        live = await engine.get_live_version()
        return {
            "version": "1.0.0",
            "commit": live.commit_hash,
            "deployed_at": live.deployed_at.isoformat(),
            "image_tag": live.image_tag,
            "verified": live.commit_hash != "unknown",
            "environment": os.environ.get("FLY_APP_NAME", "unknown"),
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

    return engine
