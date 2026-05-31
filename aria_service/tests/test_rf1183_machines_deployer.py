"""R-F1183 — Capability tests for MachinesDeployer.

Tests the pure Fly Machines API deployer:
  1. Push guard reads git refs correctly
  2. Push guard detects un-pushed commits
  3. Canary launch builds correct API request
  4. Live verification matches build_rev
  5. Deploy history is recorded to JSON files
  6. Full deploy pipeline with mocked HTTP
"""
from __future__ import annotations

import asyncio
import json
import os
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from aria_service.autonomous.machines_deployer import (
    DEPLOY_HISTORY_DIR,
    MachinesDeployer,
    DeployResult,
)


# ── Fixtures ────────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _fast_poll() -> None:
    """Speed up poll intervals for tests (avoids 3-minute timeouts)."""
    import aria_service.autonomous.machines_deployer as _md
    _md._TEST_POLL_INTERVAL_S = 0.01
    _md._TEST_BUILD_POLL_S = 0.01
    yield
    _md._TEST_POLL_INTERVAL_S = 0.0
    _md._TEST_BUILD_POLL_S = 0.0


@pytest.fixture
def deployer() -> MachinesDeployer:
    """Create a MachinesDeployer with a mock HTTP client."""
    client = AsyncMock(spec=httpx.AsyncClient)
    return MachinesDeployer(
        aria_service_url="https://aria-intel.fly.dev",
        repo_path=Path(tempfile.mkdtemp()),
        http_client=client,
    )


@pytest.fixture
def git_repo(tmp_path: Path) -> Path:
    """Create a minimal git repo with a known HEAD and origin/main."""
    repo = tmp_path / "test_repo"
    repo.mkdir()
    git_dir = repo / ".git"
    git_dir.mkdir()

    # Create HEAD ref
    heads = git_dir / "refs" / "heads"
    heads.mkdir(parents=True)
    (heads / "main").write_text(
        "abcdef1234567890abcdef1234567890abcdef12\n", encoding="utf-8",
    )

    # Create origin/main ref
    remotes = git_dir / "refs" / "remotes" / "origin"
    remotes.mkdir(parents=True)
    (remotes / "main").write_text(
        "abcdef1234567890abcdef1234567890abcdef12\n", encoding="utf-8",
    )

    return repo


# ── Push Guard Tests ────────────────────────────────────────────────────────


