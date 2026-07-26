"""R-F3148 — retention schedule and audited disposal.

Two properties carry the weight:
  1. A clock that has not started produces NO due date and a stated reason —
     never a guessed one. Anchoring the post-employment period to anything
     other than the end of employment would schedule a live personnel file for
     deletion years early.
  2. Disposal reports what SURVIVES. The evidence store is append-only, so a
     case with documents cannot be fully erased here, and the response must
     say so rather than claim a clean erasure.
"""

from __future__ import annotations

from datetime import date

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from aria_service.vetting.models import (
    UploadedDocument, DocumentType, VettingCase,
)
from aria_service.vetting.packs.base import registry
from aria_service.vetting.retention import (
    CaseOutcome, _add_months, plan_disposal, retention_due_date,
)

AS_OF = date(2026, 7, 26)
TENANT = "tenant-a"
TOKEN = "vetting-retention-token"
AUTH = {"Authorization": f"Bearer {TOKEN}"}

UK_PACK = registry.get_exact(
    "uk_bs7858", "1.1.0",
    next(p for p in [registry.latest_usable("uk_bs7858")]).content_hash(),
)


def _case(**kw) -> VettingCase:
    base = dict(
        tenant_id=TENANT, case_id="R1", applicant_name="T",
        date_of_birth=date(1990, 1, 1), employment_start=date(2026, 6, 1),
    )
    base.update(kw)
    return VettingCase(**base)


# ── month arithmetic ──────────────────────────────────────────────────────

def test_month_addition_never_rolls_into_the_next_month():
    # 31 Jan + 1 month must be 28 Feb, not 3 Mar. A disposal date that slid
    # later is a retention breach, small but real.
    assert _add_months(date(2026, 1, 31), 1) == date(2026, 2, 28)
    assert _add_months(date(2024, 1, 31), 1) == date(2024, 2, 29)   # leap year
    assert _add_months(date(2026, 1, 15), 12) == date(2027, 1, 15)


# ── clocks that have not started ──────────────────────────────────────────

def test_pending_case_has_no_due_date():
    v = retention_due_date(_case(outcome=CaseOutcome.PENDING.value), UK_PACK, AS_OF)
    assert v.due_date is None
    assert v.overdue is False
    assert "no retention clock" in v.reason


def test_employed_with_ongoing_employment_has_no_due_date():
    """THE trap: the post-employment clock starts when employment ENDS."""
    v = retention_due_date(
        _case(outcome=CaseOutcome.EMPLOYED.value, employment_end=None),
        UK_PACK, AS_OF)
    assert v.due_date is None, (
        "an ongoing employment file must have NO disposal date — deriving one "
        "from employment_start would delete a live personnel record early"
    )
    assert "employment ends" in v.reason


def test_outcome_without_a_date_does_not_start_the_clock():
    v = retention_due_date(
        _case(outcome=CaseOutcome.UNSUCCESSFUL.value, outcome_date=None),
        UK_PACK, AS_OF)
    assert v.due_date is None
    assert "cannot start" in v.reason


# ── clocks that have started ──────────────────────────────────────────────

def test_unsuccessful_file_uses_the_12_month_period():
    v = retention_due_date(
        _case(outcome=CaseOutcome.UNSUCCESSFUL.value,
              outcome_date=date(2026, 1, 10)),
        UK_PACK, AS_OF)
    assert v.due_date == date(2027, 1, 10)
    assert v.overdue is False


def test_unsuccessful_file_is_overdue_once_the_period_has_passed():
    v = retention_due_date(
        _case(outcome=CaseOutcome.UNSUCCESSFUL.value,
              outcome_date=date(2025, 1, 10)),
        UK_PACK, AS_OF)
    assert v.due_date == date(2026, 1, 10)
    assert v.overdue is True


def test_post_employment_file_uses_the_7_year_period():
    v = retention_due_date(
        _case(outcome=CaseOutcome.EMPLOYED.value,
              employment_end=date(2026, 3, 31)),
        UK_PACK, AS_OF)
    assert v.due_date == date(2033, 3, 31)
    assert v.overdue is False


