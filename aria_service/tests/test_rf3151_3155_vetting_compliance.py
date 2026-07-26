"""R-F3151..R-F3155 — the vetting module's legal obligations, as tests.

Each test names the Article it enforces. These are not style preferences: a
failure here is a compliance failure, and several of them lock defects that
were live in this module before these commits.
"""

from __future__ import annotations

import base64
from datetime import UTC, date, datetime
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from aria_service.vetting.crypto import (
    DecryptionFailed, decrypt, encrypt, new_case_key,
)
from aria_service.vetting.decisions import (
    DecisionError, DecisionOutcome, record_decision,
)
from aria_service.vetting.models import (
    DocumentType, UploadedDocument, VettingCase,
)
from aria_service.vetting.processors import (
    approved_processors, assert_served_by_approved,
)
from aria_service.vetting.retention import CaseOutcome, plan_disposal

TOKEN = "vetting-compliance-token"
AUTH = {"Authorization": f"Bearer {TOKEN}"}
TENANT = "tenant-a"


# ── R-F3151: Art. 44 / Art. 10 — data residency ───────────────────────────

def test_default_approved_processor_excludes_the_app_chain_default():
    """The app chain's default is DeepSeek ('everything else on DeepSeek',
    llm/fallback.py:29). It must NOT be approved for vetting personal data:
    no adequacy decision, and this data includes criminal-offence records."""
    approved = approved_processors()
    assert "deepseek" not in approved
    assert approved == ["anthropic"]


def test_unapproved_processor_is_detected(monkeypatch):
    monkeypatch.setenv("ARIA_VETTING_LLM_PROVIDERS", "anthropic")
    assert assert_served_by_approved("anthropic") is True
    assert assert_served_by_approved("deepseek") is False
    assert assert_served_by_approved("") is True   # nothing served, nothing sent


@pytest.mark.asyncio
async def test_extraction_refuses_when_no_approved_processor(monkeypatch):
    """Fail-CLOSED. With no approved processor, no personal data is sent at
    all — the document still reaches a human, which is slower but lawful."""
    from aria_service.vetting import documents as docs

    monkeypatch.setenv("ARIA_VETTING_LLM_PROVIDERS", "anthropic")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    called = False

    async def _must_not_be_called(*a, **k):
        nonlocal called
        called = True
        raise AssertionError("call_structured must not run without an approved processor")

    monkeypatch.setattr(
        "aria_service.llm.structured.call_structured", _must_not_be_called)
    result = await docs.extract_document(text="payslip", filename="p.txt")
    assert result["available"] is False
    assert "no approved processor" in result["reason"]
    assert called is False, "personal data must never be transmitted here"


# ── R-F3152: coherence — the retention clock must be reachable ────────────

def test_invalid_outcome_is_rejected_at_the_model():
    """Previously a free str: retention.py does CaseOutcome(case.outcome), so
    any typo raised ValueError INSIDE the route — a 500."""
    with pytest.raises(ValueError):
        VettingCase(tenant_id=TENANT, case_id="C", applicant_name="T",
                    date_of_birth=date(1990, 1, 1),
                    employment_start=date(2026, 6, 1),
                    outcome="NOT_A_REAL_OUTCOME")


def test_model_and_retention_outcome_vocabularies_cannot_drift():
    from typing import get_args
    from aria_service.vetting.models import CaseOutcomeLiteral
    assert set(get_args(CaseOutcomeLiteral)) == {o.value for o in CaseOutcome}


def test_employment_end_before_start_is_rejected():
    with pytest.raises(ValueError):
        VettingCase(tenant_id=TENANT, case_id="C", applicant_name="T",
                    date_of_birth=date(1990, 1, 1),
                    employment_start=date(2026, 6, 1),
                    employment_end=date(2025, 1, 1))


