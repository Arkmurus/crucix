"""R-F3274 — documents now reach the career timeline instead of stopping short.

R-F3265 made the module able to READ a PDF, an image, a DOCX, an email and its
attachments. Nothing consumed the result. A payslip covering 2022-2023 landed
on the file, was extracted, carried covers_from/covers_to — and the timeline
still said "0 verified · 0 declared · 61 uncovered", because the only way a
document ever reached a period was the officer passing attach_to_entry_id by
hand. Reading a document and then not using what it says is barely better than
not reading it.

Two behaviours, and the boundary between them is the whole design:

  1. A document that EVIDENCES a period attaches itself to the periods it
     overlaps and lifts them UNVERIFIED -> EVIDENCE_RECEIVED. Not VERIFIED.
     Verification is a referee's or a human's act; a payslip arriving is
     evidence received, and calling that verified is the false clean this
     module exists to prevent.

  2. An APPLICATION FORM declares a history. Periods read off it are created
     as UNVERIFIED — which is precisely what coverage_map documents that state
     to mean: "declared by the applicant, not yet verified. Present on the
     file, NOT yet evidence."

The single most dangerous thing here, and the reason for the doc-type
allow-list: a PASSPORT has covers_from/covers_to too — its issue and expiry.
A naive overlap rule would let one passport "cover" ten years of a career
timeline and turn a blank grid green. Only documents that evidence an
ENGAGEMENT may touch the timeline.
"""

from __future__ import annotations

from datetime import date

import pytest

from aria_service.vetting.models import (
    CareerEntry,
    CareerEntryType,
    DocumentType,
    UploadedDocument,
    VerificationState,
    VettingCase,
)
from aria_service.vetting.timeline import (
    PERIOD_EVIDENCING_TYPES,
    apply_document_to_timeline,
)


def _case(career=None) -> VettingCase:
    return VettingCase(
        tenant_id="t", case_id="C1", applicant_name="Maria Gomes",
        date_of_birth=date(1990, 1, 1), employment_start=date(2026, 1, 1),
        career=career or [],
    )


def _entry(entry_id="e1", start=date(2022, 1, 1), end=date(2023, 12, 31),
           state=VerificationState.UNVERIFIED, org="Acme") -> CareerEntry:
    return CareerEntry(entry_id=entry_id, entry_type=CareerEntryType.EMPLOYMENT,
                       start=start, end=end, organisation=org, state=state)


def _doc(doc_type=DocumentType.PAYSLIP, frm=date(2022, 6, 1),
         to=date(2022, 6, 30), confidence=0.95, flags=None) -> UploadedDocument:
    return UploadedDocument(
        document_id="d1", doc_type=doc_type, evidence_id="ev1",
        covers_from=frm, covers_to=to, extraction_confidence=confidence,
        authenticity_flags=list(flags or []))


# ── 1. a document reaches the period it evidences ────────────────────────

def test_a_payslip_attaches_itself_to_the_period_it_covers():
    """THE regression: the document had to be hand-attached or it was inert."""
    case = _case([_entry()])
    career, summary = apply_document_to_timeline(case, _doc(), {})

    assert career[0].supporting_documents == ["d1"]
    assert summary["linked_entry_ids"] == ["e1"]


def test_it_lifts_the_period_to_evidence_received_never_to_verified():
    """A document arriving is evidence RECEIVED. Verification is a human's or
    a referee's act, and conflating the two is the false clean."""
    case = _case([_entry()])
    career, _ = apply_document_to_timeline(case, _doc(), {})
    assert career[0].state is VerificationState.EVIDENCE_RECEIVED
    assert career[0].state is not VerificationState.VERIFIED


def test_it_never_downgrades_a_period_that_is_already_verified():
    case = _case([_entry(state=VerificationState.VERIFIED)])
    career, _ = apply_document_to_timeline(case, _doc(), {})
    assert career[0].state is VerificationState.VERIFIED
    assert "d1" in career[0].supporting_documents, (
        "the document should still be filed against the period")


def test_it_does_not_quietly_revive_a_failed_verification():
    """A verification that FAILED is a finding. An uploaded payslip must not
    silently lift it back to a neutral-looking state."""
    case = _case([_entry(state=VerificationState.VERIFICATION_FAILED)])
    career, _ = apply_document_to_timeline(case, _doc(), {})
    assert career[0].state is VerificationState.VERIFICATION_FAILED


