"""R-F1714 — NewsAPI is configured for FULL autonomous onboarding end-to-end.

NewsAPI was chosen from the live PORTALS list as the template: free dev tier,
issues the API key on /account immediately after registration (NO email
verification → no IMAP dependency). The prior config used GUESSED lowercase
field names (email/password/name) that never matched the real form, so it
silently failed — the exact "ARIA struggling with the vault" symptom.

R-F1714 sets the REAL field names read from the live newsapi.org pages
(register: Email/FirstName/Password_Value + entity radio + terms checkbox;
login: Email/Password) and the /account key regex, and resolves the login
password from the signup field that stored it.

Browser stubbed (no real network/account); this proves the CHAIN with the REAL
config: register → log in (correct creds) → read key off /account → store →
resolve_key serves it.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from aria_service.intel import portal_registry, key_resolver
from aria_service.intel.scraper import playwright_engine

_KEY = "a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6"  # 32-char lowercase hex (NewsAPI format)


def _newsapi():
    return next(p for p in portal_registry.PORTALS if p.id == "newsapi")


@pytest.mark.asyncio
async def test_newsapi_full_chain_uses_real_creds_and_activates():
    portal = _newsapi()
    # registration stores form data keyed by the SIGNUP selectors:
    store = {"newsapi": {"Email": "aria@arkmurus.com",
                         "FirstName": "ARIA Research",
                         "Password_Value": "Gx7-generated-pw-91237"}}

    async def _store(pid, cred):
        store[pid] = {**store.get(pid, {}), **cred}

    async def _get(pid):
        return store.get(pid)

    captured = {}

    async def _fake_login(login_url, login_data, api_key_url, **kw):
        captured["login_url"] = login_url
        captured["api_key_url"] = api_key_url
        captured["login_data"] = login_data
        captured["key_regex"] = kw.get("key_regex")
        return {"success": True, "api_key": _KEY, "final_url": "", "error": ""}

    with patch.object(portal_registry, "_register_via_email_form",
                      AsyncMock(return_value={"success": True})), \
         patch.object(portal_registry, "get_credential", _get), \
         patch.object(portal_registry, "store_credential", _store), \
         patch.object(playwright_engine, "login_and_get_api_key", _fake_login):
        result = await portal_registry._register_for_api_key(portal)
        assert result["success"] is True
        assert result["api_key_obtained"] is True
        got = await key_resolver.resolve_key(["NEWSAPI_API_KEY"], portal_id="newsapi")

    # Activated: the obtained key is now live via the vault.
    assert got == _KEY
    # Logged in with the RIGHT creds — password came from Password_Value, NOT
    # the FirstName (the bug R-F1714 fixed), email from Email.
    assert captured["login_data"]["Email"] == "aria@arkmurus.com"
    assert captured["login_data"]["Password"] == "Gx7-generated-pw-91237"
    # Hit the real login + key pages, with the 32-hex regex.
    assert captured["login_url"] == "https://newsapi.org/login"
    assert captured["api_key_url"] == "https://newsapi.org/account"
    assert captured["key_regex"] == r"\b[0-9a-f]{32}\b"


def test_newsapi_config_has_real_fields_and_no_email_verify():
    p = _newsapi()
    sel = {s for s, _t, _src in p.signup_fields}
    assert {"Email", "Password_Value", "FirstName"} <= sel  # real names, not guesses
    assert p.verify_email_domain == ""  # key issued without email verification
    assert p.api_key_path == "/account" and p.api_key_regex
    assert ("Password", "password", "password") in p.login_fields
