"""R-F3426 — the checklist must read the evidence the DD now gathers.

THE CONTRADICTION THIS CLOSES. R-F3422/R-F3403/R-F3424 wired five registers into the
run — Companies House charges, CH insolvency, CH disqualified officers, The Gazette
(corporate AND personal insolvency) and the employment tribunals. Their findings land in
the report. And `dd_standard.assess` still reported every one of those questions as
NOT_RUN, because FS-11 / FS-12 / IS-16b / IS-17a / IS-17c were declared with
`reader=None`.

So a customer would read an insolvency finding in the body beside "insolvency: not run"
in the scorecard — two surfaces of the same report disagreeing, on the surface they are
told to rely on. Same family as the officer screen, the discipline proxy and the
layer-health proxy; worse here because the checklist is the deliverable.

A SECOND DEFECT, FOUND BY WIRING THE FIRST. With the readers bound and the CH key
present, IS-16b STILL read NOT_RUN — because R-F3422 deliberately stays silent when the
disqualification register answers with nothing. No finding, no gap, so the checklist
concluded nobody looked. A CLEAN CHECK READING AS AN UNRUN ONE is certify-by-absence
inverted, and it understates every honest report. R-F3426 makes that path emit one
summary finding.

MEASURED end to end on Companies House 04300718 with credentials present:
    FS-11  CORROBORATED   (CH insolvency + The Gazette — two independent origins)
    FS-12  SINGLE_SOURCE  (charges register answered)
    IS-16b SINGLE_SOURCE  (1 officer checked, no match)
    IS-17c SINGLE_SOURCE  (tribunal index answered)
"""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

import pytest

from aria_service.intel import companies_house as ch
from aria_service.intel import dd_orchestrator as ddo
from aria_service.intel import dd_standard as S
from aria_service.intel.dd_schema import ARKDDReport


def _run(coro):
    return asyncio.run(coro)


def _finding(source: str, title: str = "x", severity: str = "info") -> dict:
    return {"source": source, "title": title, "severity": severity, "detail": ""}


def _report(findings=None, gaps=None) -> dict:
    return {"identity": {"entity_type": "company", "entity_name": "Testco Ltd",
                         "findings": findings or [], "data_gaps": gaps or []}}


def _state(rep: dict, qid: str) -> str:
    a = S.assess(rep, tier="STANDARD")
    return next(r["state"] for r in a["resolutions"] if r["question_id"] == qid)


# ── every wired register now has a reader ────────────────────────────────────

@pytest.mark.parametrize("qid", ["FS-11", "FS-12", "IS-16b", "IS-17a", "IS-17c"])
def test_the_question_has_a_reader(qid):
    assert S.QUESTIONS_BY_ID[qid].reader is not None, (
        f"{qid} declares resolvers but has no reader — the DD gathers the evidence and "
        f"the checklist reports NOT_RUN, which is the contradiction R-F3426 closes"
    )


# ── a register finding answers its question ──────────────────────────────────

@pytest.mark.parametrize("qid,source", [
    ("FS-12", "companies_house.charges"),
    ("IS-16b", "companies_house.disqualified_officers"),
    ("IS-17c", "employment_tribunal.decisions"),
    ("IS-17a", "court_records"),
])
def test_a_register_finding_answers_its_question(qid, source):
    assert _state(_report([_finding(source)]), qid) in (
        S.EvidenceState.SINGLE_SOURCE.value, S.EvidenceState.CORROBORATED.value)


def test_an_empty_register_still_answers():
    """'No outstanding charges' is a real answer. Only a register that did not respond
    leaves the question open."""
    rep = _report([_finding("companies_house.charges", "No outstanding charges registered")])
    assert _state(rep, "FS-12") == S.EvidenceState.SINGLE_SOURCE.value


def test_two_independent_registers_corroborate():
    """FS-11 is answerable by CH /insolvency AND The Gazette. Two origins is the whole
    point of the top state — measured live, this is the first row to earn it."""
    rep = _report([_finding("companies_house.insolvency"),
                   _finding("gazette.corporate_insolvency")])
    assert _state(rep, "FS-11") == S.EvidenceState.CORROBORATED.value


def test_one_register_alone_is_not_corroborated():
    rep = _report([_finding("companies_house.insolvency")])
    assert _state(rep, "FS-11") == S.EvidenceState.SINGLE_SOURCE.value


# ── a register that did not answer is never a pass ───────────────────────────

@pytest.mark.parametrize("qid,gap", [
    ("FS-12", "Charges register NOT checked — rate limit exhausted (not a clear result)"),
    ("FS-11", "Insolvency register NOT checked — timed out (not a clear result)"),
    ("IS-16b", "Disqualification check 'Jane Public' NOT performed — unavailable"),
    ("IS-17c", "Employment tribunal search did not complete (not a clear result)"),
])
def test_an_unreached_register_is_inconclusive_not_answered(qid, gap):
    st = _state(_report([], [gap]), qid)
    assert st == S.EvidenceState.ATTEMPTED_INCONCLUSIVE.value, (
        f"{qid} read {st} for a register that did not answer"
    )


