"""R-F1185 — Capability tests for AutonomousDeployEngine.

Tests the merged deployment engine:
  1. DeployConfig loads from env vars
  2. DeploymentDatabase CRUD operations
  3. DeploymentRecord and DeploymentStatus
  4. Git commit detection (pure Python, no subprocess)
  5. HealthChecker with mocked HTTP
  6. WebhookNotifier sends correctly
  7. BlockchainAnchoring returns mock hash
  8. Full deploy pipeline with mocked MachinesDeployer
  9. FastAPI endpoints respond correctly
"""
from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from typing import Any

from aria_service.autonomous.autonomous_deploy import (
    AutonomousDeployEngine,
    BlockchainAnchoring,
    DeployConfig,
    DeploymentDatabase,
    DeploymentRecord,
    DeploymentStatus,
    HealthChecker,
    LiveVersion,
    WebhookNotifier,
    add_deployment_endpoints,
)


# ── DeployConfig Tests ─────────────────────────────────────────────────────


class TestDeployConfig:
    """DeployConfig loads from env vars."""

    def test_default_values(self) -> None:
        """DeployConfig has sensible defaults."""
        config = DeployConfig()
        assert config.app_name == "aria-intel"
        assert config.rollback_on_failure is True
        assert config.max_retries == 3
        assert config.blockchain_enabled is False

    def test_from_env(self) -> None:
        """DeployConfig.from_env reads environment variables."""
        with patch.dict(os.environ, {
            "FLY_APP_NAME": "test-app",
            "FLY_API_TOKEN": "test-token",
            "ROLLBACK_ON_FAILURE": "false",
            "MAX_RETRIES": "5",
            "BLOCKCHAIN_ENABLED": "true",
        }, clear=False):
            config = DeployConfig.from_env()
            assert config.app_name == "test-app"
            assert config.fly_api_token == "test-token"
            assert config.rollback_on_failure is False
            assert config.max_retries == 5
            assert config.blockchain_enabled is True


# ── DeploymentStatus Tests ─────────────────────────────────────────────────


class TestDeploymentStatus:
    """DeploymentStatus enum works correctly."""

    def test_values(self) -> None:
        """DeploymentStatus has expected values."""
        assert DeploymentStatus.PENDING.value == "pending"
        assert DeploymentStatus.DEPLOYING.value == "deploying"
        assert DeploymentStatus.SUCCESS.value == "success"
        assert DeploymentStatus.FAILED.value == "failed"
        assert DeploymentStatus.ROLLING_BACK.value == "rolling_back"
        assert DeploymentStatus.ROLLED_BACK.value == "rolled_back"


# ── DeploymentRecord Tests ─────────────────────────────────────────────────


class TestDeploymentRecord:
    """DeploymentRecord dataclass works correctly."""

    def test_success_record(self) -> None:
        """DeploymentRecord stores success state."""
        now = datetime.now(timezone.utc)
        record = DeploymentRecord(
            id="deploy_123_abc",
            commit_hash="abc123",
            commit_message="Test deploy",
            image_tag="abc123",
            status=DeploymentStatus.SUCCESS,
            started_at=now,
            completed_at=now,
            duration_seconds=42.5,
            verification_hash="vhash123",
        )
        assert record.id == "deploy_123_abc"
        assert record.status == DeploymentStatus.SUCCESS
        assert record.duration_seconds == 42.5

    def test_failed_record(self) -> None:
        """DeploymentRecord stores failure state."""
        now = datetime.now(timezone.utc)
        record = DeploymentRecord(
            id="deploy_456_def",
            commit_hash="def456",
            commit_message="Failed deploy",
            image_tag="def456",
            status=DeploymentStatus.FAILED,
            started_at=now,
            completed_at=now,
            error_message="Build failed",
        )
        assert record.status == DeploymentStatus.FAILED
        assert record.error_message == "Build failed"


# ── DeploymentDatabase Tests ───────────────────────────────────────────────