def test_a_period_covered_by_a_statutory_declaration_is_left_alone():
    case = _case([_entry(state=VerificationState.COVERED_BY_STAT_DEC)])
    career, _ = apply_document_to_timeline(case, _doc(), {})
    assert career[0].state is VerificationState.COVERED_BY_STAT_DEC


def test_it_touches_only_the_periods_the_document_actually_overlaps():
    case = _case([
        _entry("e1", date(2020, 1, 1), date(2021, 1, 1)),      # before
        _entry("e2", date(2022, 1, 1), date(2023, 1, 1)),      # overlaps
        _entry("e3", date(2024, 1, 1), date(2025, 1, 1)),      # after
    ])
    career, summary = apply_document_to_timeline(case, _doc(), {})
    assert summary["linked_entry_ids"] == ["e2"]
    assert career[0].state is VerificationState.UNVERIFIED
    assert career[2].state is VerificationState.UNVERIFIED


def test_an_open_ended_period_still_matches():
    """`end=None` means "still there". A current payslip must reach it."""
    case = _case([_entry(start=date(2022, 1, 1), end=None)])
    career, _ = apply_document_to_timeline(
        case, _doc(frm=date(2026, 1, 1), to=date(2026, 1, 31)), {})
    assert career[0].state is VerificationState.EVIDENCE_RECEIVED


def test_a_document_with_no_period_evidences_no_period():
    case = _case([_entry()])
    career, summary = apply_document_to_timeline(
        case, _doc(frm=None, to=None), {})
    assert summary["linked_entry_ids"] == []
    assert career[0].state is VerificationState.UNVERIFIED


def test_relinking_the_same_document_does_not_duplicate_it():
    case = _case([_entry()])
    career, _ = apply_document_to_timeline(case, _doc(), {})
    case2 = _case(career)
    career2, _ = apply_document_to_timeline(case2, _doc(), {})
    assert career2[0].supporting_documents == ["d1"]


# ── THE dangerous one: identity documents must not cover a career ────────

def test_a_passport_cannot_cover_a_career_period():
    """A passport's covers_from/covers_to are its issue and expiry. Letting one
    overlap-match would turn a blank timeline green off a single ID document —
    the exact false clean this module exists to prevent."""
    case = _case([_entry()])
    career, summary = apply_document_to_timeline(
        case, _doc(DocumentType.PASSPORT, date(2018, 1, 1), date(2028, 1, 1)), {})
    assert summary["linked_entry_ids"] == []
    assert career[0].state is VerificationState.UNVERIFIED
    assert summary["reason"], "the refusal must be stated, not silent"


@pytest.mark.parametrize("doc_type", [
    DocumentType.PASSPORT, DocumentType.DRIVING_LICENCE,
    DocumentType.BIRTH_CERTIFICATE, DocumentType.RESIDENCE_PERMIT,
    DocumentType.PROOF_OF_ADDRESS, DocumentType.SIA_LICENCE,
    DocumentType.DISCLOSURE_CERTIFICATE, DocumentType.NPCC_POLICE_LETTER,
    DocumentType.OTHER,
])
def test_no_identity_or_status_document_evidences_an_engagement(doc_type):
    assert doc_type not in PERIOD_EVIDENCING_TYPES


@pytest.mark.parametrize("doc_type", [
    DocumentType.PAYSLIP, DocumentType.P45, DocumentType.P60,
    DocumentType.EMPLOYMENT_CONTRACT, DocumentType.EMPLOYER_REFERENCE,
    DocumentType.EDUCATION_REFERENCE, DocumentType.ACCOUNTANT_REFERENCE,
    DocumentType.REDUNDANCY_LETTER, DocumentType.DWP_CONFIRMATION,
    DocumentType.HMRC_DOCUMENT,
])
def test_the_documents_that_do_evidence_an_engagement_are_allowed(doc_type):
    assert doc_type in PERIOD_EVIDENCING_TYPES


# ── the confidence / authenticity boundary ───────────────────────────────