def test_consent_is_not_an_available_lawful_basis():
    """Art. 4(11)/Art. 7(4): consent is not freely given in an employment
    relationship. Offering it would let a customer pick the one basis a
    regulator reliably rejects."""
    from typing import get_args
    from aria_service.vetting.models import LawfulBasisLiteral
    bases = set(get_args(LawfulBasisLiteral))
    assert "CONSENT" not in bases
    assert "LEGITIMATE_INTERESTS" in bases


# ── R-F3153: Art. 22 — no solely automated decision ───────────────────────

def _dec(**kw):
    base = dict(case_id="C1", tenant_id=TENANT,
                decision=DecisionOutcome.APPROVED, decided_by="Dana Reviewer",
                engine_status="READY_FOR_CONTROLLER_REVIEW", engine_blockers=0)
    base.update(kw)
    return record_decision(**base)


@pytest.mark.parametrize("name", ["", "system", "ARIA", "automated", "engine"])
def test_decision_cannot_be_attributed_to_the_system(name):
    with pytest.raises(DecisionError):
        _dec(decided_by=name)


def test_adverse_decision_requires_a_reason():
    """Art. 22(3) safeguards are void if the applicant cannot be told why."""
    with pytest.raises(DecisionError):
        _dec(decision=DecisionOutcome.REJECTED, reason="")


def test_adverse_decision_requires_a_second_pair_of_eyes():
    with pytest.raises(DecisionError):
        _dec(decision=DecisionOutcome.REJECTED, reason="undeclared 14-month gap",
             decided_by="Same Person", assessed_by="Same Person")
    ok = _dec(decision=DecisionOutcome.REJECTED, reason="undeclared 14-month gap",
              decided_by="Dana Reviewer", assessed_by="Sam Screener")
    assert ok.decision is DecisionOutcome.REJECTED


def test_cannot_approve_over_open_blockers_without_recording_why():
    with pytest.raises(DecisionError):
        _dec(decision=DecisionOutcome.APPROVED,
             engine_status="NOT_READY", engine_blockers=2)
    ok = _dec(decision=DecisionOutcome.APPROVED, engine_status="NOT_READY",
              engine_blockers=2,
              blocker_override_reason="referee confirmed by phone, note on file")
    assert ok.engine_blockers == 2


def test_departure_from_the_engine_is_visible():
    """Whether a human ever departs from the recommendation is the question
    that separates real human involvement from a rubber stamp."""
    rubber_stamp = _dec(decision=DecisionOutcome.APPROVED,
                        engine_status="READY_FOR_CONTROLLER_REVIEW")
    assert rubber_stamp.departed_from_engine is False

    departed = _dec(decision=DecisionOutcome.REJECTED,
                    reason="reference could not be independently verified",
                    assessed_by="Sam Screener",
                    engine_status="READY_FOR_CONTROLLER_REVIEW")
    assert departed.departed_from_engine is True


def test_decision_record_states_it_was_not_automated():
    assert _dec().as_dict()["automated_decision"] is False


def test_conditions_required_for_conditional_approval():
    with pytest.raises(DecisionError):
        _dec(decision=DecisionOutcome.APPROVED_WITH_CONDITIONS, conditions=())


# ── R-F3155: Art. 17 — erasure must actually erase ────────────────────────

def test_encrypt_decrypt_roundtrip():
    key = new_case_key()
    blob = encrypt(b"payslip contents", key)
    assert b"payslip contents" not in blob      # not stored in the clear
    assert decrypt(blob, key) == b"payslip contents"


def test_destroying_the_key_makes_content_irrecoverable():
    """The whole basis of crypto-shredding: without the key the retained
    ciphertext is indistinguishable from random data."""
    blob = encrypt(b"criminal record extract", new_case_key())
    with pytest.raises(DecryptionFailed):
        decrypt(blob, new_case_key())           # any other key fails


def test_nonce_is_unique_per_document():
    """GCM nonce reuse under one key is catastrophic."""
    key = new_case_key()
    blobs = {encrypt(b"same bytes", key)[:12] for _ in range(50)}
    assert len(blobs) == 50