class TestDeploymentDatabase:
    """DeploymentDatabase CRUD operations."""

    @pytest.fixture
    def db(self, tmp_path: Path) -> DeploymentDatabase:
        """Create a DeploymentDatabase with a temp path."""
        return DeploymentDatabase(tmp_path / "test.db")

    def test_save_and_get_deployment(self, db: DeploymentDatabase) -> None:
        """Save and retrieve a deployment record."""
        now = datetime.now(timezone.utc)
        record = DeploymentRecord(
            id="test_1",
            commit_hash="abc123",
            commit_message="Test",
            image_tag="abc123",
            status=DeploymentStatus.SUCCESS,
            started_at=now,
            completed_at=now,
            duration_seconds=10.0,
        )
        db.save_deployment(record)

        retrieved = db.get_deployment("test_1")
        assert retrieved is not None
        assert retrieved.id == "test_1"
        assert retrieved.commit_hash == "abc123"
        assert retrieved.status == DeploymentStatus.SUCCESS
        assert retrieved.duration_seconds == 10.0

    def test_get_nonexistent(self, db: DeploymentDatabase) -> None:
        """get_deployment returns None for missing ID."""
        assert db.get_deployment("nonexistent") is None

    def test_save_version_history(self, db: DeploymentDatabase) -> None:
        """Save and retrieve version history."""
        live = LiveVersion(
            commit_hash="abc123",
            image_tag="abc123",
            deployed_at=datetime.now(timezone.utc),
            release_version=42,
            machine_count=3,
            health_status="healthy",
        )
        db.save_version_history(live, verified=True)

        history = db.get_deployment_history(limit=10)
        assert len(history) == 1
        assert history[0]["commit_hash"] == "abc123"
        assert history[0]["release_version"] == 42
        assert history[0]["verified"] == 1

    def test_history_ordered(self, db: DeploymentDatabase) -> None:
        """Version history is ordered by deployed_at DESC."""
        for i in range(3):
            live = LiveVersion(
                commit_hash=f"hash_{i}",
                image_tag=f"tag_{i}",
                deployed_at=datetime.now(timezone.utc),
                release_version=i,
                machine_count=1,
                health_status="healthy",
            )
            db.save_version_history(live)

        history = db.get_deployment_history(limit=10)
        assert len(history) == 3
        # Most recent first (highest release_version)
        assert history[0]["release_version"] == 2
        assert history[1]["release_version"] == 1
        assert history[2]["release_version"] == 0


# ── Git Commit Detection Tests ─────────────────────────────────────────────


class TestGitCommitDetection:
    """Git commit detection reads .git files directly."""

    def test_get_current_commit_from_ref(self, tmp_path: Path) -> None:
        """get_current_commit reads from .git/refs/heads/main."""
        git_dir = tmp_path / ".git"
        heads = git_dir / "refs" / "heads"
        heads.mkdir(parents=True)
        (heads / "main").write_text(
            "abcdef1234567890abcdef1234567890abcdef12\n", encoding="utf-8",
        )

        engine = AutonomousDeployEngine(
            config=DeployConfig(app_name="test"),
        )
        with patch.object(
            engine, "_resolve_git_root", return_value=tmp_path,
        ):
            commit_hash, commit_message = engine.get_current_commit()
            assert commit_hash == "abcdef12"

    def test_get_current_commit_fallback(self) -> None:
        """get_current_commit returns 'unknown' when no .git."""
        engine = AutonomousDeployEngine(
            config=DeployConfig(app_name="test"),
        )
        with patch.object(engine, "_resolve_git_root", return_value=None):
            commit_hash, commit_message = engine.get_current_commit()
            assert commit_hash == "unknown"
            assert commit_message == "Manual deployment"


# ── HealthChecker Tests ────────────────────────────────────────────────────


class TestHealthChecker:
    """HealthChecker with mocked HTTP."""

    @pytest.mark.asyncio
    async def test_check_healthy(self) -> None:
        """check returns True on 200."""
        checker = HealthChecker("https://test.fly.dev", "/health/live")

        with patch("httpx.AsyncClient.get") as mock_get:
            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_response.json.return_value = {"status": "alive"}
            mock_get.return_value = mock_response

            healthy, data = await checker.check(timeout=10)
            assert healthy is True
            assert data["status"] == "alive"

    @pytest.mark.asyncio
    async def test_check_unhealthy(self) -> None:
        """check returns False on non-200."""
        checker = HealthChecker("https://test.fly.dev", "/health/live")

        with patch("httpx.AsyncClient.get") as mock_get:
            mock_response = MagicMock()
            mock_response.status_code = 503
            mock_get.return_value = mock_response

            healthy, data = await checker.check(timeout=10)
            assert healthy is False
            assert data["status"] == "unhealthy"

    @pytest.mark.asyncio
    async def test_check_timeout(self) -> None:
        """check returns False on timeout."""
        checker = HealthChecker("https://test.fly.dev", "/health/live")

        with patch("httpx.AsyncClient.get") as mock_get:
            mock_get.side_effect = httpx.TimeoutException("timeout")

            healthy, data = await checker.check(timeout=10)
            assert healthy is False
            assert data["status"] == "timeout"

    @pytest.mark.asyncio
    async def test_wait_for_health_success(self) -> None:
        """wait_for_health returns True when service becomes healthy."""
        checker = HealthChecker("https://test.fly.dev", "/health/live")

        with patch.object(checker, "check") as mock_check:
            # First call fails, second succeeds
            mock_check.side_effect = [
                (False, {"status": "unhealthy"}),
                (True, {"status": "alive"}),
            ]

            healthy, data = await checker.wait_for_health(
                timeout=30, interval=0.01,
            )
            assert healthy is True
            assert data["status"] == "alive"

    @pytest.mark.asyncio
    async def test_wait_for_health_timeout(self) -> None:
        """wait_for_health returns False on timeout."""
        checker = HealthChecker("https://test.fly.dev", "/health/live")

        with patch.object(checker, "check") as mock_check:
            mock_check.return_value = (False, {"status": "unhealthy"})

            healthy, data = await checker.wait_for_health(
                timeout=0.5, interval=0.05,
            )
            assert healthy is False
            assert data["status"] == "timeout"


