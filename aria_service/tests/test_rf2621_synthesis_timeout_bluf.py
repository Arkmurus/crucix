"""R-F2621 — a synthesis timeout must NEVER produce a GREEN ("clean") BLUF.

THE BUG (proven by execution before the fix):
  * ``ARKDDReport.risk_classification`` defaults to GREEN (dd_schema.py:385/435).
  * It is only ASSIGNED at dd_orchestrator.py:6380 — INSIDE ``_run_synthesis``.
  * ``_run_synthesis`` is wrapped at :9391 in
    ``wait_for(..., timeout=_clamp_final(10))``. The except at :9392-9394 sets
    ``synthesis.meta.status = ERROR`` but NEVER touches ``risk_classification``.
  * ``_assemble_bluf()`` runs at :9427 OUTSIDE that try/except and never
    inspected the synthesis status — so it read the GREEN default and emitted
    "GREEN — passes baseline due diligence. Standard contracting path
    available." for a DD whose risk aggregation never ran, skipping the
    sanctions-unverified override at :6389.

Absence of computation must never read as "clean" (never-false-clean).

These tests drive the ACTUAL broken function (``_assemble_bluf``) with the exact
report state the timeout path leaves behind, and assert the USER-VISIBLE outcome
(the BLUF text), per CLAUDE.md §3c.
"""
import pytest

from aria_service.intel.dd_orchestrator import _assemble_bluf
from aria_service.intel.dd_schema import ARKDDReport, LayerStatus, RiskClassification


def _report_with_registry_substance(name: str = "Acme Defence Ltd") -> ARKDDReport:
    """A report whose EVIDENCE layers succeeded — registry substance present, so
    the ``_data_starved`` guard at :6857 does NOT fire and cannot mask the bug."""
    report = ARKDDReport(target={"name": name}, orchestrator_mode="deep")
    report.identity.entity_name = name
    report.identity.directors = [{"name": "J. Doe"}]
    report.identity.registration_status = "active"
    report.identity.incorporation_date = "2011-04-02"
    return report


def _is_clean_verdict(report: ARKDDReport) -> bool:
    """Did the BLUF tell the user this entity is clear to proceed?"""
    bl = (report.bottom_line or "").upper()
    return "GREEN" in bl and "INSUFFICIENT" not in bl


async def test_synthesis_timeout_never_emits_green_bluf():
    """THE CAPABILITY TEST — the symptom the operator would actually see.

    Reproduces dd_orchestrator.py:9392-9394 verbatim (status=ERROR, error set,
    risk_classification untouched) and asserts the BLUF is not a false-clean.
    """
    report = _report_with_registry_substance()
    assert report.risk_classification == RiskClassification.GREEN.value, (
        "precondition: the fail-open GREEN default is what makes this bug possible"
    )

    # exactly what the timeout except-block does, and nothing else
    report.synthesis.meta.status = LayerStatus.ERROR.value
    report.synthesis.meta.error = "timeout (budget-clamped)"

    await _assemble_bluf(report)

    assert not _is_clean_verdict(report), (
        "FALSE-CLEAN: synthesis timed out (risk aggregation never ran) yet the "
        f"BLUF told the user the entity is GREEN/clean: {report.bottom_line!r}"
    )
    assert report.risk_classification != RiskClassification.GREEN.value, (
        "risk_classification must not stay at the fail-open GREEN default when "
        "synthesis did not complete"
    )
    # the user must be told WHY the verdict is withheld (AGENTS.md §8.7: informative)
    assert any(
        w in (report.bottom_line or "").lower()
        for w in ("incomplete", "did not complete", "timed out", "partial", "time-box")
    ), f"BLUF must say the analysis was incomplete, got: {report.bottom_line!r}"
    assert report.bottom_line.strip(), "BLUF must never be empty"


async def test_synthesis_never_ran_fails_closed():
    """Defence in depth: SectionMeta.status ALSO defaults to 'ok' (dd_schema.py:75),
    so a synthesis that never ran at all leaves status='ok' + the GREEN default.
    'synthesis' missing from layers_run must therefore also fail closed."""
    report = _report_with_registry_substance("Never Ran Ltd")
    report.layers_run = ["identity", "verification"]  # synthesis absent
    assert report.synthesis.meta.status == LayerStatus.OK.value, (
        "precondition: the second fail-open default — status defaults to 'ok'"
    )

    await _assemble_bluf(report)

    assert not _is_clean_verdict(report), (
        "FALSE-CLEAN: synthesis never ran yet the BLUF claimed GREEN: "
        f"{report.bottom_line!r}"
    )


