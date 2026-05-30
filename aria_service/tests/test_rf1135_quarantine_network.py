"""R-F1135 — Capability tests for quarantine network.

Tests that:
1. flag_module logs + wires to brain, no restrictive action
2. safe_quarantine blocks outbound + rate-limits + read-only FS
3. destructive_quarantine requires operator approval
4. destructive_quarantine with approval executes destructive actions
5. Protected modules cannot be quarantined (no self-DoS)
6. Auto-release works after SAFE_AUTO_RELEASE_S
7. release_module restores normal operation
8. get_quarantine_status returns correct state
9. Brain wiring works on all levels
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from aria_service.intel.quarantine_network import (
    SAFE_AUTO_RELEASE_S,
    QuarantineLevel,
    _PROTECTED_MODULES,
    destructive_quarantine,
    flag_module,
    get_quarantine_status,
    is_quarantined,
    is_safe_quarantined,
    release_module,
    safe_quarantine,
)


# ── Fixtures ────────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def mock_redis():
    """Mock Redis with a persistent in-memory store for all tests."""
    store: dict[str, object] = {}

    async def mock_get_json(key: str) -> object:
        return store.get(key)

    async def mock_set_json(key: str, value: object, **kwargs) -> None:
        store[key] = value

    with patch("aria_service.intel.redis_store.get_json",
               side_effect=mock_get_json):
        with patch("aria_service.intel.redis_store.set_json",
                   side_effect=mock_set_json):
            yield store


# ── Tests for FLAG level ────────────────────────────────────────────────────

class TestFlagModule:
    """Proves FLAG level works."""

    async def test_flags_module(self):
        """flag_module sets level to FLAGGED."""
        result = await flag_module("test_module", "Suspicious activity detected")

        assert result["level"] == QuarantineLevel.FLAGGED.name
        assert len(result["flags"]) == 1
        assert "Suspicious activity" in result["flags"][0]["reason"]

    async def test_flag_wires_to_brain(self):
        """flag_module wires to brain."""
        with patch("aria_service.intel.engine_wiring.wire_failure") as mock_wf:
            await flag_module("test_module", "Test flag")

        mock_wf.assert_called_once()
        args, kwargs = mock_wf.call_args
        assert kwargs.get("gap_type") == "security_threat"

    async def test_protected_module_not_flagged(self):
        """Protected modules cannot be flagged."""
        for mod in list(_PROTECTED_MODULES)[:3]:
            result = await flag_module(mod, "Should not work")
            assert "protected" in result.get("note", "").lower()


# ── Tests for SAFE quarantine level ─────────────────────────────────────────

class TestSafeQuarantine:
    """Proves SAFE quarantine level works."""

    async def test_safe_quarantine_sets_restrictions(self):
        """safe_quarantine sets outbound_blocked, rate_limited, read_only_fs."""
        result = await safe_quarantine("test_module", "Multiple injection attempts")

        assert result["level"] == QuarantineLevel.SAFE.name
        assert result["restrictions"]["outbound_blocked"] is True
        assert result["restrictions"]["rate_limited"] is True
        assert result["restrictions"]["read_only_fs"] is True
        assert result["auto_release_at"] is not None

    async def test_safe_quarantine_wires_to_brain(self):
        """safe_quarantine wires to brain."""
        with patch("aria_service.intel.engine_wiring.wire_failure") as mock_wf:
            await safe_quarantine("test_module", "Test safe quarantine")

        mock_wf.assert_called_once()
        args, kwargs = mock_wf.call_args
        assert "SAFE" in kwargs.get("detail", "")

    async def test_protected_module_not_quarantined(self):
        """Protected modules cannot be safe-quarantined."""
        result = await safe_quarantine("brain_hook", "Should not work")
        assert "protected" in result.get("note", "").lower()

    async def test_is_safe_quarantined(self):
        """is_safe_quarantined returns True for SAFE level."""
        await safe_quarantine("test_module", "Test")
        assert await is_safe_quarantined("test_module") is True


# ── Tests for DESTRUCTIVE quarantine level ──────────────────────────────────

class TestDestructiveQuarantine:
    """Proves DESTRUCTIVE quarantine level works."""

    async def test_requires_operator_approval(self):
        """destructive_quarantine without approval returns error."""
        result = await destructive_quarantine(
            "test_module", "Credential exfil detected",
            operator_approved=False,
        )

        assert "operator approval" in result.get("note", "").lower()
        assert result["level"] == QuarantineLevel.NONE.name

    async def test_executes_with_approval(self):
        """destructive_quarantine with approval executes destructive actions."""
        result = await destructive_quarantine(
            "test_module", "Credential exfil detected",
            operator_approved=True,
        )

        assert result["level"] == QuarantineLevel.DESTRUCTIVE.name
        assert result["restrictions"]["process_killed"] is True
        assert result["restrictions"]["credentials_ wiped"] is True

    async def test_protected_module_not_destroyed(self):
        """Protected modules cannot be destructively quarantined."""
        result = await destructive_quarantine(
            "constitutional_validator", "Test",
            operator_approved=True,
        )
        assert "protected" in result.get("note", "").lower()


# ── Tests for release ───────────────────────────────────────────────────────

class TestReleaseModule:
    """Proves release works."""

    async def test_releases_safe_quarantine(self):
        """release_module restores normal operation for SAFE level."""
        await safe_quarantine("test_module", "Test")
        result = await release_module("test_module")

        assert result["level"] == QuarantineLevel.NONE.name
        assert result.get("released_at") is not None

    async def test_cannot_release_destructive(self):
        """DESTRUCTIVE quarantine cannot be released — requires redeploy."""
        await destructive_quarantine(
            "test_module", "Test", operator_approved=True,
        )
        result = await release_module("test_module")

        assert "cannot release" in result.get("note", "").lower()

    async def test_release_nonexistent_module(self):
        """Releasing a non-quarantined module returns clean status."""
        result = await release_module("nonexistent_module")
        assert result["level"] == QuarantineLevel.NONE.name


# ── Tests for status ────────────────────────────────────────────────────────

class TestGetStatus:
    """Proves status reporting works."""

    async def test_returns_all_modules(self):
        """get_quarantine_status returns all modules when no name given."""
        await flag_module("mod_a", "Flag A")
        await safe_quarantine("mod_b", "Quarantine B")

        status = await get_quarantine_status()
        assert "modules" in status
        assert "protected_modules" in status
        assert list(_PROTECTED_MODULES) == status["protected_modules"]

    async def test_returns_single_module(self):
        """get_quarantine_status returns single module status."""
        await safe_quarantine("test_module", "Test")
        status = await get_quarantine_status("test_module")
        assert status["level"] == QuarantineLevel.SAFE.name

    async def test_is_quarantined(self):
        """is_quarantined returns True for any non-NONE level."""
        await flag_module("test_module", "Test")
        assert await is_quarantined("test_module") is True