def test_disposal_of_encrypted_documents_is_complete_erasure():
    case = VettingCase(
        tenant_id=TENANT, case_id="C", applicant_name="T",
        date_of_birth=date(1990, 1, 1), employment_start=date(2026, 6, 1),
        documents=[UploadedDocument(document_id="d1", doc_type=DocumentType.P60,
                                    evidence_id="ev-1", encrypted=True)])
    plan = plan_disposal(case)
    assert plan.complete is True, (
        "with per-case encryption, destroying the key IS an effective erasure "
        "even though the evidence store never deletes")


def test_disposal_still_discloses_pre_encryption_plaintext_residue():
    case = VettingCase(
        tenant_id=TENANT, case_id="C", applicant_name="T",
        date_of_birth=date(1990, 1, 1), employment_start=date(2026, 6, 1),
        documents=[UploadedDocument(document_id="d1", doc_type=DocumentType.P60,
                                    evidence_id="ev-old", encrypted=False)])
    plan = plan_disposal(case)
    assert plan.complete is False
    assert "BEFORE per-case encryption" in plan.residual_reason


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


def _create(client, case_id="LEG-1"):
    return client.post("/api/aria/vetting/cases", json={
        "case_id": case_id, "applicant_name": "Test Applicant",
        "date_of_birth": "1990-01-01", "employment_start": "2026-06-01",
        "pack_id": "uk_bs7858",
    }, params={"user_id": TENANT}, headers=AUTH)


def test_rf3152_outcome_can_be_set_so_retention_can_start(client):
    """The dead-feature fix: without a way to set the outcome, every case
    stayed PENDING and Art. 5(1)(e) could never be discharged."""
    assert _create(client, "LEG-R").status_code == 200
    r = client.patch("/api/aria/vetting/case/LEG-R",
                     json={"outcome": "UNSUCCESSFUL", "outcome_date": "2026-01-10"},
                     params={"user_id": TENANT}, headers=AUTH)
    assert r.status_code == 200, r.text
    body = client.get("/api/aria/vetting/retention",
                      params={"user_id": TENANT, "as_of": "2026-07-26"},
                      headers=AUTH).json()
    row = next(c for c in body["cases"] if c["case_id"] == "LEG-R")
    assert row["due_date"] == "2027-01-10"


def test_rf3152_invalid_outcome_is_422_not_500(client):
    assert _create(client, "LEG-B").status_code == 200
    r = client.patch("/api/aria/vetting/case/LEG-B", json={"outcome": "BANANA"},
                     params={"user_id": TENANT}, headers=AUTH)
    assert r.status_code == 422


def test_rf3153_decision_route_refuses_a_system_attribution(client):
    assert _create(client, "LEG-D").status_code == 200
    r = client.post("/api/aria/vetting/case/LEG-D/decision",
                    json={"decision": "REJECTED", "decided_by": "system",
                          "reason": "gaps"},
                    params={"user_id": TENANT}, headers=AUTH)
    assert r.status_code == 422
    assert r.json()["detail"]["code"] == "decision_refused"


def test_rf3153_recorded_decision_lands_on_the_case(client):
    assert _create(client, "LEG-D2").status_code == 200
    r = client.post("/api/aria/vetting/case/LEG-D2/decision",
                    json={"decision": "REJECTED", "decided_by": "Dana Reviewer",
                          "assessed_by": "Sam Screener",
                          "reason": "unverified 90-day gap"},
                    params={"user_id": TENANT}, headers=AUTH)
    assert r.status_code == 200, r.text
    assert r.json()["automated_decision"] is False
    case = client.get("/api/aria/vetting/case/LEG-D2",
                      params={"user_id": TENANT}, headers=AUTH).json()
    assert len(case["decisions"]) == 1
    assert case["decisions"][0]["decided_by"] == "Dana Reviewer"


