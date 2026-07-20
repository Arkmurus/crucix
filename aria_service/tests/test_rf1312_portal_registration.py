"""
R-F1312 capability tests — portal registration pipeline + pending sources.

Tests:
  1. get_pending_source_requirements returns all unregistered portals
  2. auto_register_all returns a valid summary dict
  3. /portal-registry/pending-sources endpoint returns pending list
  4. /portal-registry/auto-register endpoint returns summary
  5. is_registered returns False for unknown portals
"""
from __future__ import annotations

import pytest


# ── Test 1: Pending source requirements ──────────────────────────────────────

def test_pending_source_requirements_returns_all_portals():
    """get_pending_source_requirements must return every portal that is
    not 'none' registration type, with env var requirements."""
    from aria_service.intel.portal_registry import get_pending_source_requirements

    reqs = get_pending_source_requirements()
    assert isinstance(reqs, list)
    assert len(reqs) > 0

    # Every entry must have the required fields
    for r in reqs:
        assert "id" in r, f"Missing id in {r}"
        assert "name" in r, f"Missing name in {r}"
        assert "registration_type" in r, f"Missing registration_type in {r}"
        assert "needs_env_vars" in r, f"Missing needs_env_vars in {r}"
        assert "env_vars_set" in r, f"Missing env_vars_set in {r}"
        assert "env_vars_missing" in r, f"Missing env_vars_missing in {r}"

    # ACLED should list ACLED_EMAIL and ACLED_PASSWORD
    acled = next((r for r in reqs if r["id"] == "acled"), None)
    assert acled is not None, "ACLED must be in pending sources"
    assert "ACLED_EMAIL" in acled["needs_env_vars"]
    assert "ACLED_PASSWORD" in acled["needs_env_vars"]

    # API-key portals should list their key env var
    api_key_portals = [r for r in reqs if r["registration_type"] == "api_key"]
    for p in api_key_portals:
        assert any("_API_KEY" in v for v in p["needs_env_vars"]), (
            f"API-key portal {p['id']} should need an _API_KEY env var"
        )


# ── Test 2: auto_register_all returns valid summary ──────────────────────────

@pytest.mark.asyncio
async def test_auto_register_all_returns_summary(monkeypatch):
    """auto_register_all must return a dict with expected keys even when
    no portals can be registered (no env vars set).

    R-F2812: this test used to call auto_register_all() with NOTHING mocked.
    `_ENABLED` defaults to "1", so the sweep ran for real: it walked every
    portal and drove `register_for_portal`, which launches a Playwright
    browser (portal_registry.py:1366+) to sign ARIA up for live government
    procurement portals — from the test suite. It blocked on the browser
    until pytest-timeout's thread method `os._exit()`d the whole run at
    ~31-34%, which is why the suite could never produce a §16 baseline.

    The two collaborators are stubbed so the sweep runs fully in-process.
    That keeps this test's actual subject — the SHAPE of the summary dict and
    the fact that per-portal outcomes are aggregated rather than raised — while
    removing the browser. Nothing is weakened: the assertions below are the
    original ones, and they now exercise the loop instead of timing out inside
    the first portal.
    """
    from aria_service.intel import portal_registry as pr
    from aria_service.intel.portal_registry import auto_register_all

    async def _fake_is_registered(portal_id: str) -> bool:
        return False

    async def _fake_register(portal_id: str, purpose: str = "") -> dict:
        # Mirror the real "needs a human for the captcha" outcome so the
        # captcha_deferred branch is the one under test.
        return {"success": False, "requires_operator": True,
                "message": "captcha requires operator"}

    monkeypatch.setattr(pr, "is_registered", _fake_is_registered)
    monkeypatch.setattr(pr, "register_for_portal", _fake_register)

    result = await auto_register_all()
    assert isinstance(result, dict)
    assert "total" in result
    assert "already_registered" in result
    assert "newly_registered" in result
    assert "captcha_deferred" in result
    assert "failed" in result
    assert "skipped_open" in result
    assert "details" in result
    assert isinstance(result["details"], list)
    assert result["total"] > 0


# ── Test 3: is_registered returns False for unknown portals ───────────────────

@pytest.mark.asyncio
async def test_is_registered_returns_false_for_unknown():
    """is_registered must return False for a portal that has never been
    registered (no credentials stored)."""
    from aria_service.intel.portal_registry import is_registered

    # Use a portal that definitely exists but has no credentials
    result = await is_registered("acled")
    assert result is False


# ── Test 4: get_registered_portals returns list ──────────────────────────────

@pytest.mark.asyncio
async def test_get_registered_portals_returns_list():
    """get_registered_portals must return a list of all portals with
    registered status."""
    from aria_service.intel.portal_registry import get_registered_portals

    portals = await get_registered_portals()
    assert isinstance(portals, list)
    assert len(portals) > 0
    for p in portals:
        assert "id" in p
        assert "name" in p
        assert "registered" in p
        assert "registration_type" in p
        assert "requires_captcha" in p


# ── Test 5: Module-level wire_success is present ─────────────────────────────

def test_module_has_wire_success():
    """The portal_registry module must have a wire_success call at module
    level so the brain knows it's active."""
    with open("aria_service/intel/portal_registry.py", "r", encoding="utf-8") as f:
        source = f.read()
    assert "wire_success" in source, (
        "portal_registry.py must call wire_success at module level"
    )
    assert "wire_failure" in source, (
        "portal_registry.py must call wire_failure in auto_register_all"
    )
