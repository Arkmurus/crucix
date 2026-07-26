"""R-F3162/R-F3164 — the Art. 10 regime follows the case's jurisdiction.

The defect: the gate applied UK DPA 2018 Schedule 1 to EVERY case, whatever
pack it was pinned to. A Portuguese or INTL case could therefore be
"authorised" by a UK condition, which authorises nothing there. EU GDPR Art. 10
delegates to Union or Member State law, and member states diverge sharply on
whether a private employer may process conviction data at all — so there is no
pan-EU list to substitute, and inventing one would be worse than refusing.

A case file that LOOKS authorised and is not is worse than one that never
opened.
"""

from __future__ import annotations

import base64
from datetime import date, timedelta
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from aria_service.vetting.legal_basis import (
    REVIEWED_JURISDICTIONS, JurisdictionNotReviewed, Sch1Condition,
    recommendation_for, regime_for,
)

TOKEN = "vetting-jur-token"
AUTH = {"Authorization": f"Bearer {TOKEN}"}
TENANT = "tenant-a"
TODAY = date(2026, 7, 26)


# ── the regime map ────────────────────────────────────────────────────────

def test_gb_has_a_reviewed_regime():
    assert "DPA 2018" in regime_for("GB")
    assert "GB" in REVIEWED_JURISDICTIONS


@pytest.mark.parametrize("code", ["PT", "DE", "FR", "IE", "INTL", "", "XX"])
def test_unreviewed_jurisdictions_are_refused(code):
    """Refusing is correct. Substituting UK statute would be a false clean at
    the legal layer."""
    with pytest.raises(JurisdictionNotReviewed):
        regime_for(code)


def test_the_refusal_explains_why_rather_than_just_failing():
    try:
        regime_for("DE")
    except JurisdictionNotReviewed as exc:
        message = str(exc)
    assert "Member State" in message or "member state" in message.lower()
    assert "legal review" in message


# ── the recommendation (R-F3164) ──────────────────────────────────────────

def test_gb_recommendation_is_the_standard_employment_condition():
    rec = recommendation_for("GB")
    assert rec["condition"] == Sch1Condition.EMPLOYMENT_SOCIAL_SECURITY.value
    assert rec["apd_required"] is True
    assert rec["confirmed_by_controller_required"] is True, (
        "the processor must not be recorded as having chosen the condition")
    assert rec["rationale"]


def test_no_recommendation_where_the_jurisdiction_is_not_reviewed():
    """Offering a recommendation would imply the processing is available."""
    for code in ("PT", "DE", "INTL"):
        assert recommendation_for(code) == {}


# ── capability: the real routes ───────────────────────────────────────────

@pytest.fixture()
def client(tmp_path, monkeypatch):
    from aria_service.routes.vetting import router as vetting_router
    from aria_service.vetting import store as store_module
    import aria_service.intel.dd_evidence_store as ev

    monkeypatch.setenv("ARIA_API_TOKEN", TOKEN)
    monkeypatch.setenv("ARIA_VETTING_DB", str(tmp_path / "cases.db"))
    monkeypatch.setenv("ARIA_DD_EVIDENCE_DB", str(tmp_path / "evidence.db"))
    monkeypatch.setenv("ARIA_DD_EVIDENCE_ARTIFACT_DIR", str(tmp_path / "art"))
    monkeypatch.setattr(store_module, "_STORE", None, raising=False)
    monkeypatch.setattr(ev, "_STORE", None, raising=False)
    app = FastAPI()
    app.include_router(vetting_router)
    return TestClient(app)


def _create(client, case_id, pack_id="uk_bs7858"):
    return client.post("/api/aria/vetting/cases", json={
        "case_id": case_id, "applicant_name": "Test Applicant",
        "date_of_birth": "1990-01-01", "employment_start": "2026-06-01",
        "pack_id": pack_id,
    }, params={"user_id": TENANT}, headers=AUTH)


def _record_basis(client):
    return client.post("/api/aria/vetting/legal-basis", json={
        "condition": "SCH1_P1_1_EMPLOYMENT",
        "apd_reference": "APD-2026-01",
        "apd_review_date": (TODAY + timedelta(days=365)).isoformat(),
        "determined_by": "Customer DPO",
    }, params={"user_id": TENANT}, headers=AUTH)


