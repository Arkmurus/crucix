"""R-F1133 — Capability tests for dependency integrity chain.

Tests that:
1. _get_installed_packages returns packages from site-packages
2. _compute_package_hash produces deterministic hashes
3. _check_typosquat detects known typosquat patterns
4. record_dependency_snapshot stores SBOM in Redis
5. verify_dependency_integrity detects hash mismatches
6. verify_dependency_integrity passes when hashes match
7. Brain wiring works on both success and failure
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from aria_service.intel.dependency_integrity import (
    TYPOSQUAT_PATTERNS,
    IntegrityResult,
    _check_typosquat,
    _compute_package_hash,
    _get_installed_packages,
    record_dependency_snapshot,
    verify_dependency_integrity,
)


# ── Tests for package enumeration ───────────────────────────────────────────

class TestGetInstalledPackages:
    """Proves package enumeration works."""

    def test_returns_list(self):
        """_get_installed_packages returns a list."""
        packages = _get_installed_packages()
        assert isinstance(packages, list)
        # Should at least find some packages (pytest, etc.)
        assert len(packages) > 0

    def test_packages_have_name_and_version(self):
        """Each package has name and version fields."""
        packages = _get_installed_packages()
        for pkg in packages:
            assert "name" in pkg
            assert "version" in pkg


# ── Tests for hash computation ──────────────────────────────────────────────

class TestPackageHash:
    """Proves hash computation is deterministic."""

    def test_deterministic(self):
        """Same name+version produces same hash."""
        h1 = _compute_package_hash("requests", "2.31.0")
        h2 = _compute_package_hash("requests", "2.31.0")
        assert h1 == h2

    def test_different_versions_different_hashes(self):
        """Different versions produce different hashes."""
        h1 = _compute_package_hash("requests", "2.31.0")
        h2 = _compute_package_hash("requests", "2.32.0")
        assert h1 != h2

    def test_different_packages_different_hashes(self):
        """Different packages produce different hashes."""
        h1 = _compute_package_hash("requests", "2.31.0")
        h2 = _compute_package_hash("httpx", "0.28.0")
        assert h1 != h2


# ── Tests for typosquat detection ───────────────────────────────────────────

class TestTyposquatDetection:
    """Proves typosquat detection works."""

    def test_detects_known_typosquat(self):
        """Known typosquat patterns are detected."""
        for legitimate, typosquat in TYPOSQUAT_PATTERNS:
            result = _check_typosquat(typosquat)
            assert result == legitimate, (
                f"Failed to detect typosquat '{typosquat}' -> '{legitimate}'"
            )

    def test_legitimate_package_not_flagged(self):
        """Legitimate package names are not flagged."""
        for legitimate, _ in TYPOSQUAT_PATTERNS:
            result = _check_typosquat(legitimate)
            assert result is None, (
                f"False positive: '{legitimate}' flagged as typosquat"
            )

    def test_unknown_package_not_flagged(self):
        """Unknown package names are not flagged."""
        result = _check_typosquat("some_random_package_xyz")
        assert result is None


# ── Tests for snapshot recording ────────────────────────────────────────────

class TestRecordSnapshot:
    """Proves snapshot recording works."""

    async def test_records_packages(self):
        """record_dependency_snapshot returns a snapshot with packages."""
        with patch("aria_service.intel.redis_store.set_json",
                   new_callable=AsyncMock) as mock_set:
            mock_set.side_effect = Exception("Redis not available — testing fallback")
            snapshot = await record_dependency_snapshot()

        assert "packages" in snapshot
        assert snapshot["total_packages"] > 0
        assert "recorded_at" in snapshot
        assert "python_version" in snapshot

    async def test_stores_in_redis(self):
        """Snapshot is stored in Redis."""
        mock_set = AsyncMock()
        with patch("aria_service.intel.redis_store.set_json", mock_set):
            await record_dependency_snapshot()

        mock_set.assert_called_once()
        args, kwargs = mock_set.call_args
        assert args[0] == "crucix:security:sbom"
        assert "packages" in args[1]

    async def test_wires_to_brain(self):
        """Snapshot recording wires to brain."""
        with patch("aria_service.intel.redis_store.set_json",
                   new_callable=AsyncMock) as mock_set:
            mock_set.side_effect = Exception("Redis not available")
            with patch("aria_service.intel.engine_wiring.wire_success") as mock_ws:
                await record_dependency_snapshot()

        mock_ws.assert_called_once()
        args, kwargs = mock_ws.call_args
        assert kwargs.get("module") == "dependency_integrity"


# ── Tests for integrity verification ────────────────────────────────────────

class TestVerifyIntegrity:
    """Proves integrity verification works."""

    async def test_passes_when_hashes_match(self):
        """Verification passes when all hashes match."""
        current = _get_installed_packages()
        mock_snapshot = {
            "packages": [
                {"name": p["name"], "version": p["version"],
                 "hash": _compute_package_hash(p["name"], p["version"])}
                for p in current[:5]
            ],
        }

        with patch("aria_service.intel.redis_store.get_json",
                   new_callable=AsyncMock, return_value=mock_snapshot):
            result = await verify_dependency_integrity()

        assert result.valid is True
        assert "match" in result.reason.lower()

    async def test_fails_on_hash_mismatch(self):
        """Verification fails when a hash doesn't match."""
        mock_snapshot = {
            "packages": [
                {"name": "some_package", "version": "1.0.0",
                 "hash": "wrong_hash_12345"},
            ],
        }

        with patch("aria_service.intel.redis_store.get_json",
                   new_callable=AsyncMock, return_value=mock_snapshot):
            result = await verify_dependency_integrity()

        assert result.valid is False
        assert len(result.details) > 0

    async def test_fails_when_no_snapshot(self):
        """Verification fails when no snapshot exists."""
        with patch("aria_service.intel.redis_store.get_json",
                   new_callable=AsyncMock, return_value=None):
            result = await verify_dependency_integrity()

        assert result.valid is False
        assert "No dependency snapshot" in result.reason

    async def test_wires_failure_to_brain(self):
        """Failed verification wires to brain."""
        mock_snapshot = {
            "packages": [
                {"name": "nonexistent_pkg_xyz", "version": "1.0.0",
                 "hash": "bad_hash"},
            ],
        }

        with patch("aria_service.intel.redis_store.get_json",
                   new_callable=AsyncMock, return_value=mock_snapshot):
            with patch("aria_service.intel.engine_wiring.wire_failure") as mock_wf:
                result = await verify_dependency_integrity()

        assert result.valid is False
        mock_wf.assert_called_once()
        args, kwargs = mock_wf.call_args
        assert kwargs.get("gap_type") == "security_threat"
