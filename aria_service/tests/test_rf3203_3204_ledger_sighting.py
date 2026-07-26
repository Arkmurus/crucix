"""R-F3203/R-F3204 — the request ledger persisted, and sighting over HTTP.

The property that matters most: minting an invite RECORDS the request. A ledger
the officer has to remember to update separately is what the paper progress
sheet already was.
"""

from __future__ import annotations

import base64
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

TOKEN = "ledger-test-token"
AUTH = {"Authorization": f"Bearer {TOKEN}"}
TENANT = "tenant-a"


@pytest.fixture()
def client(tmp_path, monkeypatch):
    from aria_service.routes.vetting import router as vetting_router
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
    return TestClient(app)


def _case(client, case_id="L1"):
    return client.post("/api/aria/vetting/cases", json={
        "case_id": case_id, "applicant_name": "Ada Lovelace",
        "date_of_birth": "1990-01-01", "employment_start": "2026-06-01",
        "pack_id": "uk_bs7858",
        "career": [{"entry_id": "e1", "entry_type": "EMPLOYMENT",
                    "start": "2021-06-01", "end": "2023-01-01",
                    "organisation": "Alpha Ltd"},
                   {"entry_id": "e2", "entry_type": "EDUCATION",
                    "start": "2019-01-01", "end": "2021-05-01",
                    "organisation": "A College"}],
    }, params={"user_id": TENANT}, headers=AUTH)


def _ledger(client, case_id="L1", **params):
    q = {"user_id": TENANT}
    q.update(params)
    return client.get(f"/api/aria/vetting/case/{case_id}/requests",
                      params=q, headers=AUTH).json()


# ── the ledger is FED by the invite flow ──────────────────────────────────

def test_minting_an_invite_records_a_request(client):
    """THE property. Otherwise the ledger is a second thing to remember."""
    assert _case(client).status_code == 200
    assert _ledger(client)["summary"]["open"] == 0

    client.post("/api/aria/vetting/case/L1/invites", json={
        "kind": "REFEREE", "entry_id": "e1", "referee_email": "hr@alpha.example",
    }, params={"user_id": TENANT}, headers=AUTH)

    body = _ledger(client)
    assert len(body["requests"]) == 1
    req = body["requests"][0]
    assert req["code"] == "WR", "an employment referee is a work reference"
    assert req["sent_to"] == "hr@alpha.example"
    assert req["channel"] == "link"
    assert req["invite_id"], "the request must link to the invite it was sent as"


def test_an_education_referee_is_recorded_as_ER_not_WR(client):
    """The code mapping is real, not an assumption that every referee is a
    work reference."""
    assert _case(client).status_code == 200
    client.post("/api/aria/vetting/case/L1/invites", json={
        "kind": "REFEREE", "entry_id": "e2", "referee_email": "reg@college.example",
    }, params={"user_id": TENANT}, headers=AUTH)
    assert _ledger(client)["requests"][0]["code"] == "ER"


def test_an_applicant_invite_is_a_documentation_request(client):
    assert _case(client).status_code == 200
    client.post("/api/aria/vetting/case/L1/invites", json={"kind": "APPLICANT"},
                params={"user_id": TENANT}, headers=AUTH)
    assert _ledger(client)["requests"][0]["code"] == "DR"


# ── requests sent outside the link flow ───────────────────────────────────

def test_a_posted_request_can_be_recorded_by_hand(client):
    """Not every request goes out as a link — post and phone still happen."""
    assert _case(client).status_code == 200
    r = client.post("/api/aria/vetting/case/L1/requests", json={
        "code": "CR", "sent_to": "Character referee", "channel": "post",
    }, params={"user_id": TENANT}, headers=AUTH)
    assert r.status_code == 200, r.text
    assert r.json()["code"] == "CR"


def test_an_unaddressed_request_is_refused(client):
    assert _case(client).status_code == 200
    r = client.post("/api/aria/vetting/case/L1/requests",
                    json={"code": "WR", "sent_to": "   "},
                    params={"user_id": TENANT}, headers=AUTH)
    assert r.status_code == 422


def test_a_chaser_must_name_what_it_chases(client):
    assert _case(client).status_code == 200
    r = client.post("/api/aria/vetting/case/L1/requests",
                    json={"code": "CL", "sent_to": "hr@alpha.example"},
                    params={"user_id": TENANT}, headers=AUTH)
    assert r.status_code == 422
    ok = client.post("/api/aria/vetting/case/L1/requests",
                     json={"code": "CL", "sent_to": "hr@alpha.example",
                           "chases": "vreq_original"},
                     params={"user_id": TENANT}, headers=AUTH)
    assert ok.status_code == 200


def test_an_unknown_code_is_refused_with_the_accepted_list(client):
    assert _case(client).status_code == 200
    r = client.post("/api/aria/vetting/case/L1/requests",
                    json={"code": "ZZ", "sent_to": "x"},
                    params={"user_id": TENANT}, headers=AUTH)
    assert r.status_code == 422
    assert "WR" in r.json()["detail"]["accepted"]


# ── overdue is derived, and closing works ─────────────────────────────────