def test_rf3162_uk_case_proceeds_with_a_recorded_basis(client):
    assert _create(client, "JUR-GB").status_code == 200
    assert _record_basis(client).status_code == 200
    r = client.patch("/api/aria/vetting/case/JUR-GB",
                     json={"inputs": {"convictions_declared": True}},
                     params={"user_id": TENANT}, headers=AUTH)
    assert r.status_code == 200, r.text


def test_rf3162_intl_case_is_refused_even_with_a_uk_basis(client):
    """THE capability test: a UK Schedule 1 condition must not authorise
    criminal-offence processing under a non-UK pack."""
    assert _create(client, "JUR-INTL", pack_id="intl_baseline").status_code == 200
    assert _record_basis(client).status_code == 200      # valid UK position

    r = client.patch("/api/aria/vetting/case/JUR-INTL",
                     json={"inputs": {"convictions_declared": True}},
                     params={"user_id": TENANT}, headers=AUTH)
    assert r.status_code == 409, r.text
    detail = r.json()["detail"]
    assert detail["code"] == "jurisdiction_not_reviewed"
    assert detail["jurisdiction"] == "INTL"

    # And it really did not land.
    case = client.get("/api/aria/vetting/case/JUR-INTL",
                      params={"user_id": TENANT}, headers=AUTH).json()
    assert case["inputs"]["convictions_declared"] is False


def test_rf3162_intl_document_upload_is_refused_too(client):
    assert _create(client, "JUR-DOC", pack_id="intl_baseline").status_code == 200
    assert _record_basis(client).status_code == 200
    with patch("aria_service.vetting.documents.extract_document",
               new=AsyncMock(return_value={"available": True, "data": {
                   "doc_type": "DISCLOSURE_CERTIFICATE", "confidence": 0.97}})):
        r = client.post("/api/aria/vetting/case/JUR-DOC/documents",
                        json={"filename": "dbs.txt",
                              "content_base64": base64.b64encode(b"x").decode(),
                              "declared_doc_type": "DISCLOSURE_CERTIFICATE"},
                        params={"user_id": TENANT}, headers=AUTH)
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "jurisdiction_not_reviewed"


def test_rf3162_non_criminal_work_is_unaffected_on_any_jurisdiction(client):
    """The refusal is scoped to Art. 10 data. Ordinary screening under a
    framework pack must still work."""
    assert _create(client, "JUR-OK", pack_id="intl_baseline").status_code == 200
    r = client.patch("/api/aria/vetting/case/JUR-OK",
                     json={"extension_approved": True},
                     params={"user_id": TENANT}, headers=AUTH)
    assert r.status_code == 200, r.text
    a = client.post("/api/aria/vetting/case/JUR-OK/assess",
                    params={"user_id": TENANT}, headers=AUTH)
    assert a.status_code == 200


def test_rf3164_legal_basis_surface_offers_a_confirmable_recommendation(client):
    body = client.get("/api/aria/vetting/legal-basis",
                      params={"user_id": TENANT}, headers=AUTH).json()
    assert body["recommended"]["condition"] == "SCH1_P1_1_EMPLOYMENT"
    assert body["recommended"]["confirmed_by_controller_required"] is True
    assert "GB" in body["reviewed_jurisdictions"]
    assert "INTL" not in body["reviewed_jurisdictions"]


def test_rf3163_extraction_prompt_contains_no_prohibited_practice():
    """EU AI Act Art. 5 — emotion inference in the workplace is PROHIBITED,
    not merely high-risk. The extraction prompt must never drift into
    assessing the person."""
    from aria_service.vetting.documents import _EXTRACTION_SYSTEM_PROMPT

    lowered = _EXTRACTION_SYSTEM_PROMPT.lower()
    for banned in ("emotion", "sentiment", "personality", "trustworth",
                   "credibility of the applicant", "risk score"):
        assert banned not in lowered, (
            f"the extraction prompt mentions '{banned}' — inferring traits or "
            f"emotion about a worker is an Art. 5 prohibited practice")
    assert "not assessing the person" in lowered, (
        "the prompt must explicitly forbid assessing the person")


