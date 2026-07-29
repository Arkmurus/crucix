"""R-F3422 — the three free Companies House registers actually run in a DD.

R-F3404 BUILT the adapters (charges, insolvency, disqualified officers) and proved them
against the live register. Nothing called them. This wires them into `_run_identity`, so
fundamentals #11 (insolvency), #12 (charges) and #16 (disqualification) stop being
answerable only from two profile BOOLEANS — `has_charges` / `has_insolvency_history` —
and from a check that had never been performed at all.

WHAT THE BOOLEANS COULD NOT SAY. Whether a debenture sits over the assets a buyer is
about to pay for, who holds it, when an insolvency happened or what kind it was. And
`disqualified-directors` appeared exactly once in the whole tree: as a domain fragment in
an adverse-media allowlist.

THE INVARIANT UNDER TEST. For all three, an EMPTY result is a real finding — no charges,
no insolvency, no disqualification — and an empty result is a finding ONLY when the
register answered. So `checked: False` must always become a data gap and must never
produce a clean line. That is the same never-false-clean rule R-F3229/R-F3397 enforce
elsewhere, applied to three new registers at once.

Plus the name-match discipline: the disqualification register matches on NAME ALONE, so
a hit is a CANDIDATE, never an identification (the R-F3089 class, about a named human
being).
"""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

import pytest

from aria_service.intel import companies_house as ch
from aria_service.intel import dd_orchestrator as ddo
from aria_service.intel.dd_schema import ARKDDReport


def _run(coro):
    return asyncio.run(coro)


def _report(directors=None) -> ARKDDReport:
    r = ARKDDReport()
    r.identity.entity_name = "Testco Ltd"
    r.identity.registration_number = "04300718"
    r.identity.directors = directors if directors is not None else []
    return r


def _drive(report, *, charges=None, insolvency=None, disq=None):
    charges = charges if charges is not None else {"checked": True, "total_count": 0,
                                                   "outstanding_count": 0, "items": []}
    insolvency = insolvency if insolvency is not None else {"checked": True, "case_count": 0,
                                                            "cases": []}
    disq = disq if disq is not None else {"checked": True, "total_results": 0,
                                          "candidates": [], "match_basis": "name_only"}
    with patch.object(ch, "get_charges", AsyncMock(return_value=charges)), \
         patch.object(ch, "get_insolvency", AsyncMock(return_value=insolvency)), \
         patch.object(ch, "search_disqualified_officers", AsyncMock(return_value=disq)):
        _run(ddo._run_ch_free_registers(report, {"company_number": "04300718"}))
    return report


def _titles(r):
    return [f.title for f in r.identity.findings]


def _gaps(r):
    return " ".join(r.identity.data_gaps)


# ── it is wired into the identity layer at all ───────────────────────────────

def test_the_identity_layer_calls_it():
    import inspect
    assert "_run_ch_free_registers(report, profile)" in inspect.getsource(ddo._run_identity), (
        "the registers are built but nothing runs them — the R-F3404 state this fixes"
    )


def test_no_company_number_is_a_no_op_not_a_crash():
    r = _report()
    r.identity.registration_number = ""
    _run(ddo._run_ch_free_registers(r, {}))
    assert r.identity.findings == [] and r.identity.data_gaps == []


# ── #12 charges ──────────────────────────────────────────────────────────────

def test_outstanding_charges_are_reported_with_the_secured_party():
    r = _drive(_report(), charges={
        "checked": True, "total_count": 2, "outstanding_count": 1,
        "items": [{"status": "outstanding", "persons_entitled": ["Barclays Bank plc"]}],
        "source_url": "https://example/charges"})
    f = next(f for f in r.identity.findings if "outstanding charge" in f.title)
    assert f.severity == "amber"
    assert "Barclays Bank plc" in f.detail
    assert f.source_tier == "OFFICIAL"


def test_zero_charges_is_a_finding_because_the_register_answered():
    r = _drive(_report())
    assert any("No outstanding charges" in t for t in _titles(r))


def test_unreachable_charges_register_is_a_gap_not_a_clean_line():
    r = _drive(_report(), charges={"checked": False, "reason": "rate limit exhausted"})
    assert not any("No outstanding charges" in t for t in _titles(r)), (
        "a register we could not reach produced a clean line"
    )
    assert "Charges register NOT checked" in _gaps(r)
    assert "not a clear result" in _gaps(r)


# ── #11 insolvency ───────────────────────────────────────────────────────────

