"""R-F3144/R-F3145 — vetting evidence intake and document extraction.

The property that matters most here is negative: an extraction that FAILED
must never be indistinguishable from an extraction that succeeded and found
nothing wrong. On a screening file, "we could not read this" and "we read
this and it was fine" lead to opposite human actions.
"""

from __future__ import annotations

import base64
from datetime import date
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from aria_service.vetting.documents import (
    CONFIDENCE_FLOOR,
    apply_extraction,
    build_evidence_record,
    decode_text_best_effort,
    needs_human_review,
    sha256_hex,
)
from aria_service.vetting.models import DocumentType

TOKEN = "vetting-doc-token"
AUTH = {"Authorization": f"Bearer {TOKEN}"}
TENANT = "tenant-a"
OTHER_TENANT = "tenant-b"


# ── unit: the extraction contract ─────────────────────────────────────────

def test_failed_extraction_is_disclosed_not_silently_accepted():
    doc = apply_extraction(
        document_id="d1", evidence_id="e1",
        fallback_doc_type=DocumentType.PAYSLIP,
        extraction={"available": False, "reason": "extraction unavailable (timeout)"},
    )
    assert doc.extraction_confidence == 0.0
    assert "extraction_unavailable" in doc.authenticity_flags
    assert needs_human_review(doc) is True, (
        "a document whose extraction failed must route to a human — it must "
        "never read as evidence that was examined and found unremarkable"
    )


def test_high_confidence_extraction_sets_type_and_dates():
    doc = apply_extraction(
        document_id="d2", evidence_id="e2",
        fallback_doc_type=DocumentType.OTHER,
        extraction={"available": True, "data": {
            "doc_type": "P60", "confidence": 0.94, "issuer": "HMRC",
            "covers_from": "2024-04-06", "covers_to": "2025-04-05",
            "authenticity_concerns": [],
        }},
    )
    assert doc.doc_type is DocumentType.P60
    assert doc.covers_from == date(2024, 4, 6)
    assert doc.covers_to == date(2025, 4, 5)
    assert doc.issuer == "HMRC"
    assert needs_human_review(doc) is False


def test_low_confidence_does_not_change_the_document_type():
    """Below the floor the model's guess is recorded, not acted on."""
    doc = apply_extraction(
        document_id="d3", evidence_id="e3",
        fallback_doc_type=DocumentType.OTHER,
        extraction={"available": True, "data": {
            "doc_type": "PASSPORT", "confidence": CONFIDENCE_FLOOR - 0.01,
        }},
    )
    assert doc.doc_type is DocumentType.OTHER
    assert any(f.startswith("low_confidence_classification")
               for f in doc.authenticity_flags)
    assert needs_human_review(doc) is True


def test_authenticity_concerns_force_human_review_even_at_high_confidence():
    doc = apply_extraction(
        document_id="d4", evidence_id="e4",
        fallback_doc_type=DocumentType.PAYSLIP,
        extraction={"available": True, "data": {
            "doc_type": "PAYSLIP", "confidence": 0.99,
            "authenticity_concerns": ["net pay does not equal gross minus deductions"],
        }},
    )
    assert doc.doc_type is DocumentType.PAYSLIP
    assert needs_human_review(doc) is True, (
        "a confident classification must not override an authenticity concern"
    )
    assert any("authenticity:" in f for f in doc.authenticity_flags)


def test_impossible_coverage_window_is_dropped_not_normalised():
    doc = apply_extraction(
        document_id="d5", evidence_id="e5",
        fallback_doc_type=DocumentType.PAYSLIP,
        extraction={"available": True, "data": {
            "doc_type": "PAYSLIP", "confidence": 0.95,
            "covers_from": "2025-01-01", "covers_to": "2024-01-01",
        }},
    )
    assert doc.covers_from is None and doc.covers_to is None
    assert "inconsistent_coverage_dates" in doc.authenticity_flags


def test_binary_documents_are_not_pretend_parsed():
    """A PDF must yield no text, so it routes to a human rather than to a
    confident-looking extraction from bytes we never decoded."""
    assert decode_text_best_effort(b"%PDF-1.7\n%\xe2\xe3", "payslip.pdf") == ""


def test_evidence_record_declares_user_supplied_authority():
    record = build_evidence_record(
        tenant_id=TENANT, case_id="C1", evidence_id="e1",
        content_hash="a" * 64, filename="p60.pdf", subject_entity_id="A Person",
    )
    # Overstating the authority of an applicant-supplied document at intake is
    # the hardest error to correct later.
    assert record["source_authority"] == "user_supplied"
    assert record["snapshot_policy"] == "permitted"
    assert record["tenant_id"] == TENANT