def test_a_low_confidence_document_does_not_lift_anything():
    """Below the floor the classification is a suggestion for a human. Acting
    on it would let a misread document silently evidence a period."""
    case = _case([_entry()])
    career, summary = apply_document_to_timeline(
        case, _doc(confidence=0.4), {})
    assert career[0].state is VerificationState.UNVERIFIED
    assert summary["linked_entry_ids"] == []
    assert summary["reason"]


def test_a_document_with_an_authenticity_concern_does_not_lift_anything():
    case = _case([_entry()])
    career, summary = apply_document_to_timeline(
        case, _doc(flags=["authenticity:dates contradict each other"]), {})
    assert career[0].state is VerificationState.UNVERIFIED
    assert summary["linked_entry_ids"] == []


# ── 2. the application form populates a declared history ─────────────────

_APPLICATION = {"available": True, "data": {
    "doc_type": "APPLICATION_FORM", "confidence": 0.95,
    "declared_periods": [
        {"entry_type": "EMPLOYMENT", "start": "2021-03-01", "end": "2023-06-30",
         "organisation": "Acme Security Ltd"},
        {"entry_type": "UNEMPLOYMENT", "start": "2023-07-01", "end": "2023-09-30",
         "organisation": None},
    ]}}


def test_an_application_form_populates_the_declared_timeline():
    """The live complaint: "0 verified · 0 declared · 61 uncovered" on a file
    whose application form declared a full history."""
    case = _case([])
    doc = _doc(DocumentType.APPLICATION_FORM, None, None)
    career, summary = apply_document_to_timeline(case, doc, _APPLICATION)

    assert len(career) == 2
    assert summary["created_entry_ids"] and len(summary["created_entry_ids"]) == 2
    assert career[0].organisation == "Acme Security Ltd"
    assert career[0].start == date(2021, 3, 1)
    assert career[1].entry_type is CareerEntryType.UNEMPLOYMENT


def test_declared_periods_are_unverified_never_evidence():
    """A form is the applicant SAYING something. Nothing about it is evidence
    of the thing said."""
    case = _case([])
    career, _ = apply_document_to_timeline(
        case, _doc(DocumentType.APPLICATION_FORM, None, None), _APPLICATION)
    assert all(e.state is VerificationState.UNVERIFIED for e in career)


def test_a_declared_period_records_where_it_came_from():
    """An officer must be able to tell a period a human typed from a period a
    model read off a scan, and open the document it was read from."""
    case = _case([])
    career, _ = apply_document_to_timeline(
        case, _doc(DocumentType.APPLICATION_FORM, None, None), _APPLICATION)
    assert career[0].source == "EXTRACTED_FROM_DOCUMENT"
    assert career[0].source_document_id == "d1"
    assert "d1" in career[0].supporting_documents


def test_it_does_not_duplicate_a_period_the_officer_already_entered():
    existing = _entry("hand-1", date(2021, 3, 1), date(2023, 6, 30),
                      org="Acme Security Ltd")
    case = _case([existing])
    career, summary = apply_document_to_timeline(
        case, _doc(DocumentType.APPLICATION_FORM, None, None), _APPLICATION)
    assert len(career) == 2, "the overlapping declared period was duplicated"
    assert career[0].entry_id == "hand-1"
    assert career[0].source != "EXTRACTED_FROM_DOCUMENT", (
        "an officer-entered period must not be relabelled as extracted")


def test_a_low_confidence_application_form_creates_nothing():
    case = _case([])
    low = {"available": True, "data": {**_APPLICATION["data"], "confidence": 0.3}}
    career, summary = apply_document_to_timeline(
        case, _doc(DocumentType.APPLICATION_FORM, None, None, confidence=0.3), low)
    assert career == []
    assert summary["reason"]


def test_an_unreadable_application_form_creates_nothing():
    """A failed read is a disclosed gap. Inventing a history from a document
    we could not decode is the fabrication the whole module refuses."""
    case = _case([])
    career, summary = apply_document_to_timeline(
        case, _doc(DocumentType.APPLICATION_FORM, None, None),
        {"available": False, "reason": "no extractable text in document"})
    assert career == []


