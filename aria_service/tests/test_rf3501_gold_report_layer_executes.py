"""R-F3501 — a gold case that RUNS the report-assembly layer against frozen inputs.

R-F3482 made `release_gate_eligible` mean something for the DERIVATION contract. It did
not touch the layer where the 2026-07-30 honesty fixes actually live: sanctions coverage
reconciliation, adverse-citation coherence, decision-logic pinning. Those sixteen fixes
had unit tests and nothing that exercised them TOGETHER against a real evidence shape.

This case freezes the INPUTS to the report-assembly layer, taken from the delivered
Babcock report, and runs the REAL production functions over them. It is the first gold
case that produces findings and diffs them against expectations, rather than validating a
manifest.

SCOPE, stated so it cannot be over-read — the mistake the previous sign-off made:
this gates REPORT ASSEMBLY ONLY. It performs no retrieval and does not run the
orchestrator end to end. Full replay still needs frozen RAW source responses, which do
not exist; `gate_scope` and `gate_blocker` say so in the manifest itself, and a test below
asserts the manifest keeps saying so.

WHY THESE THREE FINDINGS. Each was a defect that reached a customer:
  * "NO screen was performed" printed beside the report's own OFAC result
  * "nothing found" printed beside the report's own citation of an FRC investigation
  * a report that could not say which rules produced it
If any regresses, this case fails with the Babcock evidence that caused it.
"""
from __future__ import annotations

import json
import pathlib

import pytest

from aria_service.intel.dd_orchestrator import (
    _adverse_citation_contradictions,
    _reconcile_sanctions_coverage,
)
from aria_service.intel.dd_schema import (
    ARKDDReport,
    Evidence,
    Finding,
    verdict_logic_status,
)

CASE = (pathlib.Path(__file__).resolve().parents[2]
        / "data" / "eval" / "dd_gold_v1" / "babcock_report_layer.json")


@pytest.fixture(scope="module")
def case() -> dict:
    assert CASE.exists(), f"gold case missing: {CASE}"
    return json.loads(CASE.read_text(encoding="utf-8"))


def _build(case: dict) -> ARKDDReport:
    """Reconstruct the report state the frozen inputs describe."""
    obs = {o["observation_id"]: o["payload"] for o in case["observations"]}
    src = dict(obs["babcock-sanctions-screen-state"])
    src.update(obs["babcock-cited-sources"])
    r = ARKDDReport()
    r.identity.entity_name = src["entity_name"]
    r.identity.sanctions_screen = dict(src["sanctions_screen"])
    prov = src["provisional_sanctions_finding"]
    r.identity.findings.append(Finding(
        severity="amber", title=prov["title"], detail=prov["detail"],
        source="sanctions.screen_with_aliases", confidence="UNCERTAIN"))
    r.identity.data_gaps.append(
        "sanctions screen did not complete — sanctions_source_unavailable "
        "(UNVERIFIED, must re-screen; not a clearance)")
    r.digital.press_coverage = [
        Evidence(source=p["source"], url=p.get("url"), source_tier="UNVERIFIED")
        for p in src["press_coverage"]
    ]
    return r


def _expected(case: dict, finding_id: str) -> str:
    for f in case["expected_findings"]:
        if f["finding_id"] == finding_id:
            return f["expected_state"]
    raise AssertionError(f"{finding_id} missing from the gold case")


def test_the_case_is_gate_eligible_and_scoped(case):
    """Gate-eligible, and honest about what it gates. R-F3482's rule still applies."""
    assert case["release_gate_eligible"] is True
    assert case["gate_scope"] == "report_assembly_only"
    # Assert the PROPERTY — the manifest still says replay is out of scope — rather than
    # an exact sentence. My first cut pinned "does not exist" against a manifest reading
    # "do not exist yet", which failed on correct content.
    blocker = case["gate_blocker"].lower()
    assert "replay" in blocker and "raw source" in blocker, (
        "the manifest must keep stating that full orchestrator replay is not covered")


def test_sanctions_partial_coverage_is_produced(case):
    """THE DEFECT: 'NO screen was performed' printed beside the report's own OFAC hit."""
    r = _build(case)
    _reconcile_sanctions_coverage(r.identity.entity_name, r)

    f = next(f for f in r.identity.findings if "Sanctions screen" in f.title)
    assert "NO screen was performed" not in f.detail, (
        "the false claim survived the assembly layer")
    for label in ("OFAC SDN", "UK OFSI", "UN Security Council"):
        assert label in f.detail, f"{label} answered but is not named"
    assert _expected(case, "sanctions-partial-not-unscreened") == "partial_coverage_declared"
    assert r.identity.sanctions_screen.get("partial_coverage") is True


def test_partial_is_still_not_a_clearance(case):
    """The direction that must never regress under any refactor."""
    r = _build(case)
    _reconcile_sanctions_coverage(r.identity.entity_name, r)
    f = next(f for f in r.identity.findings if "Sanctions screen" in f.title)
    assert f.severity == "amber"
    assert "NOT a clearance" in f.detail
    assert r.identity.sanctions_screen.get("screened") is False


def test_the_frc_investigation_contradiction_is_raised(case):
    """THE DEFECT: 'nothing found' printed beside the report's own FRC citation."""
    r = _build(case)
    hits = _adverse_citation_contradictions(r)
    assert hits, "the FRC/PwC investigation was not detected in the cited sources"
    assert any("FRC" in h["title"] for h in hits)
    assert _expected(case, "adverse-citation-contradiction-raised") == "contradiction_raised"


def test_neutral_citations_do_not_produce_a_contradiction(case):
    """The other direction: the GOV.UK and LinkedIn entries in the same frozen set must
    not be flagged, or the guard becomes noise and gets switched off."""
    r = _build(case)
    hits = _adverse_citation_contradictions(r)
    titles = " ".join(h["title"] for h in hits)
    assert "LinkedIn" not in titles
    assert "GOV.UK" not in titles


def test_the_report_pins_its_decision_logic(case):
    r = _build(case)
    st = verdict_logic_status(r)
    assert st["state"] == "current"
    assert _expected(case, "decision-logic-is-pinned") == "pinned"


def test_every_expected_finding_is_actually_checked(case):
    """A gold case whose expectations nothing executes is the skeleton R-F3482 removed.
    This asserts each declared finding_id is exercised by a test in this file."""
    src = pathlib.Path(__file__).read_text(encoding="utf-8")
    for f in case["expected_findings"]:
        fid = f["finding_id"]
        assert f'_expected(case, "{fid}")' in src, (
            f"{fid} is declared in the gold case but no test executes it")