class TestPushGuard:
    """Push guard reads git refs correctly."""

    def test_push_guard_passes_when_matched(self, git_repo: Path) -> None:
        """Push guard passes when HEAD matches origin/main."""
        deployer = MachinesDeployer(
            aria_service_url="https://aria-intel.fly.dev",
            repo_path=git_repo,
            http_client=AsyncMock(spec=httpx.AsyncClient),
        )
        assert deployer._check_push_guard(
            "abcdef1234567890abcdef1234567890abcdef12",
        ) is True

    def test_push_guard_fails_when_not_pushed(self, git_repo: Path) -> None:
        """Push guard fails when HEAD != origin/main."""
        # Write a different SHA to HEAD
        heads = git_repo / ".git" / "refs" / "heads"
        (heads / "main").write_text(
            "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb\n", encoding="utf-8",
        )

        deployer = MachinesDeployer(
            aria_service_url="https://aria-intel.fly.dev",
            repo_path=git_repo,
            http_client=AsyncMock(spec=httpx.AsyncClient),
        )
        assert deployer._check_push_guard(
            "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
        ) is False

    def test_push_guard_fails_when_no_remote(self, tmp_path: Path) -> None:
        """Push guard fails when origin/main doesn't exist."""
        repo = tmp_path / "no_remote"
        repo.mkdir()
        git_dir = repo / ".git"
        git_dir.mkdir(parents=True)
        heads = git_dir / "refs" / "heads"
        heads.mkdir(parents=True)
        (heads / "main").write_text(
            "abcdef1234567890abcdef1234567890abcdef12\n", encoding="utf-8",
        )
        # No refs/remotes/origin/main

        deployer = MachinesDeployer(
            aria_service_url="https://aria-intel.fly.dev",
            repo_path=repo,
            http_client=AsyncMock(spec=httpx.AsyncClient),
        )
        assert deployer._check_push_guard(
            "abcdef1234567890abcdef1234567890abcdef12",
        ) is False

    def test_push_guard_fails_when_no_git_dir(self, tmp_path: Path) -> None:
        """Push guard fails when there's no .git directory."""
        deployer = MachinesDeployer(
            aria_service_url="https://aria-intel.fly.dev",
            repo_path=tmp_path,
            http_client=AsyncMock(spec=httpx.AsyncClient),
        )
        assert deployer._check_push_guard(
            "abcdef1234567890abcdef1234567890abcdef12",
        ) is False

    def test_read_git_ref_returns_sha(self, git_repo: Path) -> None:
        """_read_git_ref returns the SHA from a ref file."""
        ref_path = git_repo / ".git" / "refs" / "heads" / "main"
        sha = MachinesDeployer._read_git_ref(ref_path)
        assert sha == "abcdef1234567890abcdef1234567890abcdef12"

    def test_read_git_ref_returns_none_for_symref(self, tmp_path: Path) -> None:
        """_read_git_ref returns None for symbolic refs."""
        ref_file = tmp_path / "HEAD"
        ref_file.write_text("ref: refs/heads/main\n", encoding="utf-8")
        sha = MachinesDeployer._read_git_ref(ref_file)
        assert sha is None

    def test_read_git_ref_returns_none_for_missing(self, tmp_path: Path) -> None:
        """_read_git_ref returns None for missing files."""
        sha = MachinesDeployer._read_git_ref(tmp_path / "nonexistent")
        assert sha is None

    def test_find_in_packed_refs(self, tmp_path: Path) -> None:
        """_find_in_packed_refs finds a ref in packed-refs."""
        packed = tmp_path / "packed-refs"
        packed.write_text(
            "# pack-refs with: peeled fully-peeled sorted\n"
            "abcdef1234567890abcdef1234567890abcdef12 refs/heads/main\n"
            "^bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb\n"
            "cccccccccccccccccccccccccccccccccccccccc refs/remotes/origin/main\n",
            encoding="utf-8",
        )
        sha = MachinesDeployer._find_in_packed_refs(
            packed, "refs/heads/main",
        )
        assert sha == "abcdef1234567890abcdef1234567890abcdef12"

    def test_find_in_packed_refs_not_found(self, tmp_path: Path) -> None:
        """_find_in_packed_refs returns None when ref not found."""
        packed = tmp_path / "packed-refs"
        packed.write_text(
            "abcdef1234567890abcdef1234567890abcdef12 refs/heads/main\n",
            encoding="utf-8",
        )
        sha = MachinesDeployer._find_in_packed_refs(
            packed, "refs/heads/nonexistent",
        )
        assert sha is None


# ── Canary Launch Tests ─────────────────────────────────────────────────────


class TestCanaryLaunch:
    """Canary launch builds correct API request."""

    @pytest.mark.asyncio
    async def test_launch_canary_success(self, deployer: MachinesDeployer) -> None:
        """Canary launch returns machine ID on 201."""
        mock_response = MagicMock()
        mock_response.status_code = 201
        mock_response.json.return_value = {"id": "abc123"}
        deployer._client.post = AsyncMock(return_value=mock_response)

        machine_id = await deployer._launch_canary("aria-intel", "registry.fly.io/aria-intel:test")
        assert machine_id == "abc123"

        # Verify the API call was correct
        deployer._client.post.assert_called_once()
        call_args = deployer._client.post.call_args
        assert "api.machines.dev/v1/apps/aria-intel/machines" in str(call_args[0][0])

    @pytest.mark.asyncio
    async def test_launch_canary_fails_on_non_201(
        self, deployer: MachinesDeployer,
    ) -> None:
        """Canary launch returns None on non-201 status."""
        mock_response = MagicMock()
        mock_response.status_code = 400
        mock_response.text = "Bad request"
        deployer._client.post = AsyncMock(return_value=mock_response)

        machine_id = await deployer._launch_canary("aria-intel", "registry.fly.io/aria-intel:test")
        assert machine_id is None

    @pytest.mark.asyncio
    async def test_launch_canary_handles_timeout(
        self, deployer: MachinesDeployer,
    ) -> None:
        """Canary launch handles httpx timeout gracefully."""
        deployer._client.post = AsyncMock(
            side_effect=httpx.TimeoutException("timeout"),
        )

        machine_id = await deployer._launch_canary("aria-intel", "registry.fly.io/aria-intel:test")
        assert machine_id is None


