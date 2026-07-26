"""R-F3188/R-F3189 — the request ledger and document sighting.

Both come straight off the operator's manual Verification Progress Sheet: the
Code / Request sent / Reply rec. columns, and the COPY / ORG / N-A columns.
"""

from __future__ import annotations

from datetime import date

import pytest

from aria_service.vetting.models import (
    DocumentType, UploadedDocument, VettingCase,
)
from aria_service.vetting.packs.base import registry
from aria_service.vetting.requests import (
    CODE_LABELS, DEFAULT_CHASE_AFTER_DAYS, RequestCode, RequestError,
    RequestStatus, code_for_invite, record, summarise,
)
from aria_service.vetting.rules import sighting_findings

AS_OF = date(2026, 7, 26)
TENANT = "tenant-a"
PACK = registry.latest_usable("uk_bs7858")


def _req(**kw):
    base = dict(case_id="C1", code=RequestCode.WR, sent_to="hr@alpha.example",
                sent_at=date(2026, 7, 20))
    base.update(kw)
    return record(**base)


# ── the operator's own codes ──────────────────────────────────────────────

def test_all_nine_sheet_codes_exist_with_labels():
    """Verbatim from the sheet: an officer moving onto this system should see
    their own vocabulary, not ours."""
    assert {c.value for c in RequestCode} == {
        "AR", "CL", "CR", "DR", "ER", "GR", "SDR", "TR", "WR"}
    for code in RequestCode:
        assert CODE_LABELS[code], f"{code} has no label"


def test_an_invite_maps_to_the_right_request_code():
    """The ledger is FED by the invite flow, so the mapping must be real
    rather than assuming every referee is a work reference."""
    assert code_for_invite("APPLICANT") is RequestCode.DR
    assert code_for_invite("REFEREE", "EMPLOYMENT") is RequestCode.WR
    assert code_for_invite("REFEREE", "EDUCATION") is RequestCode.ER
    assert code_for_invite("REFEREE", "SELF_EMPLOYMENT") is RequestCode.AR
    assert code_for_invite("REFEREE", "UNEMPLOYMENT") is RequestCode.GR
    assert code_for_invite("REFEREE", "CAREER_BREAK") is RequestCode.GR


# ── what a request must record ────────────────────────────────────────────

def test_a_request_must_name_its_recipient():
    """An unaddressed request cannot be chased and cannot be evidenced."""
    with pytest.raises(RequestError, match="WHO"):
        _req(sent_to="   ")


def test_a_chaser_must_name_what_it_chases():
    """Three chasers should be three rows an auditor can follow, not a counter."""
    with pytest.raises(RequestError, match="follows up"):
        _req(code=RequestCode.CL)
    ok = _req(code=RequestCode.CL, chases="vreq_original")
    assert ok.chases == "vreq_original"


def test_a_request_links_to_the_invite_it_was_sent_as():
    r = _req(invite_id="inv_abc", channel="email")
    assert r.invite_id == "inv_abc"
    assert r.as_dict()["invite_id"] == "inv_abc"


# ── overdue is computed, never stored ─────────────────────────────────────

def test_overdue_is_derived_from_the_date_not_persisted():
    """A stored 'overdue' flag would be another cached verdict going stale
    behind a changing file — the defect R-F3172 had to fix."""
    r = _req(sent_at=date(2026, 7, 1))
    assert "overdue" not in r.__dict__
    assert r.is_overdue(date(2026, 7, 5)) is False
    assert r.is_overdue(date(2026, 7, 20)) is True
    assert r.days_outstanding(date(2026, 7, 20)) == 19


def test_the_chase_interval_is_house_policy_and_overridable():
    """BS 7858 sets the OVERALL 12/16-week clock; it does not prescribe a chase
    interval, so this must not masquerade as the standard."""
    r = _req(sent_at=date(2026, 7, 20))
    at = date(2026, 7, 26)                       # 6 days out
    assert r.is_overdue(at, chase_after_days=DEFAULT_CHASE_AFTER_DAYS) is False
    assert r.is_overdue(at, chase_after_days=5) is True


def test_a_closed_request_is_never_overdue():
    for status in (RequestStatus.REPLY_RECEIVED, RequestStatus.REFUSED,
                   RequestStatus.CANCELLED):
        r = _req(sent_at=date(2026, 1, 1))
        closed = type(r)(**{**r.__dict__, "status": status})
        assert closed.is_open is False
        assert closed.is_overdue(AS_OF) is False
        assert closed.days_outstanding(AS_OF) == 0