# ── WebhookNotifier Tests ──────────────────────────────────────────────────


class TestWebhookNotifier:
    """WebhookNotifier sends correctly."""

    def test_send_with_url(self) -> None:
        """send makes a POST request when URL is set."""
        notifier = WebhookNotifier("https://hooks.example.com/deploy")

        with patch("httpx.post") as mock_post:
            notifier.send("test_event", {"key": "value"})
            mock_post.assert_called_once()
            args, kwargs = mock_post.call_args
            assert args[0] == "https://hooks.example.com/deploy"
            assert kwargs["json"]["event"] == "test_event"
            assert kwargs["json"]["data"]["key"] == "value"

    def test_send_without_url(self) -> None:
        """send does nothing when URL is empty."""
        notifier = WebhookNotifier()

        with patch("httpx.post") as mock_post:
            notifier.send("test_event", {"key": "value"})
            mock_post.assert_not_called()

    def test_send_handles_error(self) -> None:
        """send handles HTTP errors gracefully."""
        notifier = WebhookNotifier("https://hooks.example.com/deploy")

        with patch("httpx.post", side_effect=Exception("Connection error")):
            # Should not raise
            notifier.send("test_event", {"key": "value"})


# ── BlockchainAnchoring Tests ──────────────────────────────────────────────


class TestBlockchainAnchoring:
    """BlockchainAnchoring returns mock hash."""

    def test_anchor_without_web3(self) -> None:
        """anchor_deployment returns a mock hash when web3 is not available."""
        anchor = BlockchainAnchoring()
        tx_hash = anchor.anchor_deployment(
            commit_hash="abc123",
            image_tag="abc123",
            verification_hash="vhash123",
        )
        assert tx_hash is not None
        assert tx_hash.startswith("0x")
        assert len(tx_hash) == 66  # 0x + 64 hex chars

    def test_anchor_deterministic(self) -> None:
        """Same inputs produce the same mock hash."""
        anchor = BlockchainAnchoring()
        tx1 = anchor.anchor_deployment("abc", "img1", "v1")
        tx2 = anchor.anchor_deployment("abc", "img1", "v1")
        assert tx1 == tx2

    def test_anchor_different_inputs(self) -> None:
        """Different inputs produce different mock hashes."""
        anchor = BlockchainAnchoring()
        tx1 = anchor.anchor_deployment("abc", "img1", "v1")
        tx2 = anchor.anchor_deployment("def", "img2", "v2")
        assert tx1 != tx2


# ── LiveVersion Tests ──────────────────────────────────────────────────────


class TestLiveVersion:
    """LiveVersion dataclass works correctly."""

    def test_live_version(self) -> None:
        """LiveVersion stores state."""
        now = datetime.now(timezone.utc)
        live = LiveVersion(
            commit_hash="abc123",
            image_tag="abc123",
            deployed_at=now,
            release_version=42,
            machine_count=3,
            health_status="healthy",
        )
        assert live.commit_hash == "abc123"
        assert live.release_version == 42
        assert live.machine_count == 3
        assert live.health_status == "healthy"


# ── Full Deploy Pipeline Tests ─────────────────────────────────────────────


