"""R-F3452 — the report said "NO screen was performed" and then printed an OFAC result.

THE CONTRADICTION, from a delivered report on Babcock International Group PLC. One
section said::

    Sanctions screen NOT performed — UNVERIFIED
    the sanctions source (OpenSanctions / primary lists) could not be reached
    (source unreachable), so NO screen was performed.

and a later section of THE SAME REPORT said::

    OFAC SDN fuzzy hit (filtered): ARIA SINA CONTROL INTERNATIONAL ...

Both were produced by the same run, and both were true of their own code path. The
aggregate screen (`sanctions.fuzzy_screen`, sanctions.py:737-780) queries OpenSanctions
and NOTHING else — `source_ok` cannot be set by any other source — so `screened` goes
false the instant that one free third-party is unreachable. Meanwhile
`_identity_primary_source_screen` had queried OFAC SDN, UK OFSI and UN SC directly and
recorded per-list availability in `_primary_snapshots`.

WHY IT MATTERS MORE THAN WORDING. The whole verdict hung on it: AMBER, NOT_CLEARED,
"Quality blocked by: sanctions screen source was unavailable or stale". "Nothing was
screened" and "the consolidated aggregate failed but three primary lists answered" send a
compliance reader to opposite actions — the first says the tool is broken, the second says
the coverage is partial and names precisely what is left to close.

WHAT IS DELIBERATELY UNCHANGED. The verdict does not improve, the AMBER override stays,
and every data gap stays. Partial coverage is NOT a clearance. Only the CLAIM changes,
from a false one to a true one that names what is still missing.
"""
from __future__ import annotations

import pytest

from aria_service.intel.dd_orchestrator import _reconcile_sanctions_coverage
from aria_service.intel.dd_schema import ARKDDReport, Finding

_PROVISIONAL_TITLE = "Sanctions screen NOT performed — UNVERIFIED"


def _report_with_failed_aggregate(snapshots: dict) -> ARKDDReport:
    """A report in exactly the state the Babcock run reached."""
    r = ARKDDReport()
    r.identity.sanctions_screen = {
        "screened": False,
        "source_unavailable": True,
        "error": "sanctions_source_unavailable",
        "source_reasons": ["timeout"],
        "primary_snapshots": dict(snapshots),
    }
    r.identity.findings.append(Finding(
        severity="amber", title=_PROVISIONAL_TITLE,
        detail=("Babcock International Group PLC — the sanctions source (OpenSanctions / "
                "primary lists) could not be reached (source unreachable), so NO screen "
                "was performed."),
        source="sanctions.screen_with_aliases", confidence="UNCERTAIN",
    ))
    r.identity.data_gaps.append(
        "sanctions screen did not complete — sanctions_source_unavailable "
        "(UNVERIFIED, must re-screen; not a clearance)")
    return r


def test_capability_the_report_no_longer_claims_nothing_was_screened():
    """THE DEFECT: three lists answered and the report said none did."""
    r = _report_with_failed_aggregate(
        {"ofac_sdn": "ok", "uk_ofsi": "ok", "un_sc": "ok", "sec_edgar": "ok"})
    _reconcile_sanctions_coverage("Babcock International Group PLC", r)

    f = next(f for f in r.identity.findings if "Sanctions screen" in f.title)
    assert "NO screen was performed" not in f.detail, "the false claim survived"
    assert f.title != _PROVISIONAL_TITLE
    for label in ("OFAC SDN", "UK OFSI", "UN Security Council"):
        assert label in f.detail, f"{label} answered but is not named"


def test_it_is_still_not_a_clearance():
    """The direction that must never regress: partial coverage is not clean."""
    r = _report_with_failed_aggregate({"ofac_sdn": "ok", "uk_ofsi": "ok", "un_sc": "ok"})
    _reconcile_sanctions_coverage("Subject Ltd", r)
    f = next(f for f in r.identity.findings if "Sanctions screen" in f.title)
    assert f.severity == "amber", "the severity must not be softened"
    assert "NOT a clearance" in f.detail
    assert "Re-screen" in f.detail
    assert r.identity.sanctions_screen.get("screened") is False, (
        "the machine-readable screened flag must stay False — this is coverage "
        "reporting, not a verdict change")


