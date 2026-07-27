"""R-F3207..R-F3216 — the intake document set, stages, and reading a stored
document back.

The defect these exist to pin: the engine had NO opinion on whether the core
documents were on the file. `accepted_evidence` is indexed by career period, so
nothing ever asked for an identity document, a criminal-record disclosure, or
two proofs of address — and a case holding none of them assessed as complete.

Every HTTP test drives the real router through the real auth dependency, and
the document tests go through the actual upload → list → open path an officer
uses, not the service layer underneath it (§3c).
"""

from __future__ import annotations

import base64
from datetime import date

import pytest

from aria_service.vetting.models import (
    CareerEntry,
    CareerEntryType,
    DocumentRequirement,
    DocumentType,
    RequirementWaiver,
    ScreeningInputs,
    UploadedDocument,
    VettingCase,
)
from aria_service.vetting.packs import builtin as B  # noqa: F401 — registers packs
from aria_service.vetting.packs.base import registry
from aria_service.vetting.requirements import (
    RequirementState,
    resolve_requirements,
    summarise,
)
from aria_service.vetting.rules import assess

AS_OF = date(2026, 7, 27)
TENANT = "tenant-a"
OTHER_TENANT = "tenant-b"
TOKEN = "vetting-req-test-token"
AUTH = {"Authorization": f"Bearer {TOKEN}"}


def pack():
    return registry.latest_usable("uk_bs7858")


def _case(**overrides) -> VettingCase:
    base = {
        "tenant_id": TENANT, "case_id": "VET-REQ", "applicant_name": "Jane Doe",
        "date_of_birth": date(1990, 1, 1), "employment_start": date(2026, 7, 1),
    }
    return VettingCase(**(base | overrides))


def _doc(doc_type: DocumentType, digest: str, **overrides) -> UploadedDocument:
    """An ACCEPTED-shaped document unless a test says otherwise."""
    fields = {
        "document_id": f"d-{digest}", "doc_type": doc_type,
        "evidence_id": f"e-{digest}", "extraction_confidence": 0.95,
        "plaintext_sha256": digest, "sighting": "ORIGINAL_SEEN",
        "examined_by": "O. Fficer", "examined_at": date(2026, 7, 2),
    }
    return UploadedDocument(**(fields | overrides))


# ── R-F3207: the engine now asks for the core documents ──────────────────

def test_empty_case_is_not_complete_because_documents_are_missing():
    """THE regression. A file with nothing on it must not read as satisfied.

    Before R-F3207 the intake set was invisible to the engine, so the only
    thing standing between an empty file and a clean status was the checklist
    of booleans an officer ticks themselves.
    """
    result = assess(_case(), pack(), AS_OF)
    codes = {f["code"] for f in result["findings"]}
    assert "DOCUMENT_OUTSTANDING" in codes
    summary = result["requirements_summary"]
    assert summary["accepted"] == 0
    assert summary["mandatory_outstanding"] == summary["mandatory"] > 0
    assert result["status"] != "READY_FOR_CONTROLLER_REVIEW"

    by_key = {r["key"]: r for r in result["requirements"]}
    for expected in ("application_form", "cv", "identity_document",
                     "proof_of_address", "criminality_certificate",
                     "interview_record", "right_to_work",
                     "signed_authorisation"):
        assert by_key[expected]["state"] == RequirementState.OUTSTANDING


def test_alternatives_satisfy_a_requirement():
    """A driving licence is proof of identity. The requirement asks 'any
    acceptable proof', not 'a passport'."""
    case = _case(documents=[_doc(DocumentType.DRIVING_LICENCE, "a" * 64)])
    identity = next(r for r in resolve_requirements(case, pack())
                    if r.requirement.key == "identity_document")
    assert identity.state == RequirementState.ACCEPTED