def test_overdue_is_computed_from_as_of_not_stored(client):
    assert _case(client).status_code == 200
    client.post("/api/aria/vetting/case/L1/requests",
                json={"code": "WR", "sent_to": "hr@alpha.example"},
                params={"user_id": TENANT}, headers=AUTH)
    today = _ledger(client)
    assert today["summary"]["overdue"] == 0
    # Same stored row, later as_of → now overdue. Nothing was written.
    later = _ledger(client, as_of="2027-01-01")
    assert later["summary"]["overdue"] == 1
    assert later["requests"][0]["days_outstanding"] > 100


def test_closing_a_request_removes_it_from_open(client):
    assert _case(client).status_code == 200
    created = client.post("/api/aria/vetting/case/L1/requests",
                          json={"code": "WR", "sent_to": "hr@alpha.example"},
                          params={"user_id": TENANT}, headers=AUTH).json()
    r = client.patch(f"/api/aria/vetting/case/L1/requests/{created['request_id']}",
                     json={"status": "REPLY_RECEIVED"},
                     params={"user_id": TENANT}, headers=AUTH)
    assert r.status_code == 200
    body = _ledger(client, as_of="2027-01-01")
    assert body["summary"]["open"] == 0
    assert body["summary"]["closed"] == 1
    assert body["summary"]["overdue"] == 0


def test_the_ledger_is_tenant_scoped(client):
    assert _case(client).status_code == 200
    client.post("/api/aria/vetting/case/L1/requests",
                json={"code": "WR", "sent_to": "x"},
                params={"user_id": TENANT}, headers=AUTH)
    other = client.get("/api/aria/vetting/case/L1/requests",
                       params={"user_id": "tenant-b"}, headers=AUTH).json()
    assert other["requests"] == []


# ── R-F3204: sighting over HTTP ───────────────────────────────────────────

def _upload(client, case_id="L1"):
    with patch("aria_service.vetting.documents.extract_document",
               new=AsyncMock(return_value={"available": True, "data": {
                   "doc_type": "PASSPORT", "confidence": 0.97}})):
        return client.post(f"/api/aria/vetting/case/{case_id}/documents", json={
            "filename": "passport.txt",
            "content_base64": base64.b64encode(b"passport").decode(),
            "declared_doc_type": "PASSPORT"},
            params={"user_id": TENANT}, headers=AUTH).json()


def test_recording_an_original_requires_a_named_examiner(client):
    """7.4 c) asks WHO examined and copied it. An unattributed sighting cannot
    be evidenced, so refusing beats accepting one that will not stand up."""
    assert _case(client).status_code == 200
    doc = _upload(client)
    r = client.patch(
        f"/api/aria/vetting/case/L1/documents/{doc['document_id']}/sighting",
        json={"sighting": "ORIGINAL_SEEN"},
        params={"user_id": TENANT}, headers=AUTH)
    assert r.status_code == 422
    assert r.json()["detail"]["code"] == "examiner_required"


def test_a_named_sighting_is_recorded_and_clears_the_finding(client):
    assert _case(client).status_code == 200
    doc = _upload(client)
    # Before: the passport is flagged as unrecorded.
    before = client.post("/api/aria/vetting/case/L1/assess",
                         params={"user_id": TENANT}, headers=AUTH).json()
    assert "SIGHTING_NOT_RECORDED" in {f["code"] for f in before["findings"]}

    r = client.patch(
        f"/api/aria/vetting/case/L1/documents/{doc['document_id']}/sighting",
        json={"sighting": "ORIGINAL_SEEN", "examined_by": "S. Officer"},
        params={"user_id": TENANT}, headers=AUTH)
    assert r.status_code == 200, r.text

    after = client.post("/api/aria/vetting/case/L1/assess",
                        params={"user_id": TENANT}, headers=AUTH).json()
    codes = {f["code"] for f in after["findings"]}
    assert "SIGHTING_NOT_RECORDED" not in codes
    assert "EXAMINER_NOT_RECORDED" not in codes


def test_copy_only_raises_the_original_not_sighted_finding(client):
    assert _case(client).status_code == 200
    doc = _upload(client)
    client.patch(
        f"/api/aria/vetting/case/L1/documents/{doc['document_id']}/sighting",
        json={"sighting": "COPY_ONLY"}, params={"user_id": TENANT}, headers=AUTH)
    result = client.post("/api/aria/vetting/case/L1/assess",
                         params={"user_id": TENANT}, headers=AUTH).json()
    assert "ORIGINAL_NOT_SIGHTED" in {f["code"] for f in result["findings"]}


def test_an_unknown_sighting_value_is_refused(client):
    assert _case(client).status_code == 200
    doc = _upload(client)
    r = client.patch(
        f"/api/aria/vetting/case/L1/documents/{doc['document_id']}/sighting",
        json={"sighting": "PROBABLY_FINE"},
        params={"user_id": TENANT}, headers=AUTH)
    assert r.status_code == 422


def test_sighting_on_an_unknown_document_is_404(client):
    assert _case(client).status_code == 200
    r = client.patch(
        "/api/aria/vetting/case/L1/documents/nope/sighting",
        json={"sighting": "COPY_ONLY"}, params={"user_id": TENANT}, headers=AUTH)
    assert r.status_code == 404