def test_withdrawn_uses_the_short_clock_like_unsuccessful():
    v = retention_due_date(
        _case(outcome=CaseOutcome.WITHDRAWN.value,
              outcome_date=date(2026, 1, 10)),
        UK_PACK, AS_OF)
    assert v.due_date == date(2027, 1, 10)


# ── disposal honesty ──────────────────────────────────────────────────────

def test_disposal_of_a_case_with_no_documents_is_complete():
    plan = plan_disposal(_case())
    assert plan.complete is True
    assert plan.retained_evidence_ids == ()


def test_disposal_discloses_evidence_that_survives():
    case = _case(documents=[
        UploadedDocument(document_id="d1", doc_type=DocumentType.P60,
                         evidence_id="ev-1"),
        UploadedDocument(document_id="d2", doc_type=DocumentType.PAYSLIP,
                         evidence_id="ev-2"),
    ])
    plan = plan_disposal(case)
    assert plan.complete is False, (
        "the evidence store is append-only, so a case with documents cannot be "
        "fully erased — claiming otherwise overstates the erasure"
    )
    assert set(plan.retained_evidence_ids) == {"ev-1", "ev-2"}
    assert "append-only" in plan.residual_reason
    assert plan.as_dict()["erasure_complete"] is False


# ── capability: the real routes ───────────────────────────────────────────

@pytest.fixture()
def client(tmp_path, monkeypatch):
    from aria_service.routes.vetting import router as vetting_router
    from aria_service.vetting import store as store_module

    monkeypatch.setenv("ARIA_API_TOKEN", TOKEN)
    monkeypatch.setenv("ARIA_VETTING_DB", str(tmp_path / "cases.db"))
    monkeypatch.setattr(store_module, "_STORE", None, raising=False)
    app = FastAPI()
    app.include_router(vetting_router)
    return TestClient(app)


def _create(client, case_id="RET-1", tenant=TENANT):
    return client.post("/api/aria/vetting/cases", json={
        "case_id": case_id, "applicant_name": "T",
        "date_of_birth": "1990-01-01", "employment_start": "2026-06-01",
        "pack_id": "uk_bs7858",
    }, params={"user_id": tenant}, headers=AUTH)


def test_rf3148_retention_endpoint_is_tenant_scoped(client):
    assert _create(client, "RET-1", TENANT).status_code == 200
    mine = client.get("/api/aria/vetting/retention",
                      params={"user_id": TENANT}, headers=AUTH).json()
    assert [c["case_id"] for c in mine["cases"]] == ["RET-1"]
    theirs = client.get("/api/aria/vetting/retention",
                        params={"user_id": "tenant-b"}, headers=AUTH).json()
    assert theirs["cases"] == []


def test_rf3148_pending_case_reports_no_due_date_over_http(client):
    assert _create(client, "RET-2").status_code == 200
    body = client.get("/api/aria/vetting/retention",
                      params={"user_id": TENANT, "as_of": "2026-07-26"},
                      headers=AUTH).json()
    row = next(c for c in body["cases"] if c["case_id"] == "RET-2")
    assert row["due_date"] is None
    assert row["overdue"] is False
    assert body["overdue_count"] == 0


def test_rf3148_dispose_removes_the_case_and_reports_erasure(client):
    assert _create(client, "RET-3").status_code == 200
    r = client.post("/api/aria/vetting/case/RET-3/dispose",
                    params={"user_id": TENANT}, headers=AUTH)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["case_record_removed"] is True
    assert body["erasure_complete"] is True     # no documents on this case
    # The case is really gone.
    assert client.get("/api/aria/vetting/case/RET-3",
                      params={"user_id": TENANT}, headers=AUTH).status_code == 404


def test_rf3148_dispose_is_tenant_scoped(client):
    assert _create(client, "RET-4", TENANT).status_code == 200
    r = client.post("/api/aria/vetting/case/RET-4/dispose",
                    params={"user_id": "tenant-b"}, headers=AUTH)
    assert r.status_code == 404
    # Still there for its real owner.
    assert client.get("/api/aria/vetting/case/RET-4",
                      params={"user_id": TENANT}, headers=AUTH).status_code == 200