# ── Live Verification Tests ─────────────────────────────────────────────────


class TestLiveVerification:
    """Live verification matches build_rev."""

    @pytest.mark.asyncio
    async def test_verify_live_matches_sha(self, deployer: MachinesDeployer) -> None:
        """Live verification passes when build_rev matches."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.text = '{"build_rev": "abcdef12", "status": "ok"}'
        deployer._client.get = AsyncMock(return_value=mock_response)

        result = await deployer._verify_live(
            "aria-intel", "abcdef1234567890abcdef1234567890abcdef12",
        )
        assert result is True

    @pytest.mark.asyncio
    async def test_verify_live_matches_sha_alt_format(
        self, deployer: MachinesDeployer,
    ) -> None:
        """Live verification matches 'sha abcdef12' format."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.text = '{"status": "ok", "sha": "abcdef12"}'
        deployer._client.get = AsyncMock(return_value=mock_response)

        result = await deployer._verify_live(
            "aria-intel", "abcdef1234567890abcdef1234567890abcdef12",
        )
        assert result is True

    @pytest.mark.asyncio
    async def test_verify_live_fails_on_mismatch(
        self, deployer: MachinesDeployer,
    ) -> None:
        """Live verification fails when build_rev doesn't match."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.text = '{"build_rev": "deadbeef", "status": "ok"}'
        deployer._client.get = AsyncMock(return_value=mock_response)

        result = await deployer._verify_live(
            "aria-intel", "abcdef1234567890abcdef1234567890abcdef12",
        )
        assert result is False

    @pytest.mark.asyncio
    async def test_verify_live_fails_on_http_error(
        self, deployer: MachinesDeployer,
    ) -> None:
        """Live verification fails on HTTP error."""
        mock_response = MagicMock()
        mock_response.status_code = 503
        deployer._client.get = AsyncMock(return_value=mock_response)

        result = await deployer._verify_live(
            "aria-intel", "abcdef1234567890abcdef1234567890abcdef12",
        )
        assert result is False


# ── Deploy History Tests ────────────────────────────────────────────────────


class TestDeployHistory:
    """Deploy history is recorded to JSON files."""

    @pytest.mark.asyncio
    async def test_record_deploy_creates_file(self, tmp_path: Path) -> None:
        """_record_deploy creates a JSON file with deploy record."""
        deployer = MachinesDeployer(
            aria_service_url="https://aria-intel.fly.dev",
            repo_path=tmp_path,
            http_client=AsyncMock(spec=httpx.AsyncClient),
        )

        # Override the history dir to use tmp_path
        with patch(
            "aria_service.autonomous.machines_deployer.DEPLOY_HISTORY_DIR",
            tmp_path / "deploy_history",
        ):
            await deployer._record_deploy(
                app="aria-intel",
                r_number=1183,
                image="registry.fly.io/aria-intel:deployment-abc123",
                commit_sha="abcdef1234567890abcdef1234567890abcdef12",
            )

            history_file = tmp_path / "deploy_history" / "aria-intel.json"
            assert history_file.exists()

            history = json.loads(history_file.read_text(encoding="utf-8"))
            assert len(history) == 1
            assert history[0]["r_number"] == 1183
            assert history[0]["app"] == "aria-intel"
            assert "ts" in history[0]

    @pytest.mark.asyncio
    async def test_record_deploy_appends(self, tmp_path: Path) -> None:
        """_record_deploy appends to existing history."""
        deployer = MachinesDeployer(
            aria_service_url="https://aria-intel.fly.dev",
            repo_path=tmp_path,
            http_client=AsyncMock(spec=httpx.AsyncClient),
        )

        history_dir = tmp_path / "deploy_history"
        history_dir.mkdir()
        (history_dir / "aria-intel.json").write_text(
            json.dumps([{"r_number": 1182, "app": "aria-intel", "ts": "2026-01-01T00:00:00"}]),
            encoding="utf-8",
        )

        with patch(
            "aria_service.autonomous.machines_deployer.DEPLOY_HISTORY_DIR",
            history_dir,
        ):
            await deployer._record_deploy(
                app="aria-intel",
                r_number=1183,
                image="registry.fly.io/aria-intel:deployment-abc123",
                commit_sha="abcdef1234567890abcdef1234567890abcdef12",
            )

            history = json.loads(
                (history_dir / "aria-intel.json").read_text(encoding="utf-8"),
            )
            assert len(history) == 2
            assert history[0]["r_number"] == 1183  # newest first
            assert history[1]["r_number"] == 1182

    def test_load_deploy_record_found(self, tmp_path: Path) -> None:
        """_load_deploy_record finds a record by R-number."""
        deployer = MachinesDeployer(
            aria_service_url="https://aria-intel.fly.dev",
            repo_path=tmp_path,
            http_client=AsyncMock(spec=httpx.AsyncClient),
        )

        history_dir = tmp_path / "deploy_history"
        history_dir.mkdir()
        (history_dir / "aria-intel.json").write_text(
            json.dumps([
                {"r_number": 1183, "app": "aria-intel", "image": "img:abc"},
                {"r_number": 1182, "app": "aria-intel", "image": "img:def"},
            ]),
            encoding="utf-8",
        )

        with patch(
            "aria_service.autonomous.machines_deployer.DEPLOY_HISTORY_DIR",
            history_dir,
        ):
            record = deployer._load_deploy_record("aria-intel", 1182)
            assert record is not None
            assert record["r_number"] == 1182
            assert record["image"] == "img:def"

    def test_load_deploy_record_not_found(self, tmp_path: Path) -> None:
        """_load_deploy_record returns None when not found."""
        deployer = MachinesDeployer(
            aria_service_url="https://aria-intel.fly.dev",
            repo_path=tmp_path,
            http_client=AsyncMock(spec=httpx.AsyncClient),
        )

        history_dir = tmp_path / "deploy_history"
        history_dir.mkdir()
        (history_dir / "aria-intel.json").write_text(
            json.dumps([{"r_number": 1183, "app": "aria-intel"}]),
            encoding="utf-8",
        )

        with patch(
            "aria_service.autonomous.machines_deployer.DEPLOY_HISTORY_DIR",
            history_dir,
        ):
            record = deployer._load_deploy_record("aria-intel", 9999)
            assert record is None


# ── Full Pipeline Test (mocked HTTP) ────────────────────────────────────────


class TestFullDeployPipeline:
    """Full deploy pipeline with mocked HTTP."""

    @pytest.mark.asyncio
    async def test_deploy_success(self, git_repo: Path) -> None:
        """Full deploy pipeline succeeds when all steps pass."""
        client = AsyncMock(spec=httpx.AsyncClient)

        # Mock build trigger
        build_response = MagicMock()
        build_response.status_code = 202
        build_response.json.return_value = {
            "id": "build_abc",
            "image": "registry.fly.io/aria-intel:deployment-abcdef12",
        }

        # Mock build poll (succeeded immediately)
        build_status = MagicMock()
        build_status.status_code = 200
        build_status.json.return_value = {
            "status": "succeeded",
            "image": "registry.fly.io/aria-intel:deployment-abcdef12",
        }

        # Mock canary launch
        canary_response = MagicMock()
        canary_response.status_code = 201
        canary_response.json.return_value = {"id": "canary_abc"}

        # Mock canary smoke test (public)
        smoke_response = MagicMock()
        smoke_response.status_code = 200

        # Mock list machines
        list_response = MagicMock()
        list_response.status_code = 200
        list_response.json.return_value = [
            {"id": "machine_1", "config": {"image": "old", "env": {}}},
        ]

        # Mock get machine
        get_response = MagicMock()
        get_response.status_code = 200
        get_response.json.return_value = {
            "id": "machine_1",
            "config": {"image": "old", "env": {}},
        }

        # Mock update machine
        update_response = MagicMock()
        update_response.status_code = 200

        # Mock destroy canary
        destroy_response = MagicMock()
        destroy_response.status_code = 200

        # Mock live verification
        live_response = MagicMock()
        live_response.status_code = 200
        live_response.text = '{"build_rev": "abcdef12", "status": "ok"}'

        # Wire up the mock client
        client.post = AsyncMock(side_effect=[
            build_response,      # build trigger
            canary_response,     # canary launch
            update_response,     # update machine
        ])
        client.get = AsyncMock(side_effect=[
            build_status,        # build poll
            smoke_response,      # canary smoke test (public)
            list_response,       # list machines
            get_response,        # get machine config
            live_response,       # live verification
        ])
        client.delete = AsyncMock(return_value=destroy_response)

        deployer = MachinesDeployer(
            aria_service_url="https://aria-intel.fly.dev",
            repo_path=git_repo,
            http_client=client,
        )

        result = await deployer.deploy(
            app="aria-intel",
            r_number=1183,
            commit_sha="abcdef1234567890abcdef1234567890abcdef12",
            skip_push_guard=True,
        )

        assert result.success is True
        assert result.app == "aria-intel"
        assert result.r_number == 1183
        assert result.image == "registry.fly.io/aria-intel:deployment-abcdef12"
        assert result.duration_s > 0

    @pytest.mark.asyncio
    async def test_deploy_fails_on_build_failure(self, git_repo: Path) -> None:
        """Deploy fails when build fails."""
        client = AsyncMock(spec=httpx.AsyncClient)

        build_response = MagicMock()
        build_response.status_code = 500
        build_response.text = "Build failed"
        client.post = AsyncMock(return_value=build_response)

        deployer = MachinesDeployer(
            aria_service_url="https://aria-intel.fly.dev",
            repo_path=git_repo,
            http_client=client,
        )

        result = await deployer.deploy(
            app="aria-intel",
            r_number=1183,
            commit_sha="abcdef1234567890abcdef1234567890abcdef12",
            skip_push_guard=True,
        )

        assert result.success is False
        assert "build" in (result.error or "").lower()

    @pytest.mark.asyncio
    async def test_deploy_fails_on_canary_failure(self, git_repo: Path) -> None:
        """Deploy fails when canary launch fails."""
        client = AsyncMock(spec=httpx.AsyncClient)

        # Build succeeds
        build_response = MagicMock()
        build_response.status_code = 202
        build_response.json.return_value = {
            "id": "build_abc",
            "image": "registry.fly.io/aria-intel:deployment-abcdef12",
        }

        # Build poll succeeds
        build_status = MagicMock()
        build_status.status_code = 200
        build_status.json.return_value = {
            "status": "succeeded",
            "image": "registry.fly.io/aria-intel:deployment-abcdef12",
        }

        # Canary launch fails
        canary_response = MagicMock()
        canary_response.status_code = 400
        canary_response.text = "Bad request"

        client.post = AsyncMock(side_effect=[build_response, canary_response])
        client.get = AsyncMock(return_value=build_status)

        deployer = MachinesDeployer(
            aria_service_url="https://aria-intel.fly.dev",
            repo_path=git_repo,
            http_client=client,
        )

        result = await deployer.deploy(
            app="aria-intel",
            r_number=1183,
            commit_sha="abcdef1234567890abcdef1234567890abcdef12",
            skip_push_guard=True,
        )

        assert result.success is False
        assert "canary" in (result.error or "").lower()

    @pytest.mark.asyncio
    async def test_deploy_fails_on_live_verification_failure(
        self, git_repo: Path,
    ) -> None:
        """Deploy fails when live verification doesn't match."""
        client = AsyncMock(spec=httpx.AsyncClient)

        # Build succeeds
        build_response = MagicMock()
        build_response.status_code = 202
        build_response.json.return_value = {
            "id": "build_abc",
            "image": "registry.fly.io/aria-intel:deployment-abcdef12",
        }

        build_status = MagicMock()
        build_status.status_code = 200
        build_status.json.return_value = {
            "status": "succeeded",
            "image": "registry.fly.io/aria-intel:deployment-abcdef12",
        }

        # Canary succeeds
        canary_response = MagicMock()
        canary_response.status_code = 201
        canary_response.json.return_value = {"id": "canary_abc"}

        smoke_response = MagicMock()
        smoke_response.status_code = 200

        # Fleet update succeeds
        list_response = MagicMock()
        list_response.status_code = 200
        list_response.json.return_value = [
            {"id": "machine_1", "config": {"image": "old", "env": {}}},
        ]

        get_response = MagicMock()
        get_response.status_code = 200
        get_response.json.return_value = {
            "id": "machine_1",
            "config": {"image": "old", "env": {}},
        }

        update_response = MagicMock()
        update_response.status_code = 200

        destroy_response = MagicMock()
        destroy_response.status_code = 200

        # Live verification fails (wrong SHA)
        live_response = MagicMock()
        live_response.status_code = 200
        live_response.text = '{"build_rev": "deadbeef", "status": "ok"}'

        client.post = AsyncMock(side_effect=[
            build_response, canary_response, update_response,
        ])
        client.get = AsyncMock(side_effect=[
            build_status, smoke_response, list_response,
            get_response, live_response,
        ])
        client.delete = AsyncMock(return_value=destroy_response)

        deployer = MachinesDeployer(
            aria_service_url="https://aria-intel.fly.dev",
            repo_path=git_repo,
            http_client=client,
        )

        result = await deployer.deploy(
            app="aria-intel",
            r_number=1183,
            commit_sha="abcdef1234567890abcdef1234567890abcdef12",
            skip_push_guard=True,
        )

        assert result.success is False
        assert "live verification" in (result.error or "").lower()


