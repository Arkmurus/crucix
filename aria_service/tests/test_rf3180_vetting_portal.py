"""R-F3180 — the unauthenticated portal, driven end to end.

Everything here is a security property. The portal is reachable by anyone
holding a link, so the tests are written as "what must a link holder be unable
to do", and they drive the REAL routers rather than the helpers underneath.
"""

from __future__ import annotations

import base64
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

TOKEN = "portal-test-token"
AUTH = {"Authorization": f"Bearer {TOKEN}"}
TENANT = "tenant-a"


@pytest.fixture()
def client(tmp_path, monkeypatch):
    from aria_service.routes.vetting import router as vetting_router
    from aria_service.routes.vetting_portal import router as portal_router
    from aria_service.vetting import store as store_module
    import aria_service.intel.dd_evidence_store as ev

    monkeypatch.setenv("ARIA_API_TOKEN", TOKEN)
    monkeypatch.setenv("ARIA_VETTING_DB", str(tmp_path / "cases.db"))
    monkeypatch.setenv("ARIA_DD_EVIDENCE_DB", str(tmp_path / "ev.db"))
    monkeypatch.setenv("ARIA_DD_EVIDENCE_ARTIFACT_DIR", str(tmp_path / "art"))
    monkeypatch.setattr(store_module, "_STORE", None, raising=False)
    monkeypatch.setattr(ev, "_STORE", None, raising=False)
    app = FastAPI()
    app.include_router(vetting_router)
    app.include_router(portal_router)
    return TestClient(app)


def _case(client, case_id="P1"):
    return client.post("/api/aria/vetting/cases", json={
        "case_id": case_id, "applicant_name": "Ada Lovelace",
        "date_of_birth": "1990-01-01", "employment_start": "2026-06-01",
        "pack_id": "uk_bs7858",
        "career": [{"entry_id": "e1", "entry_type": "EMPLOYMENT",
                    "start": "2021-06-01", "end": "2023-01-01",
                    "organisation": "Alpha Ltd"}],
    }, params={"user_id": TENANT}, headers=AUTH)


def _invite(client, case_id="P1", **body):
    payload = {"kind": "APPLICANT"}
    payload.update(body)
    return client.post(f"/api/aria/vetting/case/{case_id}/invites", json=payload,
                       params={"user_id": TENANT}, headers=AUTH)


# ── the surface is genuinely unauthenticated ──────────────────────────────

def test_portal_needs_no_credentials(client):
    """The whole point: an applicant has no account."""
    assert _case(client).status_code == 200
    tok = _invite(client).json()["token"]
    r = client.get(f"/api/vetting-portal/{tok}")     # NO Authorization header
    assert r.status_code == 200, r.text
    assert r.json()["kind"] == "APPLICANT"


def test_the_authenticated_side_still_requires_auth(client):
    """The portal must not have loosened the case routes."""
    assert client.get("/api/aria/vetting/cases",
                      params={"user_id": TENANT}).status_code == 401


# ── what a link holder CANNOT do ──────────────────────────────────────────

def test_a_token_cannot_read_the_case_or_its_verdict(client):
    assert _case(client).status_code == 200
    tok = _invite(client).json()["token"]
    body = client.get(f"/api/vetting-portal/{tok}").json()
    blob = str(body).lower()
    for leak in ("finding", "blocker", "verdict", "conviction",
                 "last_status", "manifest", "pack_hash", "tenant"):
        assert leak not in blob, f"portal context leaked {leak!r}"


def test_a_referee_link_reveals_only_its_own_period(client):
    assert _case(client).status_code == 200
    tok = _invite(client, kind="REFEREE", entry_id="e1",
                  referee_name="Pat Manager").json()["token"]
    body = client.get(f"/api/vetting-portal/{tok}").json()
    ctx = body["context"]
    assert ctx["organisation"] == "Alpha Ltd"
    assert set(ctx) == {"applicant_name", "organisation", "period_from",
                        "period_to", "asked_to_confirm", "note"}
    assert "1990" not in str(ctx)          # no DOB


def test_a_referee_invite_without_a_real_entry_is_refused(client):
    assert _case(client).status_code == 200
    assert _invite(client, kind="REFEREE", entry_id="").status_code == 422
    assert _invite(client, kind="REFEREE",
                   entry_id="not-on-this-case").status_code == 422


# ── every failure looks identical ─────────────────────────────────────────

