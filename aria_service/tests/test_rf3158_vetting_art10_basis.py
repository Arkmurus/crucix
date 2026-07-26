"""R-F3158 — Art. 10 / DPA 2018 Sch. 1, enforced rather than recorded.

The defect: R-F3153 added `criminal_data_condition` as a free string. A field
nobody validates is the same shape as a gate that cannot fail — it looks like
compliance and asserts nothing. A blank string satisfied it.

These tests pin the enforcement: criminal-offence data cannot enter a case
until the tenant has recorded a Schedule 1 condition AND the appropriate policy
document that condition requires (Sch. 1 Pt 4 para 5), in place AT THE TIME of
processing.
"""

from __future__ import annotations

import base64
from datetime import date, timedelta
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from aria_service.vetting.legal_basis import (
    Art10Position, LegalBasisError, Sch1Condition,
    holds_criminal_offence_data, requires_apd, validate_position,
)
from aria_service.vetting.models import (
    DocumentType, ScreeningInputs, UploadedDocument, VettingCase,
)

TOKEN = "vetting-art10-token"
AUTH = {"Authorization": f"Bearer {TOKEN}"}
TENANT = "tenant-a"
TODAY = date(2026, 7, 26)


def _position(**kw) -> Art10Position:
    base = dict(
        tenant_id=TENANT,
        condition=Sch1Condition.EMPLOYMENT_SOCIAL_SECURITY,
        apd_reference="APD-2026-01",
        apd_review_date=TODAY + timedelta(days=180),
        determined_by="Data Protection Officer",
    )
    base.update(kw)
    return Art10Position(**base)


# ── which conditions need an APD ──────────────────────────────────────────

def test_part1_and_part2_conditions_require_an_apd():
    """Sch. 1 Pt 4 para 5 — Part 1 and Part 2 conditions require an APD."""
    for c in (Sch1Condition.EMPLOYMENT_SOCIAL_SECURITY,
              Sch1Condition.PREVENTING_DETECTING_UNLAWFUL_ACTS,
              Sch1Condition.REGULATORY_REQUIREMENTS,
              Sch1Condition.SAFEGUARDING):
        assert requires_apd(c) is True
    assert requires_apd(Sch1Condition.LEGAL_CLAIMS) is False


def test_legal_claims_cannot_carry_routine_screening():
    """Pt 3 para 33 covers legal proceedings, not an ongoing screening
    programme. It is the condition most likely to be mis-selected because it
    needs no APD."""
    with pytest.raises(LegalBasisError, match="routine"):
        validate_position(_position(condition=Sch1Condition.LEGAL_CLAIMS,
                                    apd_reference="", apd_review_date=None),
                          TODAY)


# ── what makes a position valid ───────────────────────────────────────────

def test_valid_position_passes():
    validate_position(_position(), TODAY)      # must not raise


def test_missing_apd_is_refused():
    with pytest.raises(LegalBasisError, match="appropriate policy document"):
        validate_position(_position(apd_reference=""), TODAY)


def test_apd_without_a_review_date_is_refused():
    """Sch. 1 Pt 4 para 5(2)(b) — the APD must be kept under review."""
    with pytest.raises(LegalBasisError, match="under review"):
        validate_position(_position(apd_review_date=None), TODAY)


def test_expired_apd_review_is_refused():
    with pytest.raises(LegalBasisError, match="has passed"):
        validate_position(_position(apd_review_date=TODAY - timedelta(days=1)),
                          TODAY)


def test_unattributed_determination_is_refused():
    """Art. 5(2): a legal position nobody owns cannot be demonstrated."""
    with pytest.raises(LegalBasisError, match="attributed"):
        validate_position(_position(determined_by=""), TODAY)


# ── which cases actually engage Art. 10 ───────────────────────────────────

def _case(**kw) -> VettingCase:
    base = dict(tenant_id=TENANT, case_id="A1", applicant_name="T",
                date_of_birth=date(1990, 1, 1),
                employment_start=date(2026, 6, 1))
    base.update(kw)
    return VettingCase(**base)


def test_an_ordinary_case_does_not_engage_article_10():
    """Requiring a condition before any conviction data exists would be its
    own compliance theatre."""
    assert holds_criminal_offence_data(_case()) is False


def test_declared_convictions_engage_article_10():
    assert holds_criminal_offence_data(
        _case(inputs=ScreeningInputs(convictions_declared=True))) is True


def test_a_disclosure_certificate_engages_article_10():
    assert holds_criminal_offence_data(_case(documents=[
        UploadedDocument(document_id="d1",
                         doc_type=DocumentType.DISCLOSURE_CERTIFICATE)])) is True


def test_detector_is_not_fooled_by_a_dict_shaped_case():
    """Regression: `model_copy(update=<dumped dict>)` leaves `inputs` as a raw
    dict. A bare getattr() then returns the default and the Art. 10 gate
    reports "no criminal data" — the detector silently missing on the very
    path that produces it. Reachability depended on statement ordering in one
    route, which is not a guarantee."""
    case = _case()
    as_dict = case.model_copy(update={"inputs": {"convictions_declared": True}})
    assert holds_criminal_offence_data(as_dict) is True

    doc_dict = case.model_copy(update={
        "documents": [{"document_id": "d1",
                       "doc_type": "DISCLOSURE_CERTIFICATE"}]})
    assert holds_criminal_offence_data(doc_dict) is True

    route_dict = case.model_copy(update={
        "inputs": {"criminality_route": "NPCC_POLICE_LETTER"}})
    assert holds_criminal_offence_data(route_dict) is True