async def test_completed_green_synthesis_is_not_downgraded_to_amber_or_red():
    """NON-REGRESSION — the R-F2621 guard must not over-trigger.

    R-F2792: this test previously asserted ``_is_clean_verdict(report)`` — i.e.
    that the BLUF literally contains "GREEN". R-F2786 legitimately changed that:
    a completed GREEN synthesis whose decision-critical COVERAGE is incomplete
    now emits "🟡 NOT CLEARED — no blocking risk in the checks that completed…".

    The old assertion conflated two different things, which is exactly the
    conflation R-F2786 exists to break:
      * RISK      — what did the completed checks find?   (still GREEN here)
      * RELIANCE  — was enough collected to rely on this? (not yet)

    R-F2621's REAL invariant is that the synthesis-timeout guard must not
    over-trigger and turn a clean DD into a false AMBER/RED or an
    "INSUFFICIENT EVIDENCE" fail-closed. That invariant is asserted directly
    below — and it is STRONGER than the old text match, which never checked the
    risk classification at all. Per §23 this asserts the correct contract; it
    does not weaken the gate.
    """
    report = _report_with_registry_substance("Clean Corp Ltd")
    report.layers_run = ["identity", "verification", "synthesis"]
    report.synthesis.meta.status = LayerStatus.OK.value
    report.risk_classification = RiskClassification.GREEN.value
    report.synthesis.risk_classification = RiskClassification.GREEN.value

    await _assemble_bluf(report)

    # The guard did NOT fire: risk stays GREEN and the fail-closed marker is absent.
    assert report.risk_classification == RiskClassification.GREEN.value, (
        f"OVER-TRIGGER: completed GREEN synthesis was downgraded to "
        f"{report.risk_classification!r}"
    )
    assert "INSUFFICIENT" not in (report.bottom_line or "").upper(), (
        f"OVER-TRIGGER: the synthesis-timeout fail-closed fired on a completed "
        f"synthesis: {report.bottom_line!r}"
    )
    # And the BLUF states plainly that completed checks found no blocking risk.
    assert "NO BLOCKING RISK" in (report.bottom_line or "").upper(), (
        f"a completed GREEN synthesis must say the checks found no blocking risk: "
        f"{report.bottom_line!r}"
    )


async def test_completed_green_with_incomplete_coverage_is_not_a_clean_verdict():
    """R-F2792 — the other half of the contract, made explicit.

    GREEN risk plus INCOMPLETE decision-critical coverage must never read as
    permission to transact. This is the R-F2786 rule, pinned here next to the
    R-F2621 guard so the two can never silently drift apart again.
    """
    report = _report_with_registry_substance("Clean Corp Ltd")
    report.layers_run = ["identity", "verification", "synthesis"]
    report.synthesis.meta.status = LayerStatus.OK.value
    report.risk_classification = RiskClassification.GREEN.value
    report.synthesis.risk_classification = RiskClassification.GREEN.value

    await _assemble_bluf(report)

    assert not _is_clean_verdict(report), (
        "FALSE-CLEAN: coverage is incomplete (no adverse-media, financials, UBO "
        f"or sanctions evidence) yet the BLUF read as clean: {report.bottom_line!r}"
    )
    assert "STANDARD CONTRACTING PATH AVAILABLE" not in (report.bottom_line or "").upper(), (
        "FALSE-CLEAN: contracting language offered on incomplete coverage"
    )
    assert not (report.decision_readiness or {}).get("clearance_ready"), (
        "readiness must not be cleared when the five questions are unanswered"
    )


async def test_completed_red_synthesis_unaffected():
    """NON-REGRESSION — a real RED verdict from a completed synthesis is preserved."""
    report = _report_with_registry_substance("Bad Actor Ltd")
    report.layers_run = ["identity", "verification", "synthesis"]
    report.synthesis.meta.status = LayerStatus.OK.value
    report.risk_classification = RiskClassification.RED.value
    report.synthesis.risk_classification = RiskClassification.RED.value

    await _assemble_bluf(report)

    assert "RED" in (report.bottom_line or ""), (
        f"a completed RED verdict must survive, got: {report.bottom_line!r}"
    )
