"""R-F1489 capability test — the DD reports index never fabricates a seed report.

R-F1484 auto-seeded a fake "Acme Defence GmbH" DD report (citing real tools as if
they ran) and PERSISTED it into the real reports store whenever the index was empty.
On a compliance product that is a honesty risk, and ARIA absorbing a fabricated report
contaminates her learning. R-F1489 removed it — an empty store returns an empty list
and the UI shows an honest empty-state.
"""
from unittest.mock import AsyncMock, patch

import pytest

from aria_service.routes import aria as aria_routes


@pytest.mark.asyncio
async def test_empty_index_returns_empty_and_persists_nothing():
    # R-F3801 — DECLARE the internal tier. R-F3628 flipped
    # `_AUTH_INTERNAL_DEFAULT` to False (fail-closed), so an unscoped call
    # (user_id="") from a context that never set the var is now DENIED. The
    # denial is a deliberate 404 ("report not found") so existence is not
    # leaked, which is correct — and is why this read as a missing fixture
    # rather than an auth decision.
    aria_routes._auth_is_internal_var.set(True)
    with patch("aria_service.intel.dd_orchestrator.list_reports",
               new=AsyncMock(return_value=[])) as lr, \
         patch("aria_service.intel.dd_orchestrator._persist_report",
               new=AsyncMock()) as pr:
        result = await aria_routes.dd_reports_index_ep(limit=50, user_id="", user_email_domain="")
        assert result == {"reports": []}, f"empty store must return empty, got {result}"
        assert pr.call_count == 0, "must NOT fabricate/persist a seed report (R-F1489)"
        assert lr.call_count == 1, "should list real reports exactly once (no re-list after a seed)"


@pytest.mark.asyncio
async def test_real_reports_passed_through_unchanged():
    # R-F3801 — DECLARE the internal tier. R-F3628 flipped
    # `_AUTH_INTERNAL_DEFAULT` to False (fail-closed), so an unscoped call
    # (user_id="") from a context that never set the var is now DENIED. The
    # denial is a deliberate 404 ("report not found") so existence is not
    # leaked, which is correct — and is why this read as a missing fixture
    # rather than an auth decision.
    aria_routes._auth_is_internal_var.set(True)
    real = [{"run_id": "dd_real_1", "entity_name": "Real Entity Ltd", "risk_classification": "GREEN"}]
    with patch("aria_service.intel.dd_orchestrator.list_reports",
               new=AsyncMock(return_value=real)), \
         patch("aria_service.intel.dd_orchestrator._persist_report",
               new=AsyncMock()) as pr:
        result = await aria_routes.dd_reports_index_ep(limit=50, user_id="", user_email_domain="")
        assert result == {"reports": real}
        assert pr.call_count == 0, "must never persist anything on a read path"
