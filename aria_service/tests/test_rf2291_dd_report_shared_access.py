"""R-F2291 — a same-company colleague can VIEW and DELETE a company-shared DD
report by id (was 404: "no content on click" + "delete fails", 2026-07-02).

Root cause: the LIST view shares reports same-email-domain (R-F607/R-F608), but
the by-id GET/DELETE used an owner-EXACT check AND read the owner domain from the
report BODY — which is often None (only stamped when a full user_email is threaded
at creation). The authoritative owner domain lives on the INDEX entry (what
list_reports reads). Fix: dd_report_ep/dd_report_delete_ep overlay the INDEX
ownership (via _dd_report_acl_context → dd_orchestrator.get_report_owner) before
the ACL check.

These tests reflect the REAL split: body domain None, index domain set. The
earlier version of this test mocked a body that already carried the domain — so
it was green while live was broken (the §23 "wrong test passes while live fails"
trap). They drive the REAL endpoints + the access helper.
"""
from __future__ import annotations

import pytest
from fastapi import HTTPException

from aria_service.routes import aria as routes
from aria_service.intel import dd_orchestrator

# Report BODY as actually persisted: user_id + share set, but domain is None.
_BODY = {
    "run_id": "dd_x",
    "user_id": "owner_a",
    "user_email_domain": None,          # <-- the real-world gap (e.g. dd_1192328d70f8)
    "share_to_company": True,
    "rendered": "# DD Report\nRisk classification: AMBER-LIGHT\n",
}
# INDEX entry (authoritative — what list_reports + the shared badge read).
_INDEX = {"user_id": "owner_a", "user_email_domain": "arkmurus.com", "share_to_company": True}


def _wire(monkeypatch, body=None, index=_INDEX):
    async def _get(rid):
        return dict(body if body is not None else _BODY)
    async def _owner(rid):
        return dict(index) if index is not None else None
    monkeypatch.setattr(dd_orchestrator, "get_report", _get)
    monkeypatch.setattr(dd_orchestrator, "get_report_owner", _owner)


def test_access_helper_matrix():
    f = routes._dd_report_access_allowed
    R = {"user_id": "owner_a", "user_email_domain": "arkmurus.com", "share_to_company": True}
    assert f(R, "owner_a", "arkmurus.com") is True            # owner
    assert f(R, "colleague_b", "arkmurus.com") is True        # same-company, shared
    assert f({**R, "share_to_company": False}, "colleague_b", "arkmurus.com") is False  # opted out
    assert f(R, "colleague_b", "evil.com") is False           # different company
    assert f(R, "colleague_b", "") is False                   # no domain → blocked

    # R-F3800 — the unscoped admin path must be DECLARED, not inherited.
    #
    # This line read `assert f(R, "", "") is True` with no context set. It passed
    # only because `_auth_is_internal_var` used to default True, and R-F3628
    # deliberately flipped that default to False (fail-closed) precisely because
    # "True GRANTS to any context where the setter never ran". R-F3628's own comment
    # names the tests that would go red and says to fix them by setting the var
    # explicitly; rf1820 and rf2097 were done, this one was missed.
    #
    # Restoring the permissive default to make this green would reopen the
    # cross-tenant hole R-F2778/R-F3709 closed — an external API-token caller with no
    # user_id would regain read/delete on any report by id. So the TEST declares the
    # internal tier it is asserting about, and the token-less case is pinned below.
    routes._auth_is_internal_var.set(True)
    assert f(R, "", "") is True                               # internal / unscoped

    routes._auth_is_internal_var.set(False)
    assert f(R, "", "") is False, (
        "an unscoped caller that is NOT the internal service token must be scoped to "
        "nothing (R-F2778) — this is the half that must never be relaxed"
    )
    routes._auth_is_internal_var.set(True)
    # R-F2402 — owner-less report now FAILS CLOSED for an arbitrary scoped user
    # (was fail-open `return True` = a GDPR cross-tenant leak). Admin (user_id='')
    # still bypasses above; only the configured legacy operator may read owner-less
    # (covered in test_rf2402_ownerless_report_failclosed).
    import os as _os
    _os.environ.pop("ARIA_DD_LEGACY_OWNER_UID", None)
    _os.environ.pop("ARIA_CODER_OPERATOR_USER_ID", None)
    assert f({"run_id": "x"}, "anyone", "any.com") is False   # owner-less → fail closed
    assert f({**R, "user_email_domain": "ArkMurus.COM"}, "colleague_b", "arkmurus.com") is True  # case-insensitive


@pytest.mark.asyncio
async def test_view_same_company_uses_index_domain(monkeypatch):
    """The report BODY domain is None; the INDEX domain (arkmurus.com) must be used
    so a same-company colleague gets the content, not a 404."""
    _wire(monkeypatch)
    out = await routes.dd_report_ep(
        "dd_x", format="markdown", user_id="colleague_b", user_email_domain="arkmurus.com")
    assert "markdown" in out and "AMBER" in out["markdown"], out


@pytest.mark.asyncio
async def test_view_cross_company_is_404(monkeypatch):
    _wire(monkeypatch)
    with pytest.raises(HTTPException) as ei:
        await routes.dd_report_ep(
            "dd_x", format="markdown", user_id="c", user_email_domain="evil.com")
    assert ei.value.status_code == 404


@pytest.mark.asyncio
async def test_view_share_opt_out_is_404(monkeypatch):
    # Opt-out set on the INDEX row (authoritative) → even same-company is blocked.
    _wire(monkeypatch, index={**_INDEX, "share_to_company": False})
    with pytest.raises(HTTPException) as ei:
        await routes.dd_report_ep(
            "dd_x", format="markdown", user_id="colleague_b", user_email_domain="arkmurus.com")
    assert ei.value.status_code == 404


@pytest.mark.asyncio
async def test_delete_same_company_deletes(monkeypatch):
    deleted = {}
    _wire(monkeypatch)
    async def _del(rid):
        deleted["id"] = rid
        return {"deleted": rid}
    monkeypatch.setattr(dd_orchestrator, "delete_report", _del)
    await routes.dd_report_delete_ep(
        "dd_x", user_id="colleague_b", user_email_domain="arkmurus.com")
    assert deleted.get("id") == "dd_x"


@pytest.mark.asyncio
async def test_delete_cross_company_blocked(monkeypatch):
    _wire(monkeypatch)
    async def _del(rid):
        raise AssertionError("cross-company delete must never reach delete_report")
    monkeypatch.setattr(dd_orchestrator, "delete_report", _del)
    with pytest.raises(HTTPException) as ei:
        await routes.dd_report_delete_ep(
            "dd_x", user_id="c", user_email_domain="evil.com")
    assert ei.value.status_code == 404


@pytest.mark.asyncio
async def test_view_falls_back_to_body_when_not_in_index(monkeypatch):
    """If the run_id aged out of the index (get_report_owner → None), fall back to
    the body. Body has domain set here → owner still works, colleague still blocked
    (body domain None case already covered by the index path above)."""
    body = {**_BODY, "user_email_domain": "arkmurus.com"}
    _wire(monkeypatch, body=body, index=None)
    out = await routes.dd_report_ep(
        "dd_x", format="markdown", user_id="colleague_b", user_email_domain="arkmurus.com")
    assert "markdown" in out
