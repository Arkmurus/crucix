"""R-F3206 — the referee the applicant nominated never reached the share dialog.

THE OPERATOR'S REQUEST: the share dialog makes the vetting officer TYPE the
referee's name and address, when the applicant already nominated them on the
application form. Re-keying data the file holds is how a referee link goes to the
wrong address — and a referee link exposes one engagement, so that is a
disclosure, not a typo.

The nomination belongs on the PERIOD, not on the share request: a period is what a
referee confirms, one referee can cover several periods, and the nomination has to
survive the dialog being cancelled and reopened.

A gap period (unemployment, travel) legitimately has nobody nominated. Those are
not defects — the officer names someone by hand — but they were INVISIBLE until
the dialog opened with an empty box. `REFEREE_NOT_NOMINATED` surfaces them as
their own ACTION, per period, like every other evidence gap.
"""
from datetime import date

import pytest

from aria_service.vetting.models import (
    CareerEntry, CareerEntryType, DocumentType, VerificationState,
)
# Severity and Finding live in rules.py, not models.py (verified, §3b).
from aria_service.vetting.rules import Severity, referee_findings

# R-F3785/§16 — NOT inspect.getsource: it slices at line numbers captured
# AT IMPORT, so a mid-run edit silently returns a DIFFERENT function's body.
from ._source_probe import function_source


def _entry(entry_id="e1", etype=CareerEntryType.EMPLOYMENT, **kw):
    base = dict(entry_id=entry_id, entry_type=etype,
                start=date(2022, 1, 1), end=date(2023, 1, 1),
                organisation="Acme Ltd")
    base.update(kw)
    return CareerEntry(**base)


# ── the model contract ────────────────────────────────────────────────────────

def test_rf3206_nomination_needs_a_name_and_a_way_to_reach_them():
    """A name with no address cannot be sent a link, so it is not a usable
    nomination — calling it one would recreate the manual re-keying."""
    assert _entry(referee_name="Dana Okafor",
                  referee_email="dana@acme.example").has_nominated_referee() is True
    assert _entry(referee_name="Dana Okafor",
                  referee_phone="+447700900000").has_nominated_referee() is True
    assert _entry(referee_name="Dana Okafor").has_nominated_referee() is False
    assert _entry(referee_email="dana@acme.example").has_nominated_referee() is False
    assert _entry().has_nominated_referee() is False


def test_rf3206_blank_and_whitespace_are_not_a_nomination():
    assert _entry(referee_name="   ", referee_email="a@b.c").has_nominated_referee() is False
    assert _entry(referee_name="Dana", referee_email="   ").has_nominated_referee() is False


def test_rf3206_fields_are_optional_so_existing_cases_still_load():
    """Every persisted case predates these fields; none may become invalid."""
    e = _entry()
    assert e.referee_name is None and e.referee_email is None
    assert e.referee_phone is None and e.referee_title is None


# ── the finding ───────────────────────────────────────────────────────────────

class _Pack:
    pack_id = "test-pack"
    direct_reference_documents = [DocumentType.EMPLOYMENT_CONTRACT]
    accepted_evidence = {
        CareerEntryType.EMPLOYMENT: [DocumentType.EMPLOYMENT_CONTRACT, DocumentType.PAYSLIP],
        CareerEntryType.UNEMPLOYMENT: [DocumentType.PROOF_OF_ADDRESS],
    }
    evidence_references = {}


class _Case:
    def __init__(self, career):
        self.career = career


def _find(career, pack=None):
    return referee_findings(_Case(career), pack or _Pack(), date(2026, 1, 1))


def test_rf3206_flags_a_period_with_no_nomination():
    out = _find([_entry()])
    assert len(out) == 1
    assert out[0].code == "REFEREE_NOT_NOMINATED"
    assert out[0].severity == Severity.ACTION, (
        "a missing nomination is work to do, not a reason the file cannot proceed")
    assert out[0].entry_id == "e1"
    assert "no referee was nominated" in out[0].message


def test_rf3206_silent_when_the_referee_is_nominated():
    assert _find([_entry(referee_name="Dana", referee_email="d@a.example")]) == []


def test_rf3206_names_the_partial_nomination_distinctly():
    """'Named but unreachable' has a different remedy from 'nobody named'."""
    out = _find([_entry(referee_name="Pat Lee")])
    assert len(out) == 1
    assert "Pat Lee" in out[0].message
    assert "no email or phone" in out[0].message


def test_rf3206_gap_periods_are_never_flagged():
    """No referee confirms unemployment — flagging it would be noise, and noise
    is what makes a real action get skipped."""
    assert _find([_entry("g1", CareerEntryType.UNEMPLOYMENT, organisation=None)]) == []


def test_rf3206_verified_periods_are_not_chased():
    assert _find([_entry(state=VerificationState.VERIFIED)]) == []


def test_rf3206_pack_without_references_produces_nothing():
    """Which periods need a referee comes from the PACK, not a hardcoded list, so
    a jurisdiction that does not use references is silent here."""
    class _NoRefPack(_Pack):
        direct_reference_documents = []
    assert _find([_entry()], _NoRefPack()) == []


def test_rf3206_reports_per_period_not_once_per_case():
    out = _find([_entry("e1"), _entry("e2", organisation="Globex"),
                 _entry("e3", referee_name="Dana", referee_email="d@a.example")])
    assert {f.entry_id for f in out} == {"e1", "e2"}, (
        "each period is chased separately — a single case-level warning does not "
        "tell the officer which period is short")


def test_rf3206_is_wired_into_assess():
    """A finding generator nobody calls is the defect class this repo keeps
    hitting — protection that exists but is not on the path."""
    import inspect
    from aria_service.vetting import rules
    src = function_source(rules, "assess")
    assert "referee_findings" in src, (
        "R-F3206 REGRESSION: referee_findings is no longer called by assess()")