def test_the_same_file_uploaded_twice_counts_once():
    """Two proofs of address means two DIFFERENT items.

    Counting by upload would let the requirement be satisfied by sending one
    bank statement twice — the shortcut a rushed intake takes, and one that
    leaves the file looking compliant while holding half the evidence.
    """
    same = "b" * 64
    case = _case(documents=[
        _doc(DocumentType.BANK_STATEMENT, same, document_id="d1"),
        _doc(DocumentType.BANK_STATEMENT, same, document_id="d2"),
    ])
    address = next(r for r in resolve_requirements(case, pack())
                   if r.requirement.key == "proof_of_address")
    assert address.held == 1
    assert address.state == RequirementState.PARTIAL

    distinct = _case(documents=[
        _doc(DocumentType.BANK_STATEMENT, "b" * 64, document_id="d1"),
        _doc(DocumentType.PROOF_OF_ADDRESS, "c" * 64, document_id="d2"),
    ])
    address = next(r for r in resolve_requirements(distinct, pack())
                   if r.requirement.key == "proof_of_address")
    assert address.held == 2
    assert address.state == RequirementState.ACCEPTED


def test_present_but_unread_is_received_never_accepted():
    """The distinction the whole module rests on.

    PDFs are not text-extractable here by design, so nearly every real upload
    carries `extraction_unavailable`. Present is not checked, and a state that
    collapsed the two would report a document nobody could read as confirmed.
    """
    case = _case(documents=[
        _doc(DocumentType.PASSPORT, "d" * 64,
             extraction_confidence=0.0,
             authenticity_flags=["extraction_unavailable",
                                 "no extractable text in document"]),
    ])
    identity = next(r for r in resolve_requirements(case, pack())
                    if r.requirement.key == "identity_document")
    assert identity.state == RequirementState.RECEIVED
    assert identity.held == 1
    assert any("human must read it" in reason
               for m in identity.matched for reason in m.reasons)

    codes = {f["code"] for f in assess(case, pack(), AS_OF)["findings"]}
    assert "DOCUMENT_NEEDS_REVIEW" in codes


def test_copy_only_original_keeps_it_off_accepted_without_double_reporting():
    """Sighting is reported by ONE rule, but it still holds the state back.

    rules.sighting_findings is the clause-referenced authority on 7.4 c)/d).
    The requirement must not emit a second finding for the same fact, and must
    not therefore let a copy-only passport read as ACCEPTED.
    """
    case = _case(documents=[
        _doc(DocumentType.PASSPORT, "e" * 64, sighting="COPY_ONLY",
             examined_by=""),
    ])
    identity = next(r for r in resolve_requirements(case, pack())
                    if r.requirement.key == "identity_document")
    assert identity.state == RequirementState.RECEIVED
    assert identity.matched[0].sighting_reasons        # carried for the UI
    assert not identity.matched[0].reasons            # but not as a finding

    findings = assess(case, pack(), AS_OF)["findings"]
    identity_findings = [f for f in findings
                         if f["code"] == "DOCUMENT_NEEDS_REVIEW"
                         and "identity" in f["message"].lower()]
    assert identity_findings == [], "the same fact was reported twice"
    assert any(f["code"] == "ORIGINAL_NOT_SIGHTED" for f in findings)


# ── R-F3211: manual requirements and waivers ─────────────────────────────

def test_waived_requirement_is_never_satisfied_and_names_who_waived_it():
    case = _case(requirement_waivers=[RequirementWaiver(
        key="cv", waived_by="A. Manager", reason="Internal transfer",
        waived_at=date(2026, 7, 20))])
    cv = next(r for r in resolve_requirements(case, pack())
              if r.requirement.key == "cv")
    assert cv.state == RequirementState.WAIVED
    assert cv.state != RequirementState.ACCEPTED
    assert cv.waived_by == "A. Manager"

    result = assess(case, pack(), AS_OF)
    waived = [f for f in result["findings"] if f["code"] == "REQUIREMENT_WAIVED"]
    assert len(waived) == 1
    assert "A. Manager" in waived[0]["message"]
    assert "Internal transfer" in waived[0]["message"]
    # A waiver removes work; it must not add a blocker or an action.
    assert waived[0]["severity"] == "INFO"
    # And it must not be counted toward completion.
    assert result["requirements_summary"]["accepted"] == 0


