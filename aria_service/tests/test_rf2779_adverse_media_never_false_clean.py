"""R-F2779 — adverse-media never-false-clean disclosure.

A GREEN / clean-looking DD verdict must NOT imply "no adverse media" unless a
dedicated adverse-media pass actually produced a result. Before R-F2779 the DD
verdict aggregation (dd_orchestrator._run_synthesis) never referenced adverse
media at all, and the only adverse-media honesty disclosure fired ONLY when the
whole web-search ecosystem was DEAD/DEGRADED. So an entity screened over a
WORKING web (Brave live) with no adverse-media pass — or where the pass simply
did not run — got a clean-looking verdict with the ABSENCE of adverse-media
findings silently read as clean. That is a never-false-clean breach, the exact
failure a decision-grade DD product cannot ship.

These capability tests drive the REAL _run_synthesis and assert:
  1. adverse media NOT screened  -> honest data_gap + finding is emitted.
  2. adverse media WAS screened   -> no disclosure (the gate does not over-fire).
"""
from __future__ import annotations

import asyncio


def _green_company_report(adverse_media_hits=None):
    """A clean GREEN company report with registry substance (so the OTHER
    confidence gate does not fire and isolate this behaviour). When
    adverse_media_hits is not None, plant it on web_footprint to simulate a
    completed adverse-media pass."""
    from aria_service.intel.dd_schema import ARKDDReport, RiskClassification

    report = ARKDDReport()
    report.identity.entity_name = "Globex International Trading Ltd"
    report.identity.entity_type = "company"
    report.identity.registration_status = "active"
    report.identity.incorporation_date = "2009-04-01"
    report.identity.directors = [{"name": "A. Director"}]
    report.risk_classification = RiskClassification.GREEN.value
    report.synthesis.risk_classification = RiskClassification.GREEN.value
    if adverse_media_hits is not None:
        if not isinstance(report.digital.web_footprint, dict):
            report.digital.web_footprint = {}
        report.digital.web_footprint["adverse_media_hits"] = adverse_media_hits
    return report


def _has_r2779(report) -> bool:
    gap = any("R-F2779" in str(g) for g in report.digital.data_gaps)
    finding = any(
        "Adverse-media screening incomplete" in str(f.title)
        for f in report.digital.findings
    )
    return gap and finding


def test_rf2779_discloses_when_adverse_media_not_screened():
    """No adverse-media pass ran -> the report must NOT imply clean; it must
    carry the explicit never-false-clean disclosure (gap + finding)."""
    from aria_service.intel import dd_orchestrator

    report = _green_company_report(adverse_media_hits=None)
    target = {"name": "Globex International Trading Ltd", "entity_type": "company"}
    asyncio.run(dd_orchestrator._run_synthesis(target, report))

    assert _has_r2779(report), (
        "an unscreened adverse-media surface must emit the R-F2779 never-false-clean "
        "disclosure so a clean-looking verdict does not read as adverse-media-clean"
    )


def test_rf2779_no_disclosure_when_adverse_media_screened_clean():
    """Adverse-media pass ran and returned empty (screened, genuinely no hits)
    -> the gate must NOT over-fire (ran-and-empty is not the same as not-run)."""
    from aria_service.intel import dd_orchestrator

    report = _green_company_report(adverse_media_hits=[])  # ran, zero hits
    target = {"name": "Globex International Trading Ltd", "entity_type": "company"}
    asyncio.run(dd_orchestrator._run_synthesis(target, report))

    assert not _has_r2779(report), (
        "a completed adverse-media screen (even with zero hits) must NOT trigger the "
        "incomplete-screen disclosure"
    )


def test_rf2779_no_disclosure_when_deep_followup_present():
    """The deep adverse-media follow-up blob also counts as screened."""
    from aria_service.intel import dd_orchestrator

    report = _green_company_report(adverse_media_hits=None)
    report.adverse_media = {"framework_version": "R-F159", "findings": []}
    target = {"name": "Globex International Trading Ltd", "entity_type": "company"}
    asyncio.run(dd_orchestrator._run_synthesis(target, report))

    assert not _has_r2779(report), (
        "a present, non-error deep adverse-media blob counts as screened"
    )