class TestFullDeployPipeline:
    """Full deploy pipeline with mocked MachinesDeployer."""

    @pytest.mark.asyncio
    async def test_deploy_success(self, tmp_path: Path) -> None:
        """Full deploy succeeds when all steps pass."""
        db_path = tmp_path / "deployments.db"
        config = DeployConfig(
            app_name="aria-intel",
            db_path=db_path,
            rollback_on_failure=False,
        )

        # Mock MachinesDeployer
        mock_machines = AsyncMock()
        mock_machines.deploy.return_value = MagicMock(
            success=True,
            image="registry.fly.io/aria-intel:abc123",
            duration_s=42.5,
        )
        mock_machines._client = AsyncMock()

        engine = AutonomousDeployEngine(
            config=config, machines_deployer=mock_machines,
        )

        # Mock health checker to return healthy
        with patch.object(
            engine.health_checker, "check",
            return_value=(True, {"build_rev": "abc123", "status": "alive"}),
        ):
            record = await engine.deploy(
                commit_hash="abc123",
                commit_message="Test deploy",
                force=True,
            )

        assert record.status == DeploymentStatus.SUCCESS
        assert record.commit_hash == "abc123"
        assert record.duration_seconds is not None
        assert record.verification_hash is not None

        # Verify it was saved to DB
        retrieved = engine.db.get_deployment(record.id)
        assert retrieved is not None
        assert retrieved.status == DeploymentStatus.SUCCESS

    @pytest.mark.asyncio
    async def test_deploy_failure(self, tmp_path: Path) -> None:
        """Deploy fails when MachinesDeployer fails."""
        db_path = tmp_path / "deployments.db"
        config = DeployConfig(
            app_name="aria-intel",
            db_path=db_path,
            rollback_on_failure=False,
        )

        mock_machines = AsyncMock()
        mock_machines.deploy.return_value = MagicMock(
            success=False,
            error="Build failed",
        )
        mock_machines._client = AsyncMock()

        engine = AutonomousDeployEngine(
            config=config, machines_deployer=mock_machines,
        )

        record = await engine.deploy(
            commit_hash="abc123",
            commit_message="Test deploy",
            force=True,
        )

        assert record.status == DeploymentStatus.FAILED
        assert "Build failed" in (record.error_message or "")

    @pytest.mark.asyncio
    async def test_deploy_already_live(self, tmp_path: Path) -> None:
        """Deploy skips when commit is already live."""
        db_path = tmp_path / "deployments.db"
        config = DeployConfig(
            app_name="aria-intel",
            db_path=db_path,
            rollback_on_failure=False,
        )

        engine = AutonomousDeployEngine(
            config=config,
            machines_deployer=AsyncMock(),
        )

        # Mock get_live_version to return the same commit
        with patch.object(
            engine, "get_live_version",
            return_value=LiveVersion(
                commit_hash="abc123",
                image_tag="abc123",
                deployed_at=datetime.now(timezone.utc),
                release_version=42,
                machine_count=3,
                health_status="healthy",
            ),
        ):
            record = await engine.deploy(
                commit_hash="abc123",
                commit_message="Already deployed",
                force=False,
            )

        assert record.status == DeploymentStatus.SUCCESS
        assert record.duration_seconds == 0  # Skipped


# ── FastAPI Endpoint Tests ─────────────────────────────────────────────────


class TestFastAPIEndpoints:
    """FastAPI endpoints respond correctly."""

    @pytest.fixture
    def app(self) -> Any:
        """Create a FastAPI test app with deployment endpoints."""
        from fastapi import FastAPI
        app = FastAPI()
        add_deployment_endpoints(app)
        return app

    def test_status_endpoint_exists(self, app) -> None:
        """GET /api/aria/deploy/status is registered."""
        routes = [r.path for r in app.routes]
        assert "/api/aria/deploy/status" in routes

    def test_live_endpoint_exists(self, app) -> None:
        """GET /api/aria/deploy/live is registered."""
        routes = [r.path for r in app.routes]
        assert "/api/aria/deploy/live" in routes

    def test_trigger_endpoint_exists(self, app) -> None:
        """POST /api/aria/deploy/trigger is registered."""
        routes = [r.path for r in app.routes]
        assert "/api/aria/deploy/trigger" in routes

    def test_rollback_endpoint_exists(self, app) -> None:
        """POST /api/aria/deploy/rollback is registered."""
        routes = [r.path for r in app.routes]
        assert "/api/aria/deploy/rollback" in routes

    def test_history_endpoint_exists(self, app) -> None:
        """GET /api/aria/deploy/history is registered."""
        routes = [r.path for r in app.routes]
        assert "/api/aria/deploy/history" in routes

    def test_version_endpoint_exists(self, app) -> None:
        """GET /api/aria/version is registered."""
        routes = [r.path for r in app.routes]
        assert "/api/aria/version" in routes

    @pytest.mark.asyncio
    async def test_version_endpoint_response(self) -> None:
        """GET /api/aria/version returns expected structure."""
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        app = FastAPI()
        with patch(
            "aria_service.autonomous.autonomous_deploy.AutonomousDeployEngine",
        ) as MockEngine:
            mock_engine = MagicMock()
            mock_engine.get_live_version = AsyncMock(return_value=LiveVersion(
                commit_hash="abc123",
                image_tag="abc123",
                deployed_at=datetime.now(timezone.utc),
                release_version=42,
                machine_count=3,
                health_status="healthy",
            ))
            MockEngine.return_value = mock_engine

            add_deployment_endpoints(app)

            with TestClient(app) as client:
                response = client.get("/api/aria/version")
                assert response.status_code == 200
                data = response.json()
                assert "commit" in data
                assert "version" in data
                assert "environment" in data