def test_an_undeliverable_request_stays_open_because_it_needs_action():
    """A bounced request is not answered — it needs a new contact, so it must
    keep showing up rather than closing quietly."""
    r = _req(sent_at=date(2026, 1, 1))
    bounced = type(r)(**{**r.__dict__, "status": RequestStatus.UNDELIVERABLE})
    assert bounced.is_open is True
    assert bounced.is_overdue(AS_OF) is True


def test_summary_counts_open_overdue_and_closed():
    reqs = [
        _req(sent_at=date(2026, 7, 1)),                       # overdue
        _req(sent_at=date(2026, 7, 25)),                      # open, not overdue
    ]
    closed = _req(sent_at=date(2026, 1, 1))
    reqs.append(type(closed)(**{**closed.__dict__,
                                "status": RequestStatus.REPLY_RECEIVED}))
    s = summarise(reqs, AS_OF)
    assert (s.open_count, s.overdue_count, s.closed_count) == (2, 1, 1)
    assert len(s.overdue) == 1


# ── R-F3189: originals vs copies ──────────────────────────────────────────

def _case_with(doc_type, **doc_kw):
    return VettingCase(
        tenant_id=TENANT, case_id="S1", applicant_name="T",
        date_of_birth=date(1990, 1, 1), employment_start=date(2026, 6, 1),
        documents=[UploadedDocument(document_id="d1", doc_type=doc_type, **doc_kw)],
    )


def test_a_copy_only_identity_document_is_flagged():
    """A forged PDF passes a copy check and fails an in-person one — that is
    the entire point of 7.4 c)."""
    codes = {f.code for f in sighting_findings(
        _case_with(DocumentType.PASSPORT, sighting="COPY_ONLY"), PACK)}
    assert "ORIGINAL_NOT_SIGHTED" in codes


def test_unrecorded_sighting_is_distinct_from_copy_only():
    """"Nobody has said" and "we hold only a copy" are different states.
    Defaulting the unanswered question to the weaker answer would assert
    something nobody checked."""
    codes = {f.code for f in sighting_findings(
        _case_with(DocumentType.PASSPORT), PACK)}          # default NOT_RECORDED
    assert "SIGHTING_NOT_RECORDED" in codes
    assert "ORIGINAL_NOT_SIGHTED" not in codes


def test_an_original_seen_with_no_examiner_named_is_flagged():
    """7.4 c) requires a record of WHO examined and copied the original."""
    codes = {f.code for f in sighting_findings(
        _case_with(DocumentType.PASSPORT, sighting="ORIGINAL_SEEN"), PACK)}
    assert "EXAMINER_NOT_RECORDED" in codes


def test_a_properly_sighted_document_raises_nothing():
    findings = sighting_findings(
        _case_with(DocumentType.PASSPORT, sighting="ORIGINAL_SEEN",
                   examined_by="S. Officer", examined_at=date(2026, 7, 1)), PACK)
    assert findings == []


def test_not_applicable_is_accepted_without_complaint():
    """An electronic right-to-work share code has no original to sight."""
    assert sighting_findings(
        _case_with(DocumentType.PASSPORT, sighting="NOT_APPLICABLE"), PACK) == []


def test_documents_outside_the_originals_list_are_not_policed():
    """A payslip is documentary evidence, not an identity document — the
    standard does not demand the original."""
    assert sighting_findings(
        _case_with(DocumentType.PAYSLIP, sighting="COPY_ONLY"), PACK) == []


def test_the_uk_pack_requires_originals_for_identity_documents():
    required = set(PACK.originals_required)
    for doc in (DocumentType.PASSPORT, DocumentType.DRIVING_LICENCE,
                DocumentType.SIA_LICENCE, DocumentType.DISCLOSURE_CERTIFICATE):
        assert doc in required, f"{doc} should require an original"


def test_sighting_findings_reach_the_assessment():
    from aria_service.vetting.rules import assess
    result = assess(_case_with(DocumentType.PASSPORT, sighting="COPY_ONLY"),
                    PACK, as_of=AS_OF)
    assert "ORIGINAL_NOT_SIGHTED" in {f["code"] for f in result["findings"]}