def test_rf3154_subject_access_export_states_no_automated_decision(client):
    assert _create(client, "LEG-S").status_code == 200
    r = client.get("/api/aria/vetting/case/LEG-S/subject-access",
                   params={"user_id": TENANT}, headers=AUTH)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["automated_decision_making"] is False
    assert body["processing"]["lawful_basis"] == "LEGITIMATE_INTERESTS"
    assert body["retention"]["reason"]
    assert any("Art. 17" in right for right in body["your_rights"])


def test_rf3154_dispute_is_appended_not_applied(client):
    assert _create(client, "LEG-X").status_code == 200
    r = client.post("/api/aria/vetting/case/LEG-X/dispute",
                    json={"raised_by": "Applicant",
                          "disputed_finding": "GAP_UNDECLARED",
                          "statement": "I was a full-time carer in that period."},
                    params={"user_id": TENANT}, headers=AUTH)
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "OPEN"
    export = client.get("/api/aria/vetting/case/LEG-X/subject-access",
                        params={"user_id": TENANT}, headers=AUTH).json()
    assert len(export["disputes"]) == 1


def test_rf3155_uploaded_documents_are_encrypted_at_rest(client, tmp_path):
    assert _create(client, "LEG-E").status_code == 200
    secret = b"NATIONAL INSURANCE NUMBER QQ123456C"
    with patch("aria_service.vetting.documents.extract_document",
               new=AsyncMock(return_value={"available": True, "data": {
                   "doc_type": "P60", "confidence": 0.95}})):
        r = client.post("/api/aria/vetting/case/LEG-E/documents",
                        json={"filename": "p60.txt",
                              "content_base64": base64.b64encode(secret).decode()},
                        params={"user_id": TENANT}, headers=AUTH)
    assert r.status_code == 200, r.text

    # The retained artifact must not contain the plaintext anywhere on disk.
    art_dir = tmp_path / "art"
    blobs = [p.read_bytes() for p in art_dir.rglob("*") if p.is_file()]
    assert blobs, "no artifact was retained"
    for blob in blobs:
        assert secret not in blob, (
            "an identity document was retained in PLAINTEXT — destroying the "
            "case key would not then erase it")


def test_rf3155_disposing_an_encrypted_case_reports_complete_erasure(client):
    assert _create(client, "LEG-Z").status_code == 200
    with patch("aria_service.vetting.documents.extract_document",
               new=AsyncMock(return_value={"available": True, "data": {
                   "doc_type": "P60", "confidence": 0.95}})):
        client.post("/api/aria/vetting/case/LEG-Z/documents",
                    json={"filename": "p60.txt",
                          "content_base64": base64.b64encode(b"x").decode()},
                    params={"user_id": TENANT}, headers=AUTH)
    r = client.post("/api/aria/vetting/case/LEG-Z/dispose",
                    params={"user_id": TENANT}, headers=AUTH)
    assert r.status_code == 200, r.text
    assert r.json()["erasure_complete"] is True


def test_rf3155_disposal_destroys_the_case_key(client):
    from aria_service.vetting.store import get_case_store
    assert _create(client, "LEG-K").status_code == 200
    with patch("aria_service.vetting.documents.extract_document",
               new=AsyncMock(return_value={"available": False, "reason": "x"})):
        client.post("/api/aria/vetting/case/LEG-K/documents",
                    json={"filename": "p.txt",
                          "content_base64": base64.b64encode(b"y").decode()},
                    params={"user_id": TENANT}, headers=AUTH)
    store = get_case_store()
    assert store.get_case_key(TENANT, "LEG-K") is not None
    client.post("/api/aria/vetting/case/LEG-K/dispose",
                params={"user_id": TENANT}, headers=AUTH)
    assert store.get_case_key(TENANT, "LEG-K") is None, (
        "the case key survived disposal — the ciphertext is still readable")
