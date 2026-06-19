"""R-F1711 — autonomous-onboarding plumbing:
  (1) email verification reads the keys email_reader actually returns, so a real
      confirmation email can be matched + the link visited (was a no-op bug); and
  (2) a generic env-first-then-vault key accessor so an OBTAINED key goes live
      with no restart/operator (the 'inject the secret' step).
"""
from __future__ import annotations

import types
from unittest.mock import AsyncMock, patch

import pytest

from aria_service.intel import key_resolver, portal_registry, email_reader


# ── (1) email verification field-name fix ───────────────────────────────────
@pytest.mark.asyncio
async def test_email_verification_matches_real_reader_keys_and_visits_link():
    portal = types.SimpleNamespace(id="acme", verify_email_domain="acme.io")
    good = {
        "from_addr": "noreply@acme.io",
        "to_addr": "aria@arkmurus.com",
        "subject": "Confirm your account",
        "body_text": "Please confirm: https://acme.io/verify?token=abc123",
    }
    visited = {}

    class _Client:
        async def __aenter__(self):
            return self
        async def __aexit__(self, *a):
            return False
        async def get(self, url):
            visited["url"] = url
            return types.SimpleNamespace(status_code=200)

    with patch.object(portal_registry.asyncio, "sleep", AsyncMock()), \
         patch.object(email_reader, "read_emails", AsyncMock(return_value=[good])), \
         patch.object(portal_registry, "httpx",
                      types.SimpleNamespace(AsyncClient=lambda *a, **k: _Client())):
        ok = await portal_registry._handle_email_verification(
            portal, {"email": "aria@arkmurus.com"},
        )
    assert ok is True
    assert "acme.io/verify" in visited.get("url", "")


@pytest.mark.asyncio
async def test_email_verification_old_keys_would_not_match():
    """Pre-R-F1711 the code read from/to/body — an email carrying ONLY those
    keys is never matched → verification fails. Guards the regression."""
    portal = types.SimpleNamespace(id="acme", verify_email_domain="acme.io")
    old = {
        "from": "noreply@acme.io",
        "to": "aria@arkmurus.com",
        "body": "confirm https://acme.io/verify?t=1",
        "subject": "x",
    }
    with patch.object(portal_registry.asyncio, "sleep", AsyncMock()), \
         patch.object(email_reader, "read_emails", AsyncMock(return_value=[old])), \
         patch.object(portal_registry, "httpx", types.SimpleNamespace(AsyncClient=AsyncMock())):
        ok = await portal_registry._handle_email_verification(
            portal, {"email": "aria@arkmurus.com"},
        )
    assert ok is False


# ── (2) env-first-then-vault key accessor ───────────────────────────────────
@pytest.mark.asyncio
async def test_resolve_key_prefers_env_and_never_hits_vault():
    with patch.dict("os.environ", {"PROVIDER_KEY": "env-value"}, clear=False), \
         patch.object(portal_registry, "get_credential",
                      AsyncMock(side_effect=AssertionError("vault must not be consulted"))):
        got = await key_resolver.resolve_key(["PROVIDER_KEY"], portal_id="provider_x")
    assert got == "env-value"


@pytest.mark.asyncio
async def test_obtained_key_goes_live_via_vault_fallback(monkeypatch):
    """The autonomous-injection contract: store an obtained key → the very next
    resolve_key picks it up (no env var, no restart)."""
    monkeypatch.delenv("PROVIDER_KEY_2", raising=False)
    store: dict[str, dict] = {}

    async def _store(pid, cred):
        store[pid] = cred

    async def _get(pid):
        return store.get(pid)

    with patch.object(portal_registry, "store_credential", _store), \
         patch.object(portal_registry, "get_credential", _get):
        assert await key_resolver.store_obtained_key("provider_2", "obtained-key") is True
        got = await key_resolver.resolve_key(["PROVIDER_KEY_2"], portal_id="provider_2")
    assert got == "obtained-key"


@pytest.mark.asyncio
async def test_store_obtained_key_rejects_empty():
    assert await key_resolver.store_obtained_key("provider", "") is False
    assert await key_resolver.store_obtained_key("", "key") is False