def test_a_manual_requirement_may_raise_but_never_lower_a_pack_requirement():
    """A customer wanting three proofs of address gets three. One wanting one
    does not get to reduce what the standard asks for — that is a waiver, and a
    waiver is signed."""
    raised = _case(extra_requirements=[DocumentRequirement(
        key="proof_of_address", label="Proof of address (three items)",
        accepted=[DocumentType.PROOF_OF_ADDRESS], min_count=3,
        basis="CLIENT_CONTRACT")])
    address = next(r for r in resolve_requirements(raised, pack())
                   if r.requirement.key == "proof_of_address")
    assert address.needed == 3

    lowered = _case(extra_requirements=[DocumentRequirement(
        key="proof_of_address", label="Proof of address (one item)",
        accepted=[DocumentType.PROOF_OF_ADDRESS], min_count=1,
        mandatory=False, basis="CLIENT_CONTRACT")])
    address = next(r for r in resolve_requirements(lowered, pack())
                   if r.requirement.key == "proof_of_address")
    assert address.needed == 2, "a manual entry lowered a pack requirement"
    assert address.requirement.mandatory is True


def test_a_brand_new_manual_requirement_is_counted_like_any_other():
    case = _case(extra_requirements=[DocumentRequirement(
        key="airside_pass", label="Airside pass application",
        accepted=[DocumentType.OTHER], basis="CLIENT_CONTRACT",
        stage="APPLICATION")])
    resolved = {r.requirement.key: r for r in resolve_requirements(case, pack())}
    assert resolved["airside_pass"].state == RequirementState.OUTSTANDING
    assert resolved["airside_pass"].requirement.origin == "MANUAL"
    codes = [f for f in assess(case, pack(), AS_OF)["findings"]
             if f["code"] == "DOCUMENT_OUTSTANDING"
             and "Airside" in f["message"]]
    assert codes, "a manual requirement produced no action"


# ── R-F3212: stages ──────────────────────────────────────────────────────

def test_stage_with_an_outstanding_finding_is_never_complete():
    """A stage whose every tick is ticked can still carry outstanding work.

    An undeclared gap belongs to Career history even when no career entry
    exists to be counted, so counting ticks alone showed '0/0 COMPLETE' over a
    screening period that nothing covered.
    """
    result = assess(_case(), pack(), AS_OF)
    history = next(s for s in result["stages"] if s["key"] == "HISTORY")
    assert history["actions"] >= 1
    assert history["state"] != "COMPLETE"


def test_a_blocker_makes_its_stage_blocked_not_merely_in_progress():
    case = _case(career=[CareerEntry(
        entry_id="c1", entry_type=CareerEntryType.EMPLOYMENT,
        start=date(2027, 1, 1), organisation="Future Ltd")])
    result = assess(case, pack(), AS_OF)
    history = next(s for s in result["stages"] if s["key"] == "HISTORY")
    assert history["blockers"] >= 1
    assert history["state"] == "BLOCKED"
    assert result["current_stage"] == "HISTORY"


def test_stages_only_count_what_the_pack_actually_asks_for():
    """A checklist field absent from the pack is not a gap in the file — it is
    a question this framework does not ask."""
    from aria_service.vetting.stages import build_stages

    intl = registry.latest_usable("intl_baseline")
    case = _case()
    stages = {s.key: s for s in build_stages(
        case, intl, resolve_requirements(case, intl), [], AS_OF)}
    # intl_baseline's checklist is the common set; it asks for none of the
    # seven 7.4 f) elements, so none of them may be counted against the file.
    # Only `watchlist_check_done` is in the common checklist.
    assert stages["PUBLIC_RECORD"].total == 1
    uk_stages = {s.key: s for s in build_stages(
        case, pack(), resolve_requirements(case, pack()), [], AS_OF)}
    assert uk_stages["PUBLIC_RECORD"].total > stages["PUBLIC_RECORD"].total


def test_summary_counts_only_accepted_as_complete():
    case = _case(documents=[
        _doc(DocumentType.PASSPORT, "f" * 64, extraction_confidence=0.0,
             authenticity_flags=["extraction_unavailable"]),
        _doc(DocumentType.APPLICATION_FORM, "0" * 64),
    ])
    resolved = {r.requirement.key: r for r in resolve_requirements(case, pack())}
    summary = summarise(list(resolved.values()))
    assert summary["accepted"] == 1        # the application form only
    # The unread passport counts toward BOTH identity and right to work — a
    # passport is List A right-to-work evidence, and encoding the alternatives
    # is the point of `accepted` being a list. Neither reaches ACCEPTED,
    # because nothing has read it.
    assert resolved["identity_document"].state == RequirementState.RECEIVED
    assert resolved["right_to_work"].state == RequirementState.RECEIVED
    assert summary["received"] == 2


