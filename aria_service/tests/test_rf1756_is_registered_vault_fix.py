"""R-F1756: Capability test for is_registered vault-based fix.

Proves the root-cause fix: is_registered() now uses the VAULT as source
of truth, not Redis credentials. A portal with Redis creds but vault
status != 'registered' must return False (no fabrication).
"""
import pytest
from unittest.mock import patch, MagicMock

from aria_service.intel import portal_registry as pr
from aria_service.intel import agent_signup_vault as asv


@pytest.mark.asyncio
async def test_is_registered_returns_false_when_vault_has_no_entry():
    """A portal with NO vault entry at all must return False,
    even if Redis credentials exist (the old broken behavior)."""
    mock_vault = MagicMock()
    mock_vault.get.return_value = None  # no vault entry

    with patch.object(asv, "get_vault", return_value=mock_vault):
        result = await pr.is_registered("some_portal")
    assert result is False, "No vault entry -> is_registered=False"


@pytest.mark.asyncio
async def test_is_registered_returns_false_when_vault_status_is_pending():
    """A portal with vault status='pending' must return False.
    This is the core anti-fabrication property: credentials may exist
    in Redis (from _audit_preparation) but the vault says pending."""
    mock_vault = MagicMock()
    mock_vault.get.return_value = {"status": "pending", "site_id": "test_portal"}

    with patch.object(asv, "get_vault", return_value=mock_vault):
        result = await pr.is_registered("test_portal")
    assert result is False, "Vault status=pending -> is_registered=False"


@pytest.mark.asyncio
async def test_is_registered_returns_false_when_vault_status_is_needs_operator():
    """A portal with vault status='needs_operator' must return False."""
    mock_vault = MagicMock()
    mock_vault.get.return_value = {"status": "needs_operator", "site_id": "test_portal"}

    with patch.object(asv, "get_vault", return_value=mock_vault):
        result = await pr.is_registered("test_portal")
    assert result is False, "Vault status=needs_operator -> is_registered=False"


@pytest.mark.asyncio
async def test_is_registered_returns_true_when_vault_status_is_registered():
    """A portal with vault status='registered' must return True.
    This is the honest path: vault says registered -> is_registered=True."""
    mock_vault = MagicMock()
    mock_vault.get.return_value = {"status": "registered", "site_id": "test_portal"}

    with patch.object(asv, "get_vault", return_value=mock_vault):
        result = await pr.is_registered("test_portal")
    assert result is True, "Vault status=registered -> is_registered=True"


@pytest.mark.asyncio
async def test_is_registered_returns_true_when_vault_status_is_verified():
    """A portal with vault status='verified' must return True."""
    mock_vault = MagicMock()
    mock_vault.get.return_value = {"status": "verified", "site_id": "test_portal"}

    with patch.object(asv, "get_vault", return_value=mock_vault):
        result = await pr.is_registered("test_portal")
    assert result is True, "Vault status=verified -> is_registered=True"


@pytest.mark.asyncio
async def test_is_registered_falls_back_to_false_on_vault_exception():
    """If the vault throws, is_registered must return False (fail safe)."""
    mock_vault = MagicMock()
    mock_vault.get.side_effect = Exception("vault unavailable")

    with patch.object(asv, "get_vault", return_value=mock_vault):
        result = await pr.is_registered("test_portal")
    assert result is False, "Vault exception -> is_registered=False (fail safe)"