def test_unknown_expired_and_revoked_are_indistinguishable(client):
    """Distinguishing them turns the endpoint into an oracle: "revoked"
    confirms the link was once real, which confirms a named person is being
    screened by a named employer."""
    assert _case(client).status_code == 200
    created = _invite(client).json()
    good = created["token"]

    client.delete(f"/api/aria/vetting/case/P1/invites/{created['invite_id']}",
                  params={"user_id": TENANT}, headers=AUTH)

    revoked = client.get(f"/api/vetting-portal/{good}")
    unknown = client.get("/api/vetting-portal/vpa_completely-made-up-token")
    assert revoked.status_code == unknown.status_code == 404
    assert revoked.json() == unknown.json(), (
        "a revoked link must be indistinguishable from one that never existed")


def test_a_disposed_case_kills_its_links(client):
    assert _case(client, "P-GONE").status_code == 200
    tok = _invite(client, "P-GONE").json()["token"]
    assert client.get(f"/api/vetting-portal/{tok}").status_code == 200
    client.post("/api/aria/vetting/case/P-GONE/dispose",
                params={"user_id": TENANT}, headers=AUTH)
    assert client.get(f"/api/vetting-portal/{tok}").status_code == 404


# ── upload goes through the SAME intake path ──────────────────────────────

def test_upload_lands_on_the_case_and_is_marked_link_sourced(client):
    assert _case(client).status_code == 200
    tok = _invite(client).json()["token"]
    with patch("aria_service.vetting.documents.extract_document",
               new=AsyncMock(return_value={"available": True, "data": {
                   "doc_type": "P60", "confidence": 0.95}})):
        r = client.post(f"/api/vetting-portal/{tok}/documents", json={
            "filename": "p60.txt",
            "content_base64": base64.b64encode(b"payslip").decode()})
    assert r.status_code == 200, r.text
    assert r.json()["received"] is True

    case = client.get("/api/aria/vetting/case/P1",
                      params={"user_id": TENANT}, headers=AUTH).json()
    assert len(case["documents"]) == 1
    doc = case["documents"][0]
    assert doc["encrypted"] is True, "portal uploads must be encrypted like any other"
    assert any("uploaded_via:applicant_link" in f
               for f in doc["authenticity_flags"]), (
        "an officer must be able to see this arrived via a link, not from them")


def test_the_uploader_is_told_arrival_only_never_the_assessment(client):
    """Returning the classification or the review flag would tell someone
    probing the link how the extractor scores a forgery."""
    assert _case(client).status_code == 200
    tok = _invite(client).json()["token"]
    with patch("aria_service.vetting.documents.extract_document",
               new=AsyncMock(return_value={"available": False, "reason": "x"})):
        body = client.post(f"/api/vetting-portal/{tok}/documents", json={
            "filename": "x.txt",
            "content_base64": base64.b64encode(b"x").decode()}).json()
    assert set(body) == {"received", "filename", "message"}
    blob = str(body).lower()
    for leak in ("confidence", "needs_review", "doc_type", "extraction"):
        assert leak not in blob


def test_a_referee_upload_attaches_to_the_period_it_covers(client):
    assert _case(client).status_code == 200
    tok = _invite(client, kind="REFEREE", entry_id="e1").json()["token"]
    with patch("aria_service.vetting.documents.extract_document",
               new=AsyncMock(return_value={"available": True, "data": {
                   "doc_type": "EMPLOYER_REFERENCE", "confidence": 0.96}})):
        assert client.post(f"/api/vetting-portal/{tok}/documents", json={
            "filename": "ref.txt",
            "content_base64": base64.b64encode(b"ref").decode()
        }).status_code == 200
    case = client.get("/api/aria/vetting/case/P1",
                      params={"user_id": TENANT}, headers=AUTH).json()
    entry = next(e for e in case["career"] if e["entry_id"] == "e1")
    assert len(entry["supporting_documents"]) == 1


def test_upload_rejects_junk_without_leaking_internals(client):
    assert _case(client).status_code == 200
    tok = _invite(client).json()["token"]
    r = client.post(f"/api/vetting-portal/{tok}/documents",
                    json={"filename": "x.txt", "content_base64": "!!!not-b64!!!"})
    assert r.status_code == 422
    assert "traceback" not in r.text.lower()


# ── employer side ─────────────────────────────────────────────────────────

def test_the_token_is_returned_once_and_never_again(client):
    assert _case(client).status_code == 200
    created = _invite(client).json()
    assert created["token"].startswith("vpa_")
    listed = client.get("/api/aria/vetting/case/P1/invites",
                        params={"user_id": TENANT}, headers=AUTH).json()
    assert listed["count"] == 1
    assert "token" not in listed["invites"][0]
    assert created["token"] not in str(listed)


def test_invites_are_tenant_scoped(client):
    assert _case(client).status_code == 200
    _invite(client)
    other = client.get("/api/aria/vetting/case/P1/invites",
                       params={"user_id": "tenant-b"}, headers=AUTH).json()
    assert other["count"] == 0
