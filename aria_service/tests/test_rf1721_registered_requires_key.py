"""R-F1721 — for api_key portals, 'registered' REQUIRES a stored key.

Live verification found NewsAPI status=registered but resolve_key→empty: a prior
attempt stored credentials (email/password), is_registered() returned True (creds
exist != key exists), register_for_portal short-circuited 'already registered',
and determine_and_drive marked it registered — a fabricated status with no usable
key (same class as R-F1702). Fixes:
  - register_for_portal: api_key account exists but no key → LOG IN + RETRIEVE
    the key (not fake 'already registered').
  - determine_and_drive: a registered-but-keyless api_key entry is RE-DRIVEN,
    not short-circuited as done.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from aria_service.intel import portal_registry as pr


def _seed(no_key=True):
    cred = {"Email": "aria@imaria.io", "Password_Value": "pw-12345678"}
    if not no_key:
        cred["api_key"] = "existing32hexkeyaaaaaaaaaaaaaaaa"
    store = {"newsapi": cred}

    async def _get(pid):
        return store.get(pid)

    async def _store(pid, c):
        store[pid] = {**store.get(pid, {}), **c}

    return store, _get, _store


@pytest.mark.asyncio
async def test_register_existing_account_no_key_retrieves_instead_of_faking():
    store, _get, _store = _seed(no_key=True)
    with patch.object(pr, "is_registered", AsyncMock(return_value=True)), \
         patch.object(pr, "get_credential", _get), \
         patch.object(pr, "store_credential", _store), \
         patch.object(pr, "_retrieve_api_key", AsyncMock(return_value=["realkey32hexbbbbbbbbbbbbbbbbbbbb"])), \
         patch.object(pr, "_verify_api_key", AsyncMock(return_value=True)):
        res = await pr.register_for_portal("newsapi")
    assert res["success"] is True
    assert res["api_key_obtained"] is True
    assert store["newsapi"].get("api_key") == "realkey32hexbbbbbbbbbbbbbbbbbbbb"


@pytest.mark.asyncio
async def test_register_existing_account_no_key_unverified_is_honest_failure():
    _, _get, _store = _seed(no_key=True)
    with patch.object(pr, "is_registered", AsyncMock(return_value=True)), \
         patch.object(pr, "get_credential", _get), \
         patch.object(pr, "store_credential", _store), \
         patch.object(pr, "_retrieve_api_key", AsyncMock(return_value=["junk"])), \
         patch.object(pr, "_verify_api_key", AsyncMock(return_value=False)):
        res = await pr.register_for_portal("newsapi")
    assert res["success"] is False
    assert res["requires_operator"] is True


@pytest.mark.asyncio
async def test_register_existing_account_with_key_is_genuinely_registered():
    _, _get, _store = _seed(no_key=False)
    with patch.object(pr, "is_registered", AsyncMock(return_value=True)), \
         patch.object(pr, "get_credential", _get):
        res = await pr.register_for_portal("newsapi")
    assert res["success"] is True
    assert res["api_key_obtained"] is True
    assert "key present" in res["message"]


class _Vault:
    def __init__(self, status):
        self._status = status

    def get(self, pid):
        return {"status": self._status}

    def update_status(self, *a, **k):
        pass


@pytest.mark.asyncio
async def test_determine_and_drive_redrives_registered_but_keyless():
    """A vault entry marked 'registered' with NO key must be re-driven (the ~19
    keyless fakes), not reported done."""
    _, _get, _store = _seed(no_key=True)  # registered-ish but no api_key
    with patch("aria_service.intel.agent_signup_vault.get_vault", lambda: _Vault("registered")), \
         patch.object(pr, "get_credential", _get), \
         patch("aria_service.intel.captcha_solver.get_solver",
               lambda: type("S", (), {"is_ready": True})()), \
         patch.object(pr, "register_for_portal",
                      AsyncMock(return_value={"success": True, "message": "registered + key", "api_key_obtained": True})):
        res = await pr.determine_and_drive("newsapi")
    # It re-drove (reached register_for_portal) — NOT the keyless short-circuit.
    assert res["status"] == "registered"
    assert res["message"] != "Confirmed registered in vault"


@pytest.mark.asyncio
async def test_determine_and_drive_keeps_registered_when_key_present():
    _, _get, _store = _seed(no_key=False)  # has api_key
    with patch("aria_service.intel.agent_signup_vault.get_vault", lambda: _Vault("registered")), \
         patch.object(pr, "get_credential", _get):
        res = await pr.determine_and_drive("newsapi")
    assert res["status"] == "registered"
    assert res["message"] == "Confirmed registered in vault"