def test_a_reversed_or_impossible_declared_period_is_dropped_not_normalised():
    case = _case([])
    bad = {"available": True, "data": {
        "doc_type": "APPLICATION_FORM", "confidence": 0.95,
        "declared_periods": [
            {"entry_type": "EMPLOYMENT", "start": "2023-01-01", "end": "2021-01-01"},
            {"entry_type": "EMPLOYMENT", "start": "not-a-date", "end": None},
            {"entry_type": "NONSENSE", "start": "2021-01-01", "end": "2022-01-01"},
        ]}}
    career, summary = apply_document_to_timeline(
        case, _doc(DocumentType.APPLICATION_FORM, None, None), bad)
    assert career == []
    assert summary["rejected"] == 3


def test_only_an_application_form_may_declare_a_history():
    """A payslip that happens to mention other dates must not spawn periods."""
    case = _case([])
    career, _ = apply_document_to_timeline(
        case, _doc(DocumentType.PAYSLIP), _APPLICATION)
    assert career == [], "a non-application document created declared periods"


def test_the_function_is_pure_and_does_not_mutate_the_case():
    entry = _entry()
    case = _case([entry])
    apply_document_to_timeline(case, _doc(), {})
    assert entry.state is VerificationState.UNVERIFIED
    assert entry.supporting_documents == []
    assert len(case.career) == 1


# ── the capability test: the officer's real upload path (§3c) ────────────
#
# The unit tests above drive timeline.py directly. §3c is explicit that a
# helper test is not a capability test — R-F3216 shipped a fix that was never
# wired to its call site and every unit test stayed green. So this drives
# POST /case/{id}/documents, the endpoint the upload button hits, with the
# extraction stubbed at the LLM boundary (never the module under test).

import base64

TOKEN = "test-token"
AUTH = {"Authorization": f"Bearer {TOKEN}"}
TENANT = "tenant-a"


