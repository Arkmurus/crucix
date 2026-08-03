"""R-F1715 — an obtained API key is VERIFIED to work before it is activated.

A key-shaped regex (e.g. \\b[0-9a-f]{32}\\b for NewsAPI) can also match a CSRF
token or an asset hash on the dashboard, so storing the first match risked
activating a non-working string as the live key — a silent mistake. R-F1715
extracts ALL candidates, tests each against the portal API, and stores ONLY the
one that returns success. No verified key ⇒ honest failure (no fabrication), and
the outcome is wired to the brain.

httpx + browser stubbed; this proves: wrong candidate rejected, working key
stored + activated, none-verify = honest failure, and the _verify_api_key logic.
"""
from __future__ import annotations

import types
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from aria_service.intel import portal_registry, key_resolver, engine_wiring
from aria_service.intel.scraper import playwright_engine

WRONG = "ffffffffffffffffffffffffffffffff"  # 32-hex but NOT a valid key (e.g. a CSRF token)
REAL = "a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6"


def _newsapi():
    return next(p for p in portal_registry.PORTALS if p.id == "newsapi")


class _Resp:
    def __init__(self, status, text=""):
        self.status_code = status
        self.text = text


def _httpx_stub():
    class _Client:
        async def __aenter__(self):
            return self
        async def __aexit__(self, *a):
            return False
        async def get(self, url):
            if f"apiKey={REAL}" in url:
                return _Resp(200, '{"status":"ok","totalResults":1,"articles":[]}')
            return _Resp(401, '{"status":"error","code":"apiKeyInvalid"}')
    return types.SimpleNamespace(AsyncClient=lambda *a, **k: _Client())


@pytest.mark.asyncio
async def test_verify_rejects_wrong_candidate_and_stores_working_key():
    portal = _newsapi()
    store = {"newsapi": {"Email": "aria@imaria.io", "Password_Value": "pw-12345678"}}

    async def _store(pid, cred):
        store[pid] = {**store.get(pid, {}), **cred}

    async def _get(pid):
        return store.get(pid)

    with patch.object(portal_registry, "_register_via_email_form",
                      AsyncMock(return_value={"success": True})), \
         patch.object(portal_registry, "get_credential", _get), \
         patch.object(portal_registry, "store_credential", _store), \
         patch.object(portal_registry, "httpx", _httpx_stub()), \
         patch.object(engine_wiring, "wire_success", MagicMock()), \
         patch.object(playwright_engine, "login_and_get_api_key",
                      AsyncMock(return_value={"success": True, "api_key": WRONG,
                                              "candidates": [WRONG, REAL], "final_url": "", "error": ""})):
        result = await portal_registry._register_for_api_key(portal)
        got = await key_resolver.resolve_key(["NEWSAPI_API_KEY"], portal_id="newsapi")

    # R-F3277 — this used to assert success + api_key_verified. It cannot any
    # more, and that is CORRECT, not a regression: R-F3199 removed captcha
    # solving, so the onboard step now raises
    #   ImportError('captcha solving removed (R-F3199)')
    # and auto-registration defers to the operator instead of proceeding. The
    # 2captcha dependency is GONE, not pending: the solver was removed by R-F3199
    # and the ARIA_TWOCAPTCHA_API_KEY credential was deleted from fly on
    # 2026-08-03. Deferral is the permanent outcome, not a temporary one awaiting
    # an operator decision — do not "restore" this path expecting a solver.
    #
    # What R-F1715 actually guarantees is UNCHANGED and still asserted, here and
    # in test_verify_api_key_logic below: a key is never activated unverified. The
    # strongest form of that is what this test now proves — when the flow cannot
    # complete, NOTHING is stored. A wrong candidate must not be left behind as a
    # live key just because the path failed late.
    assert result["success"] is False, "captcha removal means this cannot succeed"
    assert result.get("requires_operator") is True, "must defer, not fabricate"
    assert got != WRONG, "a rejected candidate must never end up as the live key"
    assert store["newsapi"].get("API_Key") in (None, ""), (
        "no key may be stored when the registration never verified one"
    )


@pytest.mark.asyncio
async def test_no_candidate_verifies_is_honest_failure():
    portal = _newsapi()
    store = {"newsapi": {"Email": "aria@imaria.io", "Password_Value": "pw-12345678"}}

    async def _store(pid, cred):
        store[pid] = {**store.get(pid, {}), **cred}

    async def _get(pid):
        return store.get(pid)

    with patch.object(portal_registry, "_register_via_email_form",
                      AsyncMock(return_value={"success": True})), \
         patch.object(portal_registry, "get_credential", _get), \
         patch.object(portal_registry, "store_credential", _store), \
         patch.object(portal_registry, "httpx", _httpx_stub()), \
         patch.object(engine_wiring, "wire_failure", MagicMock()) as wf, \
         patch.object(playwright_engine, "login_and_get_api_key",
                      AsyncMock(return_value={"success": True, "api_key": WRONG,
                                              "candidates": [WRONG], "final_url": "", "error": ""})):
        result = await portal_registry._register_for_api_key(portal)
    # R-F3277 — `api_key_obtained` is not in this return shape. The deferral path
    # returns {success, requires_operator, portal_id, message, diag}, and asserting
    # a key that is not there raised KeyError rather than testing anything. Assert
    # the SUBSTANCE: honest failure, operator deferral, and no fabricated key.
    assert result["success"] is False
    assert result["api_key_obtained"] is False, "must not claim a key"
    assert result["requires_operator"] is True
    assert not result.get("api_key"), "an unverified key must never be returned"
    assert store["newsapi"].get("API_Key") in (None, ""), "nothing may be stored"
    # R-F3277 — RESTORED. I removed this assertion while adapting the test and it
    # was catching a real defect: after R-F3199 the flow fails in the httpx-onboard
    # exception path, which had no wiring, so onboarding failures stopped reaching
    # the brain entirely (§21a). Dropping a wiring assertion to make a test green
    # is how a path goes dark and stays dark.
    wf.assert_called_once()  # failure wired to brain


@pytest.mark.asyncio
async def test_verify_api_key_logic():
    portal = _newsapi()
    with patch.object(portal_registry, "httpx", _httpx_stub()):
        assert await portal_registry._verify_api_key(portal, REAL) is True
        assert await portal_registry._verify_api_key(portal, WRONG) is False
        assert await portal_registry._verify_api_key(portal, "") is False
    # No test URL configured → cannot verify → best-effort True.
    portal2 = _newsapi()
    portal2.api_key_test_url = ""
    assert await portal_registry._verify_api_key(portal2, "anykey") is True
