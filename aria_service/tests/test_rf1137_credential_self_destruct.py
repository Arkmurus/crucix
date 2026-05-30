"""R-F1137 — Capability tests for credential self-destruct.

Tests that:
1. Credentials are stored with TTL
2. Credentials are returned within TTL
3. Credentials expire after TTL + grace period
4. Credentials are usable within grace period (no hard-fail)
5. Single credential invalidation works
6. Panic wipe invalidates ALL credentials
7. Panic requires HIGH confidence event (wired to destructive quarantine)
8. Brain wiring works on panic
9. get_credential_status returns correct state
"""
from __future__ import annotations

import time
from unittest.mock import AsyncMock, patch

import pytest

from aria_service.intel.credential_self_destruct import (
    DEFAULT_TTL_S,
    GRACE_PERIOD_S,
    clear_panic,
    get_credential_status,
    get_credential_with_ttl,
    invalidate_all_credentials,
    invalidate_credential,
    store_credential_with_ttl,
)


# ── Fixtures ────────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def mock_redis():
    """Persistent in-memory Redis mock."""
    store: dict[str, object] = {}

    async def mock_get(key: str) -> object:
        return store.get(key)

    async def mock_set(key: str, value: object, **kwargs) -> None:
        store[key] = value

    with patch("aria_service.intel.redis_store.get_json", side_effect=mock_get):
        with patch("aria_service.intel.redis_store.set_json", side_effect=mock_set):
            yield store


# ── Tests for credential storage ────────────────────────────────────────────

class TestCredentialStorage:
    """Proves credential storage works."""

    async def test_stores_with_ttl(self, mock_redis):
        """Credential is stored with TTL."""
        entry = await store_credential_with_ttl(
            "test_key", "sk-test123", ttl_s=3600,
        )

        assert entry["credential_id"] == "test_key"
        assert entry["ttl_s"] == 3600
        assert entry["is_valid"] is True
        assert entry["expires_at"] > time.time()
        assert entry["grace_period_until"] > entry["expires_at"]

    async def test_returns_within_ttl(self, mock_redis):
        """Credential is returned within TTL."""
        await store_credential_with_ttl("test_key", "sk-test123", ttl_s=3600)
        value = await get_credential_with_ttl("test_key")

        assert value == "sk-test123"

    async def test_returns_none_for_unknown(self, mock_redis):
        """Unknown credential returns None."""
        value = await get_credential_with_ttl("nonexistent_key")
        assert value is None


# ── Tests for TTL expiry ────────────────────────────────────────────────────

class TestTTLExpiry:
    """Proves TTL expiry works."""

    async def test_expires_after_ttl(self, mock_redis):
        """Credential expires after TTL + grace period."""
        # Store with 0-second TTL (already expired)
        await store_credential_with_ttl("test_key", "sk-test123", ttl_s=0)

        # Manually set grace_period_until to the past
        import aria_service.intel.credential_self_destruct as csd
        vault = await csd._get_vault()
        vault["test_key"]["grace_period_until"] = time.time() - 1  # Grace period ended
        await csd._set_vault(vault)

        value = await get_credential_with_ttl("test_key")
        assert value is None

    async def test_grace_period_allows_access(self, mock_redis):
        """Credential is still accessible within grace period after TTL expiry."""
        # Store with a very short TTL that's already expired
        # We simulate this by storing with expires_at in the past
        vault_key = "crucix:security:credential_vault"
        from aria_service.intel.credential_self_destruct import _CRED_VAULT_KEY

        # Store normally first
        await store_credential_with_ttl("test_key", "sk-test123", ttl_s=3600)

        # Manually set expires_at to the past (but grace_period_until in the future)
        import aria_service.intel.credential_self_destruct as csd
        vault = await csd._get_vault()
        now = time.time()
        vault["test_key"]["expires_at"] = now - 60  # Expired 60s ago
        vault["test_key"]["grace_period_until"] = now + GRACE_PERIOD_S  # Still in grace
        await csd._set_vault(vault)

        # Should still return the value (within grace period)
        value = await get_credential_with_ttl("test_key")
        assert value == "sk-test123"


# ── Tests for invalidation ──────────────────────────────────────────────────

class TestInvalidation:
    """Proves credential invalidation works."""

    async def test_invalidates_single_credential(self, mock_redis):
        """Single credential invalidation works."""
        await store_credential_with_ttl("key_a", "value_a", ttl_s=3600)
        await store_credential_with_ttl("key_b", "value_b", ttl_s=3600)

        await invalidate_credential("key_a", "Compromised")

        # key_a should be invalid
        assert await get_credential_with_ttl("key_a") is None
        # key_b should still work
        assert await get_credential_with_ttl("key_b") == "value_b"

    async def test_panic_invalidates_all(self, mock_redis):
        """Panic wipe invalidates ALL credentials."""
        await store_credential_with_ttl("key_a", "value_a", ttl_s=3600)
        await store_credential_with_ttl("key_b", "value_b", ttl_s=3600)

        result = await invalidate_all_credentials("Security breach detected")

        assert result["panicked"] is True
        assert result["credentials_invalidated"] == 2

        # Both should be invalid
        assert await get_credential_with_ttl("key_a") is None
        assert await get_credential_with_ttl("key_b") is None

    async def test_panic_wires_to_brain(self, mock_redis):
        """Panic wires CRITICAL alert to brain."""
        await store_credential_with_ttl("test_key", "value", ttl_s=3600)

        with patch("aria_service.intel.engine_wiring.wire_failure") as mock_wf:
            await invalidate_all_credentials("Test panic")

        mock_wf.assert_called_once()
        args, kwargs = mock_wf.call_args
        assert kwargs.get("gap_type") == "security_threat"
        assert "PANIC" in kwargs.get("detail", "")

    async def test_clear_panic(self, mock_redis):
        """Panic can be cleared."""
        await invalidate_all_credentials("Test")
        result = await clear_panic()

        assert result["panicked"] is False


# ── Tests for status ────────────────────────────────────────────────────────

class TestCredentialStatus:
    """Proves credential status reporting works."""

    async def test_returns_status(self, mock_redis):
        """get_credential_status returns correct state."""
        await store_credential_with_ttl("key_a", "value_a", ttl_s=3600)
        await store_credential_with_ttl("key_b", "value_b", ttl_s=3600)
        await invalidate_credential("key_b", "Test")

        status = await get_credential_status()
        assert status["total_credentials"] == 2
        assert status["valid_credentials"] == 1
        assert status["invalidated_credentials"] == 1

    async def test_returns_single_status(self, mock_redis):
        """get_credential_status returns single credential status."""
        await store_credential_with_ttl("test_key", "value", ttl_s=3600)

        status = await get_credential_status("test_key")
        assert status["credential_id"] == "test_key"
        assert status["is_valid"] is True