@pytest.fixture()
def client(tmp_path, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from aria_service.routes import vetting as routes_vetting
    from aria_service.vetting import store as store_module

    monkeypatch.setenv("ARIA_API_TOKEN", TOKEN)
    monkeypatch.setenv("ARIA_VETTING_DB", str(tmp_path / "routes_timeline.db"))
    monkeypatch.setenv("ARIA_VETTING_EVIDENCE_DIR", str(tmp_path / "ev"))
    monkeypatch.setattr(store_module, "_STORE", None, raising=False)

    app = FastAPI()
    app.include_router(routes_vetting.router)
    return TestClient(app)


def _stub_extraction(monkeypatch, payload: dict) -> None:
    """Stub at the LLM boundary only — everything below it is the real path."""
    import aria_service.vetting.documents as docs

    async def _fake(**kwargs):
        return payload

    monkeypatch.setattr(docs, "extract_document", _fake)
    # The route imports the symbol inside the function body, so patching the
    # module attribute is what the route will actually resolve.


def _upload(client, case_id, payload, filename="payslip.pdf",
            declared="PAYSLIP", attach=None):
    body = {"filename": filename, "declared_doc_type": declared,
            "content_base64": base64.b64encode(b"some document bytes").decode()}
    if attach:
        body["attach_to_entry_id"] = attach
    return client.post(f"/api/aria/vetting/case/{case_id}/documents",
                       json=body, params={"user_id": TENANT}, headers=AUTH)


def _make_case(client, career=None):
    payload = {"case_id": "T1", "applicant_name": "Maria Gomes",
               "date_of_birth": "1990-01-01", "employment_start": "2026-01-01",
               "pack_id": "uk_bs7858"}
    if career:
        payload["career"] = career
    r = client.post("/api/aria/vetting/cases", json=payload,
                    params={"user_id": TENANT}, headers=AUTH)
    assert r.status_code == 200, r.text


def test_uploading_a_payslip_lifts_the_period_over_http(client, monkeypatch):
    """THE capability. Before this, the same upload left the period untouched."""
    _make_case(client, career=[{
        "entry_id": "e1", "entry_type": "EMPLOYMENT",
        "start": "2022-01-01", "end": "2023-12-31", "organisation": "Acme"}])
    _stub_extraction(monkeypatch, {"available": True, "data": {
        "doc_type": "PAYSLIP", "confidence": 0.95,
        "covers_from": "2022-06-01", "covers_to": "2022-06-30"}})

    r = _upload(client, "T1", None)
    assert r.status_code == 200, r.text
    assert r.json()["timeline"]["linked_entry_ids"] == ["e1"]

    case = client.get("/api/aria/vetting/case/T1",
                      params={"user_id": TENANT}, headers=AUTH).json()
    entry = case["career"][0]
    assert entry["state"] == "EVIDENCE_RECEIVED"
    assert entry["supporting_documents"] == [r.json()["document_id"]]


def test_uploading_an_application_form_populates_the_timeline_over_http(
        client, monkeypatch):
    """The live complaint: "0 verified · 0 declared · 61 uncovered"."""
    _make_case(client)
    _stub_extraction(monkeypatch, {"available": True, "data": {
        "doc_type": "APPLICATION_FORM", "confidence": 0.95,
        "declared_periods": [
            {"entry_type": "EMPLOYMENT", "start": "2021-03-01",
             "end": "2023-06-30", "organisation": "Acme Security Ltd"},
            {"entry_type": "UNEMPLOYMENT", "start": "2023-07-01",
             "end": "2023-09-30"}]}})

    r = _upload(client, "T1", None, filename="application.pdf",
                declared="APPLICATION_FORM")
    assert r.status_code == 200, r.text
    assert len(r.json()["timeline"]["created_entry_ids"]) == 2

    case = client.get("/api/aria/vetting/case/T1",
                      params={"user_id": TENANT}, headers=AUTH).json()
    assert len(case["career"]) == 2
    assert all(e["state"] == "UNVERIFIED" for e in case["career"]), (
        "a declared period was filed as evidence")
    assert all(e["source"] == "EXTRACTED_FROM_DOCUMENT" for e in case["career"])


def test_a_passport_upload_leaves_the_timeline_alone_over_http(client, monkeypatch):
    """The false clean this guards: a passport spans issue to expiry, and
    would otherwise 'cover' the whole screening period."""
    _make_case(client, career=[{
        "entry_id": "e1", "entry_type": "EMPLOYMENT",
        "start": "2022-01-01", "end": "2023-12-31", "organisation": "Acme"}])
    _stub_extraction(monkeypatch, {"available": True, "data": {
        "doc_type": "PASSPORT", "confidence": 0.98,
        "covers_from": "2018-01-01", "covers_to": "2028-01-01"}})

    r = _upload(client, "T1", None, filename="passport.jpg", declared="PASSPORT")
    assert r.status_code == 200, r.text
    assert r.json()["timeline"]["linked_entry_ids"] == []
    assert r.json()["timeline"]["reason"]

    case = client.get("/api/aria/vetting/case/T1",
                      params={"user_id": TENANT}, headers=AUTH).json()
    assert case["career"][0]["state"] == "UNVERIFIED"


def test_an_unreadable_document_is_still_stored_and_still_disclosed(
        client, monkeypatch):
    """A failed read must remain a disclosed gap, and must not silently change
    the timeline in either direction."""
    _make_case(client, career=[{
        "entry_id": "e1", "entry_type": "EMPLOYMENT",
        "start": "2022-01-01", "end": "2023-12-31"}])
    _stub_extraction(monkeypatch, {"available": False,
                                   "reason": "no extractable text in document"})

    r = _upload(client, "T1", None)
    assert r.status_code == 200, r.text
    assert r.json()["needs_human_review"] is True
    assert r.json()["timeline"]["linked_entry_ids"] == []

    case = client.get("/api/aria/vetting/case/T1",
                      params={"user_id": TENANT}, headers=AUTH).json()
    assert case["career"][0]["state"] == "UNVERIFIED"
    assert len(case["documents"]) == 1, "the document must still be on the file"


def test_the_manual_attachment_still_works_and_does_not_double_file(
        client, monkeypatch):
    """attach_to_entry_id is a human naming the period. It must survive the
    automatic pass, and must not file the same document twice."""
    _make_case(client, career=[{
        "entry_id": "e1", "entry_type": "EMPLOYMENT",
        "start": "2022-01-01", "end": "2023-12-31"}])
    _stub_extraction(monkeypatch, {"available": True, "data": {
        "doc_type": "PAYSLIP", "confidence": 0.95,
        "covers_from": "2022-06-01", "covers_to": "2022-06-30"}})

    r = _upload(client, "T1", None, attach="e1")
    assert r.status_code == 200, r.text
    case = client.get("/api/aria/vetting/case/T1",
                      params={"user_id": TENANT}, headers=AUTH).json()
    assert case["career"][0]["supporting_documents"] == [r.json()["document_id"]]
