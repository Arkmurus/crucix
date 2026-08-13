"""R-F3953 / C-44 — one of the three GREEN→AMBER confidence triggers was dead code.

The confidence gate in `_run_synthesis` refuses to issue GREEN when ARIA could
not actually verify the entity. It has three triggers: registry substance,
data-gap count, and unresolved ghost indicators. The data-gap one could never
fire, for two compounding reasons:

    dd_orchestrator.py:10573
        _total_gaps = len(report.data_gaps_summary) if hasattr(...) else 0
        if not hasattr(report, "data_gaps_summary"):
            _total_gaps = sum(len(s.data_gaps) for s in (...))   # ← DEAD

`data_gaps_summary` is a dataclass field with `default_factory=list`
(dd_schema.py:699), so `hasattr` is unconditionally True and the fallback —
the branch that actually counted anything — was unreachable.

And the list it did read is populated only in `_assemble_bluf`
(10988-11344), which runs AFTER `_run_synthesis` (10319-10948). So at gate
time it was always empty and `_total_gaps` was always 0.

Net effect: **a company with 15 unresolved data gaps still got GREEN**, as
long as its registry status was live and its ghost score was clean. The only
existing test touching this hardcodes the reason string and never exercises
the computation.

The fix counts what is actually on the report at gate time — the per-section
gaps, which is precisely what the dead branch intended — unioned with the
summary for when this is ever called later in the pipeline.
"""
from __future__ import annotations

import pytest

from aria_service.intel import dd_orchestrator as DD
from aria_service.intel.dd_schema import ARKDDReport

from ._source_probe import function_code


def _report_with_section_gaps(n: int) -> ARKDDReport:
    rep = ARKDDReport()
    rep.identity.entity_type = "company"
    for i in range(n):
        rep.digital.data_gaps.append(f"gap {i}")
    return rep


# ── the premise the dead branch got wrong ────────────────────────────────────

def test_data_gaps_summary_always_exists_so_hasattr_is_not_a_test():
    """This is why the fallback was unreachable."""
    rep = ARKDDReport()
    assert hasattr(rep, "data_gaps_summary")
    assert rep.data_gaps_summary == []


# ── the counter ──────────────────────────────────────────────────────────────

def test_counts_section_gaps_when_the_summary_is_still_empty():
    """Gate time. The summary is empty; the sections are where the gaps live."""
    rep = _report_with_section_gaps(4)
    assert rep.data_gaps_summary == []
    assert DD._count_unresolved_gaps(rep) == 4


def test_counts_across_every_section_not_just_digital():
    rep = ARKDDReport()
    rep.identity.data_gaps.append("no registry")
    rep.network.data_gaps.append("no ownership")
    rep.compliance.data_gaps.append("no sanctions source")
    assert DD._count_unresolved_gaps(rep) == 3


def test_summary_is_included_when_it_has_been_populated():
    rep = _report_with_section_gaps(2)
    rep.data_gaps_summary.append("a summary-only gap")
    assert DD._count_unresolved_gaps(rep) == 3


def test_the_same_gap_in_both_places_is_counted_once():
    """`_assemble_bluf` copies section gaps into the summary — do not double-count."""
    rep = ARKDDReport()
    rep.digital.data_gaps.append("identical gap text")
    rep.data_gaps_summary.append("identical gap text")
    assert DD._count_unresolved_gaps(rep) == 1


def test_empty_report_counts_zero():
    assert DD._count_unresolved_gaps(ARKDDReport()) == 0


def test_counter_never_raises_on_a_malformed_report():
    class _Broken:
        pass
    assert DD._count_unresolved_gaps(_Broken()) == 0
    assert DD._count_unresolved_gaps(None) == 0


# ── the gate, driven for real ────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_green_with_many_gaps_is_overridden_to_amber(monkeypatch):
    """The capability test: a GREEN verdict with 5 unresolved gaps must not stand.

    Registry substance is satisfied deliberately, so the ONLY trigger that can
    fire is the data-gap one. Pre-fix this returned GREEN.
    """
    from aria_service.intel.dd_schema import RiskClassification

    rep = _report_with_section_gaps(5)
    rep.risk_classification = RiskClassification.GREEN.value
    # satisfy the registry-substance trigger so it cannot be the cause
    rep.identity.registration_status = "active"
    rep.identity.incorporation_date = "2011-04-02"
    rep.identity.directors = [{"name": "A Director", "is_current": True}]
    # and the ghost trigger
    rep.identity.ghost_score = {"total": 2, "indicators": []}

    await DD._run_synthesis({"name": "Gappy Ltd", "type": "company"}, rep)

    assert rep.risk_classification == RiskClassification.AMBER_LIGHT.value, (
        "5 unresolved data gaps still issued a GREEN clearance"
    )
    assert rep.confidence_gate_triggered is True
    assert any("data gap" in r.lower() for r in rep.confidence_gate_reasons), (
        f"the data-gap trigger did not fire: {rep.confidence_gate_reasons}"
    )


@pytest.mark.asyncio
async def test_green_with_two_gaps_still_green(monkeypatch):
    """The threshold is >= 3. Two gaps must not trip it — a gate that always
    fires is as useless as one that never does."""
    from aria_service.intel.dd_schema import RiskClassification

    rep = _report_with_section_gaps(2)
    rep.risk_classification = RiskClassification.GREEN.value
    rep.identity.registration_status = "active"
    rep.identity.incorporation_date = "2011-04-02"
    rep.identity.directors = [{"name": "A Director", "is_current": True}]
    rep.identity.ghost_score = {"total": 2, "indicators": []}

    await DD._run_synthesis({"name": "Tidy Ltd", "type": "company"}, rep)
    assert rep.risk_classification == RiskClassification.GREEN.value


# ── the dead branch must not come back ───────────────────────────────────────

def test_the_hasattr_fallback_is_gone():
    src = function_code(DD, "_run_synthesis")
    assert 'hasattr(report, "data_gaps_summary")' not in src, (
        "the unreachable hasattr branch is back — it can never be False for a "
        "dataclass field with default_factory"
    )
    assert "_count_unresolved_gaps(" in src, (
        "the confidence gate no longer routes through the one gap counter"
    )