def test_interview_needs_a_date_and_a_name_not_just_a_tick():
    """7.3.4 requires the interview BEFORE any offer — a claim about a date,
    which a boolean cannot evidence."""
    ticked = _case(inputs=ScreeningInputs(interview_done=True))
    messages = [f["message"] for f in assess(ticked, pack(), AS_OF)["findings"]]
    assert any("Interview date" in m for m in messages)
    assert any("conducted the interview" in m for m in messages)

    recorded = _case(inputs=ScreeningInputs(
        interview_done=True, interview_date=date(2026, 6, 15),
        interviewed_by="H. Manager"))
    messages = [f["message"] for f in assess(recorded, pack(), AS_OF)["findings"]]
    assert not any("Interview date" in m for m in messages)


def test_v120_cases_do_not_gain_new_requirements_retroactively():
    """Pack versions are immutable and pinned per case. A case opened under
    1.2.0 keeps 1.2.0's rules — otherwise every historical assessment silently
    changes meaning."""
    old = registry.get_exact("uk_bs7858", "1.2.0",
                             B.UK_BS7858_V120.content_hash())
    assert old.required_documents == []
    codes = {f["code"] for f in assess(_case(), old, AS_OF)["findings"]}
    assert "DOCUMENT_OUTSTANDING" not in codes


# ── R-F3214: the BS 7858 clause register ─────────────────────────────────

def test_a_clause_cannot_claim_coverage_the_pack_does_not_corroborate():
    """THE property that keeps the register honest.

    Without this the register is a list of assertions about compliance
    coverage — the kind of artefact that gets believed and should not be. The
    check must be able to FAIL, so this proves it fails on a fabricated clause
    rather than only that it passes on the real ones.
    """
    from aria_service.vetting import standard_map as sm

    live = sm.coverage_report(pack())
    assert live["counts"]["encoded"] > 0
    assert live["counts"]["claimed_not_corroborated"] == 0, (
        "a clause in the register claims code the live pack cannot "
        "corroborate — fix standard_map.py, not the pack")
    assert live["not_modelled"], "the register must state what it does NOT cover"

    original = sm.CLAUSES
    try:
        sm.CLAUSES = (*original, sm.Clause(
            clause="9.9 z)", title="Invented",
            requirement="Nothing implements this.",
            status=sm.ClauseStatus.ENCODED))
        probed = sm.coverage_report(pack())
        caught = [c for c in probed["clauses"]
                  if c["status"] == "CLAIMED_NOT_CORROBORATED"]
        assert [c["clause"] for c in caught] == ["9.9 z)"], (
            "the corroboration check is vacuous — it passed a clause that "
            "nothing implements")
    finally:
        sm.CLAUSES = original


def test_the_register_stores_no_text_of_the_standard():
    """BS 7858 is BSI copyright. Clause numbers and our own words only."""
    from aria_service.vetting import standard_map as sm

    for clause in sm.CLAUSES:
        assert clause.requirement, f"{clause.clause} has no stated requirement"
        # Our statements are prose we wrote. A verbatim extract would read as
        # normative drafting — "shall" is the tell.
        assert " shall " not in clause.requirement.lower(), (
            f"{clause.clause} reads like reproduced standard text")


# ── R-F3209: the officer can read a stored document ──────────────────────