def test_no_evidence_at_all_is_not_run():
    assert _state(_report(), "FS-12") == S.EvidenceState.NOT_RUN.value


def test_inconclusive_and_not_run_are_different_sentences():
    """'We tried and it did not answer' has a remedy; 'nothing looked' has a different
    one. Collapsing them sends the reader to the wrong action."""
    a = S.assess(_report([], ["Charges register NOT checked — timed out"]), tier="STANDARD")
    b = S.assess(_report(), tier="STANDARD")
    ra = next(x for x in a["resolutions"] if x["question_id"] == "FS-12")
    rb = next(x for x in b["resolutions"] if x["question_id"] == "FS-12")
    assert ra["state"] != rb["state"]
    assert ra["remedy"] and rb["remedy"]


def test_a_tribunal_finding_does_not_answer_the_court_question():
    """Different bodies, different coverage. Letting a tribunal result answer IS-17a
    would claim judgment-database coverage the run never had."""
    rep = _report([_finding("employment_tribunal.decisions")])
    assert _state(rep, "IS-17a") == S.EvidenceState.NOT_RUN.value


# ── the second defect: a clean check must leave a trace ──────────────────────

def _drive_registers(report: ARKDDReport, *, dq_hits: int = 0, checked: bool = True):
    ok_charges = {"checked": True, "total_count": 0, "outstanding_count": 0, "items": []}
    ok_ins = {"checked": True, "case_count": 0, "cases": []}
    dq = {"checked": checked, "total_results": dq_hits, "candidates": [],
          "match_basis": "name_only", "reason": "rate limit exhausted"}
    with patch.object(ch, "get_charges", AsyncMock(return_value=ok_charges)), \
         patch.object(ch, "get_insolvency", AsyncMock(return_value=ok_ins)), \
         patch.object(ch, "search_disqualified_officers", AsyncMock(return_value=dq)):
        _run(ddo._run_ch_free_registers(report, {"company_number": "04300718"}))
    return report


def test_a_clean_disqualification_check_emits_a_finding():
    """Before R-F3426 this path was SILENT: no finding, no gap, so the checklist read
    IS-16b as NOT_RUN on a run where every officer HAD been searched."""
    r = ARKDDReport()
    r.identity.entity_type = "company"
    r.identity.directors = [{"name": "Jane Q Public"}]
    _drive_registers(r)
    titles = [f.title for f in r.identity.findings]
    assert any("disqualified-directors register" in t for t in titles), (
        "a clean disqualification check left no trace — indistinguishable from never "
        "having looked"
    )


def test_the_clean_check_finding_answers_is16b_end_to_end():
    r = ARKDDReport()
    r.identity.entity_type = "company"
    r.identity.directors = [{"name": "Jane Q Public"}]
    _drive_registers(r)
    assert _state(r.as_dict(), "IS-16b") == S.EvidenceState.SINGLE_SOURCE.value


def test_one_summary_finding_not_one_per_officer():
    """A per-officer 'nothing found' would bury the report in non-findings."""
    r = ARKDDReport()
    r.identity.entity_type = "company"
    r.identity.directors = [{"name": f"Officer Number {i}"} for i in range(5)]
    _drive_registers(r)
    dq = [f for f in r.identity.findings
          if f.source == "companies_house.disqualified_officers"]
    assert len(dq) == 1
    assert "5 serving officer(s)" in dq[0].title


def test_the_clean_finding_states_the_name_match_limitation():
    r = ARKDDReport()
    r.identity.entity_type = "company"
    r.identity.directors = [{"name": "Jane Q Public"}]
    _drive_registers(r)
    f = next(f for f in r.identity.findings
             if f.source == "companies_house.disqualified_officers")
    assert "matches on name" in f.detail
    assert "another spelling" in f.detail


def test_an_unreachable_register_emits_no_clean_finding():
    r = ARKDDReport()
    r.identity.entity_type = "company"
    r.identity.directors = [{"name": "Jane Q Public"}]
    _drive_registers(r, checked=False)
    assert not any(f.source == "companies_house.disqualified_officers"
                   for f in r.identity.findings)
    assert "NOT performed" in " ".join(r.identity.data_gaps)


def test_a_hit_does_not_also_produce_the_cleared_summary():
    r = ARKDDReport()
    r.identity.entity_type = "company"
    r.identity.directors = [{"name": "Jane Q Public"}]
    _drive_registers(r, dq_hits=2)
    titles = [f.title for f in r.identity.findings]
    assert any("NAME MATCH" in t for t in titles)
    assert not any("no match" in t for t in titles), (
        "an officer with a register hit was also reported as cleared"
    )