def test_same_bytes_same_case_produce_one_evidence_attempt_id():
    args = dict(tenant_id=TENANT, case_id="C1", filename="p60.pdf",
                subject_entity_id="A Person", content_hash=sha256_hex(b"x"))
    a = build_evidence_record(evidence_id="e1", **args)
    b = build_evidence_record(evidence_id="e2", **args)
    assert a["source_attempt_id"] == b["source_attempt_id"], (
        "re-uploading identical bytes must de-duplicate, not double-count as "
        "two independent pieces of evidence"
    )


# ── capability: the real upload route ─────────────────────────────────────

@pytest.fixture()
def client(tmp_path, monkeypatch):
    from aria_service.routes.vetting import router as vetting_router
    from aria_service.vetting import store as store_module

    monkeypatch.setenv("ARIA_API_TOKEN", TOKEN)
    monkeypatch.setenv("ARIA_VETTING_DB", str(tmp_path / "cases.db"))
    monkeypatch.setenv("ARIA_DD_EVIDENCE_DB", str(tmp_path / "evidence.db"))
    monkeypatch.setenv("ARIA_DD_EVIDENCE_ARTIFACT_DIR", str(tmp_path / "artifacts"))
    monkeypatch.setattr(store_module, "_STORE", None, raising=False)
    # The evidence store is also a process singleton.
    import aria_service.intel.dd_evidence_store as ev
    monkeypatch.setattr(ev, "_STORE", None, raising=False)

    app = FastAPI()
    app.include_router(vetting_router)
    return TestClient(app)


def _make_case(client, case_id="DOC-1", tenant=TENANT):
    return client.post("/api/aria/vetting/cases", json={
        "case_id": case_id, "applicant_name": "Test Applicant",
        "date_of_birth": "1990-01-01", "employment_start": "2026-06-01",
        "pack_id": "uk_bs7858",
    }, params={"user_id": tenant}, headers=AUTH)


def _upload(client, case_id, content=b"payslip text", tenant=TENANT,
            filename="payslip.txt"):
    return client.post(
        f"/api/aria/vetting/case/{case_id}/documents",
        json={"filename": filename,
              "content_base64": base64.b64encode(content).decode(),
              "declared_doc_type": "PAYSLIP"},
        params={"user_id": tenant}, headers=AUTH)


def test_rf3144_upload_stores_hash_verified_evidence(client):
    assert _make_case(client).status_code == 200
    with patch("aria_service.vetting.documents.extract_document",
               new=AsyncMock(return_value={"available": True, "data": {
                   "doc_type": "PAYSLIP", "confidence": 0.95}})):
        r = _upload(client, "DOC-1")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["content_hash_verified"] is True
    assert body["evidence_id"]
    assert body["needs_human_review"] is False


def test_rf3145_llm_failure_still_stores_but_flags_for_a_human(client):
    """The decisive test: the LLM is down, the document is still on file, and
    it is explicitly NOT presented as examined-and-fine."""
    assert _make_case(client, "DOC-2").status_code == 200
    with patch("aria_service.vetting.documents.extract_document",
               new=AsyncMock(return_value={
                   "available": False, "reason": "extraction unavailable (timeout)"})):
        r = _upload(client, "DOC-2")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["content_hash_verified"] is True   # the bytes ARE retained
    assert body["needs_human_review"] is True
    assert "extraction_unavailable" in body["authenticity_flags"]
    assert body["extraction_confidence"] == 0.0


def test_rf3144_upload_is_tenant_scoped(client):
    assert _make_case(client, "DOC-3").status_code == 200
    r = _upload(client, "DOC-3", tenant=OTHER_TENANT)
    assert r.status_code == 404, (
        f"another tenant uploaded into this case: {r.status_code} {r.text}")


def test_rf3144_uploaded_document_lands_on_the_case(client):
    assert _make_case(client, "DOC-4").status_code == 200
    with patch("aria_service.vetting.documents.extract_document",
               new=AsyncMock(return_value={"available": True, "data": {
                   "doc_type": "P60", "confidence": 0.92, "issuer": "HMRC"}})):
        assert _upload(client, "DOC-4").status_code == 200
    case = client.get("/api/aria/vetting/case/DOC-4",
                      params={"user_id": TENANT}, headers=AUTH).json()
    assert len(case["documents"]) == 1
    assert case["documents"][0]["doc_type"] == "P60"


def test_rf3144_rejects_invalid_base64(client):
    assert _make_case(client, "DOC-5").status_code == 200
    r = client.post("/api/aria/vetting/case/DOC-5/documents",
                    json={"filename": "x.txt", "content_base64": "!!!not-base64!!!"},
                    params={"user_id": TENANT}, headers=AUTH)
    assert r.status_code == 422


def test_rf3144_rejects_empty_document(client):
    assert _make_case(client, "DOC-6").status_code == 200
    r = client.post("/api/aria/vetting/case/DOC-6/documents",
                    json={"filename": "x.txt",
                          "content_base64": base64.b64encode(b"").decode()},
                    params={"user_id": TENANT}, headers=AUTH)
    assert r.status_code == 422