# ── R-F3171/R-F3172 — defects found reviewing the first real card ─────────

def test_rf3171_create_is_art10_gated(client):
    """CREATE was NOT gated while PATCH and upload were, so conviction data
    entered ungated AND the case then became permanently un-editable: every
    later write hit the gate the create had skipped."""
    r = client.post("/api/aria/vetting/cases", json={
        "case_id": "GATE-1", "applicant_name": "T",
        "date_of_birth": "1990-01-01", "employment_start": "2026-06-01",
        "pack_id": "uk_bs7858",
        "inputs": {"convictions_declared": True},
    }, params={"user_id": TENANT}, headers=AUTH)
    assert r.status_code == 409, r.text
    assert r.json()["detail"]["code"] == "art10_basis_required"
    # And nothing was created — the message promises that explicitly.
    assert client.get("/api/aria/vetting/case/GATE-1",
                      params={"user_id": TENANT}, headers=AUTH).status_code == 404


def test_rf3171_ordinary_create_is_unaffected(client):
    assert _create(client, "GATE-OK").status_code == 200


def test_rf3172_editing_a_case_marks_its_verdict_stale(client):
    """THE false clean caching reintroduced: a case assessed clean, then
    changed, kept showing the clean verdict with a current-looking date."""
    assert _create(client, "STALE-1").status_code == 200
    before = client.post("/api/aria/vetting/case/STALE-1/assess",
                         params={"user_id": TENANT}, headers=AUTH).json()
    row = lambda: next(c for c in client.get(
        "/api/aria/vetting/cases", params={"user_id": TENANT}, headers=AUTH
    ).json()["cases"] if c["case_id"] == "STALE-1")

    assert row()["assessment_stale"] is False
    assert row()["last_status"] == before["status"]

    # Add a future-dated entry — a BLOCKER the cached verdict knows nothing of.
    assert client.patch("/api/aria/vetting/case/STALE-1", json={"career": [
        {"entry_id": "e1", "entry_type": "EMPLOYMENT", "start": "2027-01-01",
         "organisation": "Future Co"}]},
        params={"user_id": TENANT}, headers=AUTH).status_code == 200

    assert row()["assessment_stale"] is True, (
        "the cached verdict now describes a file that no longer exists and "
        "must be flagged, or the card shows a clean status for a blocked file")

    # Re-assessing clears it and tells the truth.
    after = client.post("/api/aria/vetting/case/STALE-1/assess",
                        params={"user_id": TENANT}, headers=AUTH).json()
    assert after["status"] == "NOT_READY"
    assert row()["assessment_stale"] is False
    assert row()["last_blockers"] >= 1


def test_rf3172_uploading_a_document_also_marks_stale(client):
    import base64
    from unittest.mock import AsyncMock, patch as _patch
    assert _create(client, "STALE-2").status_code == 200
    client.post("/api/aria/vetting/case/STALE-2/assess",
                params={"user_id": TENANT}, headers=AUTH)
    with _patch("aria_service.vetting.documents.extract_document",
                new=AsyncMock(return_value={"available": True, "data": {
                    "doc_type": "P60", "confidence": 0.95}})):
        assert client.post("/api/aria/vetting/case/STALE-2/documents",
                           json={"filename": "p60.txt",
                                 "content_base64": base64.b64encode(b"x").decode()},
                           params={"user_id": TENANT},
                           headers=AUTH).status_code == 200
    row = next(c for c in client.get("/api/aria/vetting/cases",
                                     params={"user_id": TENANT}, headers=AUTH
                                     ).json()["cases"] if c["case_id"] == "STALE-2")
    assert row["assessment_stale"] is True, (
        "new evidence invalidates the recorded verdict just as an edit does")


def test_rf3172_staleness_defaults_safe_for_an_unknown_writer():
    """The invalidation lives in store.save() with mark_stale defaulting TRUE,
    so a future writer that forgets gets the SAFE outcome."""
    import inspect
    from aria_service.vetting.store import VettingCaseStore
    sig = inspect.signature(VettingCaseStore.save)
    assert sig.parameters["mark_stale"].default is True
