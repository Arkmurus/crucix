"""R-F1716 — the autonomous driver re-attempts needs_operator portals.

determine_and_drive_all (run every 12h by the scheduler) drove ONLY 'pending'
portals. The R-F1704 migration parked ~30 portals (incl. newsapi, now fully
auto-onboardable) in 'needs_operator', so the loop NEVER re-attempted them —
ARIA silently stopped registering. needs_operator is RETRYABLE (its blocker may
be resolved now); only declined/deferred are terminal. This proves both
pending AND needs_operator portals are driven, and the registration path is
actually invoked for the needs_operator one (newsapi).
"""
from __future__ import annotations

from unittest.mock import patch

import pytest

from aria_service.intel import portal_registry


class _VaultStub:
    def __init__(self):
        self.updates = []

    def list(self, status=None, limit=100):
        return {
            "pending": [{"site_id": "pending_portal"}],
            "needs_operator": [{"site_id": "newsapi"}],
            # declined/deferred would be returned ONLY if queried — the driver
            # must NOT query them, so they never get driven.
            "declined": [{"site_id": "crunchbase"}],
            "deferred": [{"site_id": "acled"}],
        }.get(status, [])

    def update_status(self, pid, status, notes=""):
        self.updates.append((pid, status))


@pytest.mark.asyncio
async def test_driver_attempts_needs_operator_and_pending_not_declined():
    driven = []

    # R-F3298 added the keyword-only `drive` flag. The stub mirrors the real
    # signature deliberately: a stub that swallowed **kwargs would keep passing
    # while the production call drifted away from it.
    async def _fake_drive(pid, *, drive=True):
        driven.append(pid)
        return {"status": "needs_operator", "blocker": "test"}

    vault = _VaultStub()
    with patch("aria_service.intel.agent_signup_vault.get_vault", lambda: vault), \
         patch.object(portal_registry, "determine_and_drive", _fake_drive):
        await portal_registry.determine_and_drive_all()

    # The fix: newsapi (needs_operator) is now driven — was skipped before.
    assert "newsapi" in driven, "needs_operator portals must be re-attempted"
    assert "pending_portal" in driven, "pending portals still driven"
    # Terminal operator decisions are NOT auto-re-attempted.
    assert "crunchbase" not in driven  # declined
    assert "acled" not in driven       # deferred
    assert set(driven) == {"pending_portal", "newsapi"}
