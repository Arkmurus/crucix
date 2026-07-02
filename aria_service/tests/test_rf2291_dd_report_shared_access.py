"""R-F2291 — a same-company colleague can VIEW and DELETE a company-shared DD
report by id (was 404: "no content on click" + "delete fails", 2026-07-02).

Root cause: the LIST view shares reports same-email-domain (R-F607/R-F608), but
the by-id GET/DELETE used an owner-EXACT ownership check, so a same-company
colleague opening a SHARED report got 404. These tests drive the REAL endpoints
(dd_report_ep / dd_report_delete_ep) and the shared access helper.
"""
from __future__ import annotations

import pytest
from fastapi import HTTPException

from aria_service.routes import aria as routes
from aria_service.intel import dd_orchestrator

_REPORT = {
    "run_id": "dd_x",
    "user_id": "owner_a",
    "user_email_domain": "arkmurus.com",
    "share_to_company": True,
    "rendered": "# DD Report\nRisk classification: AMBER-LIGHT\n",
}


def test_access_helper_matrix():
    f = routes._dd_report_access_allowed
    assert f(_REPORT, "owner_a", "arkmurus.com") is True            # owner
    assert f(_REPORT, "colleague_b", "arkmurus.com") is True        # same-company, shared
    assert f({**_REPORT, "share_to_company": False}, "colleague_b", "arkmurus.com") is False  # opted out
    assert f(_REPORT, "colleague_b", "evil.com") is False           # different company
    assert f(_REPORT, "colleague_b", "") is False                   # no domain → blocked
    assert f(_REPORT, "", "") is True                               # admin / unscoped
    assert f({"run_id": "x"}, "anyone", "any.com") is True          # legacy report, no owner
    # case-insensitive domain
    assert f({**_REPORT, "user_email_domain": "ArkMurus.COM"}, "colleague_b", "arkmurus.com") is True


@pytest.mark.asyncio
async def test_view_same_company_returns_content(monkeypatch):
    async def _get(rid):
        return dict(_REPORT)
    monkeypatch.setattr(dd_orchestrator, "get_report", _get)
    out = await routes.dd_report_ep(
        "dd_x", format="markdown", user_id="colleague_b", user_email_domain="arkmurus.com")
    assert "markdown" in out and "AMBER" in out["markdown"], out


@pytest.mark.asyncio
async def test_view_cross_company_is_404(monkeypatch):
    async def _get(rid):
        return dict(_REPORT)
    monkeypatch.setattr(dd_orchestrator, "get_report", _get)
    with pytest.raises(HTTPException) as ei:
        await routes.dd_report_ep(
            "dd_x", format="markdown", user_id="c", user_email_domain="evil.com")
    assert ei.value.status_code == 404


@pytest.mark.asyncio
async def test_delete_same_company_deletes(monkeypatch):
    deleted = {}
    async def _get(rid):
        return dict(_REPORT)
    async def _del(rid):
        deleted["id"] = rid
        return {"deleted": rid}
    monkeypatch.setattr(dd_orchestrator, "get_report", _get)
    monkeypatch.setattr(dd_orchestrator, "delete_report", _del)
    await routes.dd_report_delete_ep(
        "dd_x", user_id="colleague_b", user_email_domain="arkmurus.com")
    assert deleted.get("id") == "dd_x"


@pytest.mark.asyncio
async def test_delete_cross_company_blocked(monkeypatch):
    async def _get(rid):
        return dict(_REPORT)
    async def _del(rid):
        raise AssertionError("cross-company delete must never reach delete_report")
    monkeypatch.setattr(dd_orchestrator, "get_report", _get)
    monkeypatch.setattr(dd_orchestrator, "delete_report", _del)
    with pytest.raises(HTTPException) as ei:
        await routes.dd_report_delete_ep(
            "dd_x", user_id="c", user_email_domain="evil.com")
    assert ei.value.status_code == 404