def test_what_was_not_screened_is_named():
    """A partial claim that does not say what is missing is not actionable."""
    r = _report_with_failed_aggregate({"ofac_sdn": "ok", "uk_ofsi": "unavailable"})
    _reconcile_sanctions_coverage("Subject Ltd", r)
    f = next(f for f in r.identity.findings if "Sanctions screen" in f.title)
    assert "OFAC SDN" in f.detail
    assert "NOT screened" in f.detail
    assert "UK OFSI consolidated list" in f.detail
    assert r.identity.sanctions_screen["lists_not_screened"] == ["uk_ofsi", "un_sc"]


def test_when_nothing_answered_the_original_wording_stands():
    """The honest case must not be softened into a false partial.

    This is the direction that would turn the fix into the very defect it removes.
    """
    r = _report_with_failed_aggregate(
        {"ofac_sdn": "unavailable", "uk_ofsi": "unavailable", "un_sc": "unavailable"})
    _reconcile_sanctions_coverage("Subject Ltd", r)
    f = next(f for f in r.identity.findings if "Sanctions screen" in f.title)
    assert f.title == _PROVISIONAL_TITLE, "wording changed when nothing was screened"
    assert "NO screen was performed" in f.detail
    assert not r.identity.sanctions_screen.get("partial_coverage")


def test_non_sanctions_adapters_do_not_count_as_sanctions_coverage():
    """sec_edgar, wb_debarred and acled are not sanctions lists. Counting them would be
    certification-by-something-else — the class this whole file exists to remove."""
    r = _report_with_failed_aggregate(
        {"sec_edgar": "ok", "wb_debarred": "ok", "acled": "ok",
         "ofac_sdn": "unavailable", "uk_ofsi": "unavailable", "un_sc": "unavailable"})
    _reconcile_sanctions_coverage("Subject Ltd", r)
    f = next(f for f in r.identity.findings if "Sanctions screen" in f.title)
    assert f.title == _PROVISIONAL_TITLE, (
        "a filings/debarment/conflict adapter was counted as sanctions coverage")


def test_a_genuinely_clean_aggregate_is_untouched():
    """No source_unavailable -> nothing to reconcile."""
    r = ARKDDReport()
    r.identity.sanctions_screen = {"screened": True, "matches": []}
    _reconcile_sanctions_coverage("Subject Ltd", r)
    assert "partial_coverage" not in r.identity.sanctions_screen


def test_the_data_gap_is_reconciled_too():
    """The gaps block is what a reviewer works from; leaving it saying the screen did
    not complete would recreate the contradiction one section lower."""
    r = _report_with_failed_aggregate({"ofac_sdn": "ok", "uk_ofsi": "ok", "un_sc": "ok"})
    _reconcile_sanctions_coverage("Subject Ltd", r)
    gap = next(g for g in r.identity.data_gaps if "sanctions screen" in str(g))
    assert "PARTIAL" in gap and "OFAC SDN" in gap
    assert "Not a clearance" in gap


def test_the_rendered_verdict_says_partial_not_not_screened():
    """The identity panel is a THIRD surface with its own wording. R-F1696/R-F2693 show
    these surfaces drift apart, so assert the rendered line, not just the dict."""
    from aria_service.intel.dd_schema import _sanctions_match_metric

    r = _report_with_failed_aggregate({"ofac_sdn": "ok", "uk_ofsi": "ok", "un_sc": "ok"})
    _reconcile_sanctions_coverage("Subject Ltd", r)
    line = _sanctions_match_metric(r.identity.sanctions_screen)
    assert line.startswith("PARTIAL"), line
    assert "OFAC SDN" in line
    assert "NOT SCREENED" not in line


def test_reconciliation_never_raises_on_a_malformed_screen():
    """A wording pass must not be able to fail a report."""
    r = ARKDDReport()
    r.identity.sanctions_screen = {"source_unavailable": True, "primary_snapshots": "junk"}
    _reconcile_sanctions_coverage("Subject Ltd", r)  # must not raise
    r.identity.sanctions_screen = None
    _reconcile_sanctions_coverage("Subject Ltd", r)  # must not raise
