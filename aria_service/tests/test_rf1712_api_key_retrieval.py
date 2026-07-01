"""R-F1712 — close the autonomous-onboarding loop: after an account is created,
log into the dashboard, RETRIEVE the API key, store it, and prove it goes live.

Before this, _register_for_api_key returned a FAKE success ("API key may need to
be obtained from the dashboard") without ever getting a key — the back-half of
the operator's flow didn't exist. R-F1712 adds login + dashboard key-retrieval
(playwright_engine.login_and_get_api_key) wired to key_resolver.store_obtained_key.

The Playwright/browser step is stubbed (no real network); these tests prove the
CHAIN — register → retrieve → store → resolve_key returns it — and the honesty
contract: no key retrieved ⇒ NO success claimed (R-F1702).
"""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from aria_service.intel import portal_registry, key_resolver
from aria_service.intel.portal_registry import PortalDef
from aria_service.intel.scraper import playwright_engine


def _portal() -> PortalDef:
    return PortalDef(
        id="testportal",
        name="Test Portal",
        url="https://testportal.example",
        description="test",
        registration_type="api_key",
        signup_fields=[("email", "email", "email")],
        login_fields=[("email", "email", "email"), ("password", "password", "password")],
        login_path="/login",
        api_key_path="/account/api",
        api_key_selector="#api-key",
    )


def _cred_store_patches(initial: dict):
    store = dict(initial)

    async def _store(pid, cred):
        store[pid] = {**store.get(pid, {}), **cred}

    async def _get(pid):
        return store.get(pid)

    return store, _store, _get


@pytest.mark.asyncio
async def test_full_chain_register_retrieve_store_activate():
    """The whole loop: account created → key retrieved from dashboard → stored →
    resolve_key returns it (live, no operator)."""
    portal = _portal()
    store, _store, _get = _cred_store_patches(
        {"testportal": {"email": "aria@imaria.io", "password": "generatedpw12345"}}
    )
    with patch.object(portal_registry, "_register_via_email_form",
                      AsyncMock(return_value={"success": True})), \
         patch.object(portal_registry, "get_credential", _get), \
         patch.object(portal_registry, "store_credential", _store), \
         patch.object(playwright_engine, "login_and_get_api_key",
                      AsyncMock(return_value={"success": True, "api_key": "sk-live-abc123",
                                              "final_url": "x", "error": ""})):
        result = await portal_registry._register_for_api_key(portal)
        assert result["success"] is True
        assert result["api_key_obtained"] is True
        # Activation: the obtained key is now resolvable from the vault (no env var).
        got = await key_resolver.resolve_key(["TESTPORTAL_API_KEY"], portal_id="testportal")
    assert got == "sk-live-abc123"


@pytest.mark.asyncio
async def test_no_key_retrieved_is_honest_failure_not_fake_success():
    """If the dashboard yields no key, do NOT claim success (R-F1702 honesty)."""
    portal = _portal()
    _, _store, _get = _cred_store_patches(
        {"testportal": {"email": "aria@imaria.io", "password": "generatedpw12345"}}
    )
    with patch.object(portal_registry, "_register_via_email_form",
                      AsyncMock(return_value={"success": True})), \
         patch.object(portal_registry, "get_credential", _get), \
         patch.object(portal_registry, "store_credential", _store), \
         patch.object(playwright_engine, "login_and_get_api_key",
                      AsyncMock(return_value={"success": False, "api_key": "",
                                              "final_url": "", "error": "no key on dashboard"})):
        result = await portal_registry._register_for_api_key(portal)
    assert result["success"] is False
    assert result["api_key_obtained"] is False
    assert result["requires_operator"] is True


@pytest.mark.asyncio
async def test_no_retrieval_config_does_not_fake_success():
    """A portal with no api_key_path/selector cannot self-retrieve — honest."""
    portal = _portal()
    portal.api_key_path = ""
    portal.api_key_selector = ""
    _, _store, _get = _cred_store_patches({"testportal": {}})
    with patch.object(portal_registry, "_register_via_email_form",
                      AsyncMock(return_value={"success": True})), \
         patch.object(portal_registry, "get_credential", _get), \
         patch.object(portal_registry, "store_credential", _store):
        result = await portal_registry._register_for_api_key(portal)
    assert result["success"] is False
    assert result["api_key_obtained"] is False


@pytest.mark.asyncio
async def test_retrieve_builds_login_data_from_login_fields():
    """_retrieve_api_key resolves login_fields value-sources from stored creds
    and passes them to the browser routine."""
    portal = _portal()
    _, _store, _get = _cred_store_patches(
        {"testportal": {"email": "aria@imaria.io", "password": "pw-secret-999"}}
    )
    captured = {}

    async def _fake_login(login_url, login_data, api_key_url, **kw):
        captured["login_url"] = login_url
        captured["login_data"] = login_data
        captured["api_key_url"] = api_key_url
        return {"success": True, "api_key": "k", "final_url": "", "error": ""}

    with patch.object(portal_registry, "get_credential", _get), \
         patch.object(playwright_engine, "login_and_get_api_key", _fake_login):
        key = await portal_registry._retrieve_api_key(portal)
    # R-F1715: _retrieve_api_key now returns a candidate LIST (verified downstream).
    assert key == ["k"]
    assert captured["login_data"]["email"] == "aria@imaria.io"
    assert captured["login_data"]["password"] == "pw-secret-999"
    assert captured["login_url"] == "https://testportal.example/login"
    assert captured["api_key_url"] == "https://testportal.example/account/api"