# ── Rollback Tests ──────────────────────────────────────────────────────────


class TestRollback:
    """Rollback redeploys a previous image."""

    @pytest.mark.asyncio
    async def test_rollback_success(self, tmp_path: Path) -> None:
        """Rollback succeeds when deploy record exists."""
        client = AsyncMock(spec=httpx.AsyncClient)

        # Create deploy history
        history_dir = tmp_path / "deploy_history"
        history_dir.mkdir(parents=True)
        (history_dir / "aria-intel.json").write_text(
            json.dumps([
                {
                    "r_number": 1183,
                    "app": "aria-intel",
                    "image": "registry.fly.io/aria-intel:new",
                },
                {
                    "r_number": 1182,
                    "app": "aria-intel",
                    "image": "registry.fly.io/aria-intel:old",
                },
            ]),
            encoding="utf-8",
        )

        # Mock fleet update
        list_response = MagicMock()
        list_response.status_code = 200
        list_response.json.return_value = [
            {"id": "machine_1", "config": {"image": "new", "env": {}}},
        ]

        get_response = MagicMock()
        get_response.status_code = 200
        get_response.json.return_value = {
            "id": "machine_1",
            "config": {"image": "new", "env": {}},
        }

        update_response = MagicMock()
        update_response.status_code = 200

        client.get = AsyncMock(side_effect=[list_response, get_response])
        client.post = AsyncMock(return_value=update_response)

        deployer = MachinesDeployer(
            aria_service_url="https://aria-intel.fly.dev",
            repo_path=tmp_path,
            http_client=client,
        )

        with patch(
            "aria_service.autonomous.machines_deployer.DEPLOY_HISTORY_DIR",
            history_dir,
        ):
            result = await deployer.rollback(
                app="aria-intel", target_r_number=1182,
            )

        assert result.success is True
        assert result.image == "registry.fly.io/aria-intel:old"

    @pytest.mark.asyncio
    async def test_rollback_fails_when_no_record(self, tmp_path: Path) -> None:
        """Rollback fails when no deploy record exists."""
        deployer = MachinesDeployer(
            aria_service_url="https://aria-intel.fly.dev",
            repo_path=tmp_path,
            http_client=AsyncMock(spec=httpx.AsyncClient),
        )

        result = await deployer.rollback(
            app="aria-intel", target_r_number=9999,
        )

        assert result.success is False
        assert "No deploy record" in (result.error or "")


# ── DeployResult Tests ──────────────────────────────────────────────────────


class TestDeployResult:
    """DeployResult dataclass works correctly."""

    def test_success_result(self) -> None:
        """DeployResult stores success state."""
        result = DeployResult(
            success=True,
            app="aria-intel",
            image="registry.fly.io/aria-intel:abc",
            r_number=1183,
            commit_sha="abcdef1234567890abcdef1234567890abcdef12",
            duration_s=42.5,
        )
        assert result.success is True
        assert result.app == "aria-intel"
        assert result.r_number == 1183
        assert result.duration_s == 42.5

    def test_failure_result(self) -> None:
        """DeployResult stores failure state."""
        result = DeployResult(
            success=False,
            app="aria-intel",
            error="Build failed",
        )
        assert result.success is False
        assert result.error == "Build failed"

    def test_default_timestamp(self) -> None:
        """DeployResult has a default timestamp."""
        result = DeployResult(success=True)
        assert result.deployed_at is not None
        assert "T" in result.deployed_at  # ISO format
