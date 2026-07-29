"""R-F3410 — the DD Standard reaches the surface, and the layer-proxy is retired.

TWO THINGS SHIP TOGETHER, DELIBERATELY.

(1) THE CHECKLIST RENDERS. `structured_view` is the contract the online report reads.
    It now carries `dd_standard`, COMPUTED LIVE from the report exactly as
    `quality_assessment` and `decision_readiness` already are — so every report already
    persisted gains a checklist without being re-run. The scope it is judged against
    comes from the STORED `dd_scope`, because a waiver cannot be recomputed from
    evidence: "nobody screened this" and "the operator declined the screen, by name, for
    this reason" look identical in the output and are entirely different facts.

(2) THE PROXY IS RETIRED IN THE SAME CHANGE. `discipline_coverage` used to certify
    disciplines from which LAYERS RAN — a populated director list asserted
    `sanctions_screening` covered, including on runs where the screen returned
    `screened: False`. Leaving both alive would be two aggregators disagreeing about the
    same question, which is the failure CLAUDE.md §1 spent three R-numbers killing on
    the Phase A gates. So these tests assert the proxy is GONE, not merely supplemented.

The last test pins the discrepancy this change's own test run uncovered: a waiver for a
question outside the run's scope used to evaporate silently.
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

from aria_service.intel import dd_standard as S
from aria_service.intel.dd_schema import ARKDDReport, structured_view

ORCH_SRC = (Path(__file__).resolve().parents[1] / "intel" / "dd_orchestrator.py").read_text(
    encoding="utf-8")


def _report(**identity) -> dict:
    base = {"entity_type": "company", "entity_name": "Testco Ltd"}
    base.update(identity)
    return {"run_id": "dd_test", "identity": base}


# ── (1) the checklist reaches the render contract ────────────────────────────

def test_structured_view_exposes_the_standard():
    v = structured_view(_report(registration_number="1", registration_status="active"))
    assert "dd_standard" in v, "the online report has no checklist to render"
    assert v["dd_standard"]["standard_version"] == S.STANDARD_VERSION


def test_a_report_with_no_scope_still_gets_a_checklist():
    """Every historical report must gain one without a re-run."""
    v = structured_view(_report())
    s = v["dd_standard"]
    assert s["required"] > 0
    assert s["answered"] == 0
    assert s["elections_honoured"] is True     # nothing was ordered, so nothing is owed


def test_structured_view_never_raises_on_a_broken_report():
    for junk in ({}, {"identity": None}, {"identity": {"entity_type": 5}}):
        v = structured_view(junk)          # type: ignore[arg-type]
        assert "dd_standard" in v


def test_an_unbuildable_checklist_is_an_error_not_an_empty_pass():
    """An empty dict would read to a renderer as 'no open questions'.

    Patches `assess` on the real module — `from . import dd_standard` resolves through
    the package attribute, so patching sys.modules by dotted name does NOT intercept it
    (my first attempt at this test passed the real function through and proved nothing).
    """
    import unittest.mock as mock
    from aria_service.intel import dd_schema as ds
    from aria_service.intel import dd_standard as real

    with mock.patch.object(real, "assess", side_effect=RuntimeError("kaboom")):
        out = ds._dd_standard_assessment({"identity": {}})
    assert out.get("assessed") is False
    assert "error" in out and "kaboom" in out["error"]


# ── the persisted scope ──────────────────────────────────────────────────────

def test_report_carries_dd_scope_as_a_declared_field():
    """Instance attributes are dropped by asdict(); R-F591/R-F875 are the two prior
    times that silently removed a field from every JSON consumer."""
    r = ARKDDReport()
    assert "dd_scope" in r.as_dict()


def test_stored_scope_drives_the_assessment():
    rep = _report()
    rep["dd_scope"] = {"tier": "STANDARD",
                       "waivers": [{"question_id": "IS-13", "waived_by": "A. Correa",
                                    "reason": "domestic contract"}]}
    s = structured_view(rep)["dd_standard"]
    waived = [r["question_id"] for r in s["resolutions"] if r["state"] == "WAIVED"]
    assert waived == ["IS-13"]


def test_an_ordered_section_that_did_not_run_reaches_the_render_contract():
    """The operator's requirement: a selected section must never be silently absent."""
    rep = _report()
    rep["dd_scope"] = {"tier": "SIMPLIFIED",
                       "elections": [{"question_id": "IS-17b", "elected_by": "A. Correa"}]}
    s = structured_view(rep)["dd_standard"]
    assert s["elections_honoured"] is False
    assert s["elections_unfulfilled"][0]["question_id"] == "IS-17b"
    assert s["elections_unfulfilled"][0]["billable"] is False