@pytest.fixture()
def client(tmp_path, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from aria_service.intel import dd_evidence_store as evidence_module
    from aria_service.routes.vetting import router as vetting_router
    from aria_service.vetting import store as store_module

    monkeypatch.setenv("ARIA_API_TOKEN", TOKEN)
    monkeypatch.setenv("ARIA_VETTING_DB", str(tmp_path / "vetting.db"))
    monkeypatch.setattr(store_module, "_STORE", None, raising=False)

    # Point the evidence store at the temp tree too, so an upload test never
    # writes into /data.
    isolated = evidence_module.DDEvidenceStore(
        db_path=tmp_path / "evidence.db", artifact_dir=tmp_path / "artifacts")
    monkeypatch.setattr(evidence_module, "get_evidence_store", lambda: isolated)

    app = FastAPI()
    app.include_router(vetting_router)
    return TestClient(app)


def _create(client, case_id: str = "DOC-1"):
    return client.post("/api/aria/vetting/cases", json={
        "case_id": case_id, "applicant_name": "Jane Doe",
        "date_of_birth": "1990-01-01", "employment_start": "2026-07-01",
        "pack_id": "uk_bs7858",
    }, params={"user_id": TENANT}, headers=AUTH)


def _upload(client, case_id, filename, content: bytes, doc_type="OTHER"):
    return client.post(
        f"/api/aria/vetting/case/{case_id}/documents",
        json={"filename": filename,
              "content_base64": base64.b64encode(content).decode(),
              "declared_doc_type": doc_type},
        params={"user_id": TENANT}, headers=AUTH)


def test_upload_then_read_it_back_byte_for_byte(client):
    """THE capability test for R-F3209.

    The module could store, encrypt, hash and prove a document and had no way
    to show it to anyone — the officer's core task is to LOOK at the passport.
    This drives the officer's actual path: upload, list, open.
    """
    assert _create(client).status_code == 200
    content = b"%PDF-1.4 pretend passport scan"
    uploaded = _upload(client, "DOC-1", "passport.pdf", content, "PASSPORT")
    assert uploaded.status_code == 200, uploaded.text
    document_id = uploaded.json()["document_id"]
    assert uploaded.json()["doc_type"] == "PASSPORT", (
        "the officer's declared type was discarded; a PDF cannot be "
        "text-extracted, so the declaration is the only signal there is")

    listed = client.get("/api/aria/vetting/case/DOC-1/documents",
                        params={"user_id": TENANT}, headers=AUTH)
    assert listed.status_code == 200, listed.text
    rows = listed.json()["documents"]
    assert len(rows) == 1
    assert rows[0]["filename"] == "passport.pdf"
    assert rows[0]["viewable"] is True
    assert rows[0]["renders_inline"] is True

    opened = client.get(
        f"/api/aria/vetting/case/DOC-1/documents/{document_id}/content",
        params={"user_id": TENANT}, headers=AUTH)
    assert opened.status_code == 200, opened.text
    assert opened.content == content, "the bytes handed back are not the bytes stored"
    assert opened.headers["content-type"].startswith("application/pdf")
    assert opened.headers["content-disposition"].startswith("inline")
    assert "no-store" in opened.headers["cache-control"]
    assert opened.headers["x-content-type-options"] == "nosniff"


def test_a_declared_type_satisfies_a_requirement_end_to_end(client):
    """Upload → assess. The point of declaring the type is that the engine can
    then count it, which is what the old hardcoded 'OTHER' made impossible."""
    _create(client, "DOC-REQ")
    _upload(client, "DOC-REQ", "form.pdf", b"application form", "APPLICATION_FORM")
    result = client.post("/api/aria/vetting/case/DOC-REQ/assess",
                         params={"user_id": TENANT, "as_of": "2026-07-27"},
                         headers=AUTH).json()
    form = next(r for r in result["requirements"] if r["key"] == "application_form")
    assert form["held"] == 1
    # RECEIVED, not ACCEPTED: nothing could read the PDF, and the officer has
    # not yet confirmed it. That gap is the honest one.
    assert form["state"] == RequirementState.RECEIVED


def test_html_upload_is_never_served_inline(client):
    """Applicants and referees upload through an unauthenticated portal link,
    and this content is served from our own origin. An HTML file rendered
    inline is script running as us against an officer's session."""
    _create(client, "DOC-XSS")
    uploaded = _upload(client, "DOC-XSS", "evil.html",
                       b"<script>alert(document.cookie)</script>")
    document_id = uploaded.json()["document_id"]
    opened = client.get(
        f"/api/aria/vetting/case/DOC-XSS/documents/{document_id}/content",
        params={"user_id": TENANT}, headers=AUTH)
    assert opened.status_code == 200
    assert opened.headers["content-type"].startswith("application/octet-stream")
    assert opened.headers["content-disposition"].startswith("attachment")


def test_documents_do_not_leak_across_tenants(client):
    _create(client, "DOC-SECRET")
    uploaded = _upload(client, "DOC-SECRET", "passport.pdf", b"secret",
                       "PASSPORT")
    document_id = uploaded.json()["document_id"]
    for path in ("/api/aria/vetting/case/DOC-SECRET/documents",
                 f"/api/aria/vetting/case/DOC-SECRET/documents/{document_id}/content"):
        response = client.get(path, params={"user_id": OTHER_TENANT},
                              headers=AUTH)
        assert response.status_code == 404, f"{path} leaked across tenants"


def test_an_unknown_document_id_is_404_not_a_server_error(client):
    _create(client, "DOC-404")
    response = client.get(
        "/api/aria/vetting/case/DOC-404/documents/vdoc_nope/content",
        params={"user_id": TENANT}, headers=AUTH)
    assert response.status_code == 404


# ── R-F3211 over HTTP ────────────────────────────────────────────────────

def test_add_waive_and_remove_requirements_over_http(client):
    _create(client, "REQ-HTTP")

    added = client.post("/api/aria/vetting/case/REQ-HTTP/requirements", json={
        "key": "airside pass", "label": "Airside pass application",
        "accepted": ["OTHER"], "min_count": 1,
    }, params={"user_id": TENANT}, headers=AUTH)
    assert added.status_code == 200, added.text
    assert added.json()["requirement"]["key"] == "airside_pass"
    assert added.json()["requirement"]["basis"] == "CLIENT_CONTRACT", (
        "a hand-added requirement must not default to citing the standard")

    result = client.post("/api/aria/vetting/case/REQ-HTTP/assess",
                         params={"user_id": TENANT}, headers=AUTH).json()
    assert any(r["key"] == "airside_pass" for r in result["requirements"])

    waived = client.post("/api/aria/vetting/case/REQ-HTTP/requirements/waive",
                         json={"key": "cv", "waived_by": "A. Manager",
                               "reason": "Internal transfer"},
                         params={"user_id": TENANT}, headers=AUTH)
    assert waived.status_code == 200, waived.text
    result = client.post("/api/aria/vetting/case/REQ-HTTP/assess",
                         params={"user_id": TENANT}, headers=AUTH).json()
    cv = next(r for r in result["requirements"] if r["key"] == "cv")
    assert cv["state"] == "WAIVED"
    assert cv["waived_by"] == "A. Manager"

    # A pack requirement cannot be deleted — only waived, which leaves a name.
    refused = client.delete("/api/aria/vetting/case/REQ-HTTP/requirements/cv",
                            params={"user_id": TENANT}, headers=AUTH)
    assert refused.status_code == 404
    assert refused.json()["detail"]["code"] == "not_a_manual_requirement"

    removed = client.delete(
        "/api/aria/vetting/case/REQ-HTTP/requirements/airside_pass",
        params={"user_id": TENANT}, headers=AUTH)
    assert removed.status_code == 200


def test_a_waiver_cannot_be_anonymous(client):
    _create(client, "REQ-ANON")
    for payload in ({"key": "cv", "waived_by": "", "reason": "because"},
                    {"key": "cv", "waived_by": "A. Manager", "reason": ""}):
        response = client.post(
            "/api/aria/vetting/case/REQ-ANON/requirements/waive",
            json=payload, params={"user_id": TENANT}, headers=AUTH)
        assert response.status_code == 422, (
            f"an anonymous or unexplained waiver was accepted: {payload}")


def test_document_types_are_served_not_hardcoded(client):
    response = client.get("/api/aria/vetting/document-types", headers=AUTH)
    assert response.status_code == 200
    body = response.json()
    served = {t for group in body["groups"] for t in group["types"]}
    assert served == {t.value for t in DocumentType}, (
        "the served picker and the engine's vocabulary have drifted")


def test_standard_coverage_is_served_with_its_own_limits(client):
    response = client.get("/api/aria/vetting/standard", headers=AUTH)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["counts"]["claimed_not_corroborated"] == 0
    # The counts must never travel without the scope limits — a coverage
    # figure read alone reads as completeness.
    assert body["not_modelled"]
    assert "not model" in body["honest_summary"]