def test_a_police_letter_engages_article_10():
    assert holds_criminal_offence_data(_case(
        inputs=ScreeningInputs(
            criminality_route=DocumentType.NPCC_POLICE_LETTER))) is True


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


def _create(client, case_id="A10-1"):
    return client.post("/api/aria/vetting/cases", json={
        "case_id": case_id, "applicant_name": "Test Applicant",
        "date_of_birth": "1990-01-01", "employment_start": "2026-06-01",
        "pack_id": "uk_bs7858",
    }, params={"user_id": TENANT}, headers=AUTH)


def _record_basis(client, **over):
    body = {
        "condition": "SCH1_P1_1_EMPLOYMENT",
        "apd_reference": "APD-2026-01",
        "apd_review_date": (TODAY + timedelta(days=365)).isoformat(),
        "dpia_reference": "DPIA-VETTING-2026-01",
        "determined_by": "Data Protection Officer",
    }
    body.update(over)
    return client.post("/api/aria/vetting/legal-basis", json=body,
                       params={"user_id": TENANT}, headers=AUTH)


def test_rf3158_declaring_convictions_is_refused_without_a_basis(client):
    """THE capability test: the data must not be able to enter the file."""
    assert _create(client, "A10-1").status_code == 200
    r = client.patch("/api/aria/vetting/case/A10-1",
                     json={"inputs": {"convictions_declared": True}},
                     params={"user_id": TENANT}, headers=AUTH)
    assert r.status_code == 409, r.text
    assert r.json()["detail"]["code"] == "art10_basis_required"

    # And it really did not land.
    case = client.get("/api/aria/vetting/case/A10-1",
                      params={"user_id": TENANT}, headers=AUTH).json()
    assert case["inputs"]["convictions_declared"] is False


def test_rf3158_same_update_succeeds_once_the_basis_is_recorded(client):
    assert _create(client, "A10-2").status_code == 200
    assert _record_basis(client).status_code == 200
    r = client.patch("/api/aria/vetting/case/A10-2",
                     json={"inputs": {"convictions_declared": True}},
                     params={"user_id": TENANT}, headers=AUTH)
    assert r.status_code == 200, r.text


def test_rf3158_ordinary_updates_are_unaffected(client):
    """A case with no conviction data must not be blocked."""
    assert _create(client, "A10-3").status_code == 200
    r = client.patch("/api/aria/vetting/case/A10-3",
                     json={"extension_approved": True},
                     params={"user_id": TENANT}, headers=AUTH)
    assert r.status_code == 200, r.text


def test_rf3158_uploading_a_disclosure_certificate_is_gated(client):
    assert _create(client, "A10-4").status_code == 200
    with patch("aria_service.vetting.documents.extract_document",
               new=AsyncMock(return_value={"available": True, "data": {
                   "doc_type": "DISCLOSURE_CERTIFICATE", "confidence": 0.97}})):
        r = client.post("/api/aria/vetting/case/A10-4/documents",
                        json={"filename": "dbs.txt",
                              "content_base64": base64.b64encode(b"dbs").decode(),
                              "declared_doc_type": "DISCLOSURE_CERTIFICATE"},
                        params={"user_id": TENANT}, headers=AUTH)
    assert r.status_code == 409, r.text
    assert r.json()["detail"]["code"] == "art10_basis_required"


def test_rf3158_an_invalid_position_is_refused_at_record_time(client):
    """Storing an invalid position and complaining later would leave a tenant
    believing they had a basis."""
    r = _record_basis(client, apd_reference="")
    assert r.status_code == 422
    assert r.json()["detail"]["code"] == "invalid_legal_basis"

    expired = _record_basis(
        client, apd_review_date=(TODAY - timedelta(days=1)).isoformat())
    assert expired.status_code == 422


def test_rf3158_legal_basis_surface_lists_conditions_and_apd_need(client):
    before = client.get("/api/aria/vetting/legal-basis",
                        params={"user_id": TENANT}, headers=AUTH).json()
    assert before["recorded"] is False
    assert before["valid"] is False
    by_code = {c["code"]: c for c in before["available_conditions"]}
    assert by_code["SCH1_P1_1_EMPLOYMENT"]["apd_required"] is True
    assert by_code["SCH1_P3_33_LEGAL_CLAIMS"]["apd_required"] is False

    assert _record_basis(client).status_code == 200
    after = client.get("/api/aria/vetting/legal-basis",
                       params={"user_id": TENANT}, headers=AUTH).json()
    assert after["recorded"] is True and after["valid"] is True


def test_rf3158_legal_basis_is_tenant_scoped(client):
    assert _record_basis(client).status_code == 200
    other = client.get("/api/aria/vetting/legal-basis",
                       params={"user_id": "tenant-b"}, headers=AUTH).json()
    assert other["recorded"] is False, (
        "one tenant's Art. 10 position must never authorise another's "
        "criminal-offence processing")