# ── (2) the proxy is retired, not supplemented ───────────────────────────────

def test_layer_to_discipline_proxy_is_gone():
    """The specific certifications that were wrong. Asserted as ACTIVE CODE, not as
    text: the retirement comment quotes the old lines to explain the defect, so a
    substring ban would cry wolf on the documentation."""
    tree = ast.parse(ORCH_SRC)
    banned = {"identity_verification", "sanctions_screening", "pep_screening"}
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        f = node.func
        if not (isinstance(f, ast.Attribute) and f.attr in {"extend", "append"}):
            continue
        if not (isinstance(f.value, ast.Name) and f.value.id == "_covered"):
            continue
        for arg in node.args:
            lits = ([e.value for e in arg.elts if isinstance(e, ast.Constant)]
                    if isinstance(arg, ast.List) else
                    [arg.value] if isinstance(arg, ast.Constant) else [])
            assert not (banned & set(lits)), (
                f"the layer→discipline proxy is back at line {node.lineno}: {lits}. "
                f"Coverage must be derived from dd_standard.assess, or two aggregators "
                f"disagree about the same question."
            )


def test_coverage_is_derived_from_the_measured_assessment():
    assert "dd_standard.assess" in ORCH_SRC
    assert "_FUNDAMENTAL_TO_DISCIPLINES" in ORCH_SRC


def test_orchestrator_stores_the_assessment_on_the_report():
    assert "report.dd_standard = _std_assessment" in ORCH_SRC


def test_unfulfilled_election_is_pushed_into_the_data_gap_summary():
    """It must appear where the operator already looks, not only in a new key."""
    assert "ORDERED SECTION NOT DELIVERED" in ORCH_SRC


def test_fundamental_to_discipline_map_only_names_real_disciplines():
    from aria_service.intel.dd_disciplines import DD_DISCIPLINES
    tree = ast.parse(ORCH_SRC)
    named: set[str] = set()
    for node in ast.walk(tree):
        if (isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name)
                and node.target.id == "_FUNDAMENTAL_TO_DISCIPLINES"
                and isinstance(node.value, ast.Dict)):
            for v in node.value.values:
                if isinstance(v, ast.Tuple):
                    named |= {e.value for e in v.elts if isinstance(e, ast.Constant)}
    assert named, "guard is blind — the map was not found"
    unknown = sorted(named - set(DD_DISCIPLINES))
    assert not unknown, f"map names disciplines that do not exist: {unknown}"


# ── the discrepancy this change's own test run uncovered ─────────────────────

def test_a_waiver_outside_the_run_scope_is_reported_not_dropped():
    """IS-15 is ENHANCED; waiving it on a STANDARD run used to evaporate silently —
    a form that accepted an instruction and a report with no trace of it."""
    rep = _report()
    rep["dd_scope"] = {"tier": "STANDARD",
                       "waivers": [{"question_id": "IS-15", "waived_by": "A. Correa",
                                    "reason": "media sweep not purchased"}]}
    s = structured_view(rep)["dd_standard"]
    assert s["waivers_ignored"], "an unapplied waiver left no trace"
    assert s["waivers_ignored"][0]["question_id"] == "IS-15"
    assert "not in scope" in s["waivers_ignored"][0]["reason"]


def test_an_applied_waiver_is_not_listed_as_ignored():
    rep = _report()
    rep["dd_scope"] = {"tier": "STANDARD",
                       "waivers": [{"question_id": "IS-13", "waived_by": "A", "reason": "r"}]}
    s = structured_view(rep)["dd_standard"]
    assert s["waivers_ignored"] == []
