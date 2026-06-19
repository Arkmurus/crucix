"""R-F1719 — determine_and_drive attempts CAPTCHA portals when a solver is ready.

The live newsapi run returned needs_operator/blocker=captcha: determine_and_drive
short-circuited ALL requires_captcha portals to the operator BEFORE reaching the
register path — predating the 2captcha solver. With a ready solver it must
proceed (register_for_portal solves the CAPTCHA via 2captcha). Only bail when no
solver is configured.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from aria_service.intel import portal_registry


class _Solver:
    def __init__(self, ready):
        self.is_ready = ready


class _Vault:
    def get(self, pid):
        return {"status": "needs_operator"}  # not yet registered

    def update_status(self, *a, **k):
        pass


@pytest.mark.asyncio
async def test_captcha_portal_proceeds_when_solver_ready():
    outcome = {"success": True, "message": "registered + key", "api_key_obtained": True}
    with patch("aria_service.intel.captcha_solver.get_solver", lambda: _Solver(True)), \
         patch("aria_service.intel.agent_signup_vault.get_vault", lambda: _Vault()), \
         patch.object(portal_registry, "register_for_portal", AsyncMock(return_value=outcome)):
        res = await portal_registry.determine_and_drive("newsapi")
    # The fix: it reached register_for_portal (status=registered) instead of
    # bailing on captcha — determine_and_drive only returns registered after a
    # successful register_for_portal call.
    assert res["status"] == "registered", res


@pytest.mark.asyncio
async def test_captcha_portal_bails_only_when_no_solver():
    with patch("aria_service.intel.captcha_solver.get_solver", lambda: _Solver(False)), \
         patch("aria_service.intel.agent_signup_vault.get_vault", lambda: _Vault()), \
         patch.object(portal_registry, "register_for_portal",
                      AsyncMock(side_effect=AssertionError("must NOT register without a solver"))):
        res = await portal_registry.determine_and_drive("newsapi")
    assert res["status"] == "needs_operator"
    assert res["blocker"] == "captcha"