def test_insolvency_cases_are_red_and_name_the_kind():
    r = _drive(_report(), insolvency={
        "checked": True, "case_count": 1,
        "cases": [{"type": "creditors-voluntary-liquidation"}],
        "source_url": "https://example/insolvency"})
    f = next(f for f in r.identity.findings if "insolvency case" in f.title)
    assert f.severity == "red"
    assert "creditors voluntary liquidation" in f.detail


def test_no_insolvency_is_a_finding_because_404_is_an_answer():
    """Companies House returns 404 for a solvent company; R-F3404 makes that an ANSWER,
    and this asserts the DD reports it as one."""
    r = _drive(_report(), insolvency={"checked": True, "case_count": 0, "cases": [],
                                      "detail": "No insolvency case is recorded"})
    assert any("No insolvency case" in t for t in _titles(r))


def test_unreachable_insolvency_register_is_a_gap():
    r = _drive(_report(), insolvency={"checked": False, "reason": "timed out"})
    assert not any("No insolvency case" in t for t in _titles(r))
    assert "Insolvency register NOT checked" in _gaps(r)


# ── #16 disqualified officers — a name is not an identity ────────────────────

def test_a_disqualification_hit_is_a_candidate_never_a_determination():
    r = _drive(_report(directors=[{"name": "HOWARD, Justin"}]), disq={
        "checked": True, "total_results": 2, "match_basis": "name_only",
        "corroboration_required": "Matched on NAME ONLY. Confirm date of birth and address.",
        "candidates": [{"title": "Justin HOWARD", "address_snippet": "Liverpool"}],
        "source_url": "https://example/dq"})
    f = next(f for f in r.identity.findings if "disqualified" in f.title.lower())
    assert f.severity == "amber", "a name match was raised to red — that is an accusation"
    assert "NAME MATCH" in f.title and "identity NOT confirmed" in f.title
    assert "not a determination" in f.detail
    assert f.confidence == "ASSESSED"


def test_a_clean_disqualification_check_leaves_a_TRACE_not_silence():
    """R-F3426 CORRECTION. This test originally asserted the opposite — that a clean
    check stays SILENT, "no finding needed". That was my own assumption and it was
    wrong: with no finding and no gap, `dd_standard` read IS-16b as NOT_RUN on a run
    where every officer HAD been searched. A clean check reported as an unrun one is
    certify-by-absence inverted, and it understates every honest report.

    So the property is a TRACE, not silence — one summary finding, and still no gap
    (nothing failed)."""
    r = _drive(_report(directors=[{"name": "Jane Q Public"}]))
    assert any("disqualified-directors register" in t for t in _titles(r)), (
        "a clean check left no trace — indistinguishable from never having looked"
    )
    assert "NOT performed" not in _gaps(r), "a successful check must not leave a gap"


def test_unreachable_disqualification_check_is_a_gap():
    r = _drive(_report(directors=[{"name": "Jane Q Public"}]),
               disq={"checked": False, "reason": "rate limit exhausted"})
    assert "Disqualification check 'Jane Q Public' NOT performed" in _gaps(r)


def test_resigned_officers_are_not_checked():
    r = _drive(_report(directors=[{"name": "Former Director", "resigned_on": "2020-01-01"}]),
               disq={"checked": True, "total_results": 9, "candidates": []})
    assert not any("disqualif" in t.lower() for t in _titles(r))


# ── a failing register must never cost the report ────────────────────────────

@pytest.mark.parametrize("fn", ["get_charges", "get_insolvency"])
def test_a_raising_register_becomes_a_gap_not_an_exception(fn):
    r = _report()
    kw = {"charges": None, "insolvency": None}
    with patch.object(ch, "get_charges", AsyncMock(side_effect=RuntimeError("boom")
                                                   if fn == "get_charges" else None,
                                                   return_value={"checked": True,
                                                                 "total_count": 0,
                                                                 "outstanding_count": 0,
                                                                 "items": []})), \
         patch.object(ch, "get_insolvency", AsyncMock(side_effect=RuntimeError("boom")
                                                      if fn == "get_insolvency" else None,
                                                      return_value={"checked": True,
                                                                    "case_count": 0,
                                                                    "cases": []})), \
         patch.object(ch, "search_disqualified_officers",
                      AsyncMock(return_value={"checked": True, "total_results": 0,
                                              "candidates": []})):
        _run(ddo._run_ch_free_registers(r, {"company_number": "04300718"}))
    assert "NOT checked" in _gaps(r)
