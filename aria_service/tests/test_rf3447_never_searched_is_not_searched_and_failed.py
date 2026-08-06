"""R-F3447 — an UNCONFIGURED register must not report as "searched and did not answer".

FOUND BY A LIVE DD, not by reading code. A real run under the operator's account
(dd_610b97fc5557, Babcock International Group PLC, 2026-07-29) elected IS-17b. The report
correctly said ORDERED BUT NOT COMPLETED on the customer-facing surface, but the checklist
ledger said:

    failure_kind : source_failed
    detail       : the source was searched and did not answer

There is no Registry Trust contract, so the register was never contacted. The two states
are not interchangeable and the difference is what the reader DOES about it:

    source_failed  -> the register's fault, transient, RETRY may work
    not configured -> ours, structural, retrying changes NOTHING until a contract exists

Telling a buyer that a judgment register "did not answer" when nobody asked it is the same
class of dishonesty as a clean line on an unsearched register — the thing this whole
standard exists to prevent. `_register_reader` assumed any gap naming the register meant
"tried and failed", which is true for the always-attempted free registers (Companies
House, Gazette) and false for a gated one.
"""
from __future__ import annotations

import pytest

from aria_service.intel import dd_standard as ds

# R-F3757/§16 — NOT inspect.getsource: it slices at line numbers captured
# AT IMPORT, so an edit mid-run silently returns a DIFFERENT function's body.
from ._source_probe import function_source


ELECTION = [{"question_id": "IS-17b", "elected_by": "acorrea@arkmurus.com"}]

# The EXACT gap text the live run produced, so this test reproduces the report shape that
# was actually served rather than a paraphrase of it.
LIVE_PREFLIGHT_GAP = (
    "ORDERED SECTION IS-17b (County Court Judgments (CCJs) against the company or the "
    "individual) CANNOT BE SEARCHED on this run — Registry Trust (Register of Judgments, "
    "Orders and Fines): No CCJ backend configured. It is not covered and must not be "
    "charged for")
LIVE_RUNTIME_GAP = (
    "CCJ search was ORDERED for this subject but could not run: No CCJ backend configured. "
    "Registry Trust is the only authoritative source for England & Wales and has no public "
    "API. Not searched — an unsearched judgment register is not a clean one, and this "
    "section must not be charged for.")


def _report(gaps, findings=()):
    return {"identity": {"entity_name": "Babcock International Group PLC",
                         "entity_type": "company",
                         "findings": list(findings), "data_gaps": list(gaps)}}


def _row(payload, qid="IS-17b", elections=ELECTION):
    out = ds.assess(payload, tier="STANDARD", elections=elections)
    return (next(r for r in out["resolutions"] if r["question_id"] == qid),
            next((e for e in out["elections"] if e["question_id"] == qid), None),
            out)


def test_an_unconfigured_register_is_NOT_RUN_not_attempted_inconclusive():
    """THE regression, driven through the real `assess` with the real gap text."""
    res, _, _ = _row(_report([LIVE_PREFLIGHT_GAP, LIVE_RUNTIME_GAP]))
    assert res["state"] == ds.EvidenceState.NOT_RUN.value, (
        f"an unconfigured register was never contacted; got {res['state']} "
        f"({res.get('reason')})")
    assert "NOT searched" in res["reason"], res["reason"]
    assert "did not answer" not in res["reason"], (
        "this phrasing claims the register was asked, which it was not")


def test_the_election_ledger_says_ordered_but_NOT_SEARCHED():
    """What the reader is told to DO. `no_adapter` is R-F3408's 'ordered but not searched'
    branch; `source_failed` would send them to retry a search that cannot happen."""
    _, el, out = _row(_report([LIVE_PREFLIGHT_GAP, LIVE_RUNTIME_GAP]))
    assert el is not None
    assert el["fulfilled"] is False
    assert el["failure_kind"] == "no_adapter", (
        f"expected the ordered-but-not-searched branch, got {el['failure_kind']!r}: "
        f"{el.get('detail')}")
    assert el["billable"] is False, "a section that was never searched must not be billable"
    assert out["elections_honoured"] is False


def test_a_register_that_REALLY_failed_still_reports_source_failed():
    """The other direction must not be collateral damage: a register that WAS contacted and
    did not answer is transient and retryable, and must keep saying so."""
    res, el, _ = _row(_report([
        "CCJ register was NOT searched (http_503) — no view of County Court Judgments "
        "against this subject. An unsearched register is not a clean one."]))
    assert res["state"] == ds.EvidenceState.ATTEMPTED_INCONCLUSIVE.value, (
        f"a real 503 is attempted-and-failed, not never-attempted: {res}")
    assert el["failure_kind"] == "source_failed"


def test_a_real_ccj_finding_still_answers_the_question():
    """And the success path is untouched."""
    res, el, _ = _row(_report([], findings=[{
        "title": "2 County Court Judgment(s) on record, 1 UNSATISFIED",
        "detail": "Register of Judgments entries against Babcock...",
        "source": "registry_trust.ccj", "severity": "red", "confidence": "CONFIRMED"}]))
    assert res["state"] in (ds.EvidenceState.SINGLE_SOURCE.value,
                            ds.EvidenceState.CORROBORATED.value), res
    assert el["fulfilled"] is True and el["billable"] is True


def test_the_free_registers_are_unaffected():
    """`_register_reader` is shared. The always-attempted registers must keep treating a
    gap as attempted-and-failed, because for them it genuinely is — a Companies House gap
    means the request was made and the API did not answer."""
    payload = {"identity": {"entity_name": "X", "entity_type": "company", "findings": [],
                            "data_gaps": ["charges register did not respond (http_500)"]}}
    out = ds.assess(payload, tier="STANDARD")
    row = next(r for r in out["resolutions"] if r["question_id"] == "FS-12")
    assert row["state"] == ds.EvidenceState.ATTEMPTED_INCONCLUSIVE.value, (
        f"a CH gap is attempted-and-failed and must stay that way: {row}")


def test_no_gap_at_all_is_still_NOT_RUN_for_a_different_reason():
    """Nothing looked, nothing said. Must not be confused with either failure mode."""
    res, _, _ = _row(_report([]))
    assert res["state"] == ds.EvidenceState.NOT_RUN.value
    assert "no register result" in res["reason"], res["reason"]


@pytest.mark.parametrize("phrase", [
    "could not run", "CANNOT BE SEARCHED", "cannot be searched", "not configured",
])
def test_every_unavailability_phrase_the_engine_emits_is_recognised(phrase):
    """Producer/consumer: the reader's needles must match what dd_orchestrator actually
    writes. A needle that matches nothing silently reverts to the wrong branch."""
    res, _, _ = _row(_report([f"CCJ register — {phrase} on this run"]))
    assert res["state"] == ds.EvidenceState.NOT_RUN.value, (
        f"{phrase!r} is an unavailability phrase the orchestrator emits, but the reader "
        f"classified it as {res['state']}")


def test_the_needles_are_actually_present_in_the_orchestrator_text():
    """And the inverse check: assert the phrases exist in the emitting code, so a reworded
    gap message cannot silently orphan the needles."""
    import inspect
    from aria_service.intel import dd_orchestrator as ddo

    src = function_source(ddo, "_run_ccj_search") + function_source(ddo, "_preflight_elections")
    lowered = src.lower()
    for needle in ("could not run", "cannot be searched"):
        assert needle in lowered, (
            f"the reader keys on {needle!r} but no orchestrator gap emits it any more — "
            f"rewording the message orphans the classification")
