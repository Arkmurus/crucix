"""R-F3013 — the evidence (reliance) grade is capped by decision-readiness
completeness, so a report can never read "3 of 5 answered | evidence grade A".

Live defect (Schroder dd_fc3e2b4e824b): the evidence-DEPTH grader scored Grade A
off GLEIF/vault-enriched identity while the decision-readiness scorecard left
identity + financial UNRESOLVED (3/5). Grade A is a RELIANCE claim — it cannot
exceed what the answered decision-critical questions support. The cap lives in
_dd_quality_assessment (vault + BLUF) AND _dd_decision_readiness (header), via
shared helpers, so all three surfaces agree.
"""
from aria_service.intel.dd_schema import (
    _cap_grade, _readiness_grade_cap, _dd_quality_assessment, _dd_decision_readiness,
)


# ── cap policy (unit) ──────────────────────────────────────────────────────
def test_rf3013_readiness_cap_policy():
    assert _readiness_grade_cap(5, 5) == "A"
    assert _readiness_grade_cap(4, 5) == "B"
    assert _readiness_grade_cap(3, 5) == "C"
    assert _readiness_grade_cap(2, 5) == "C"
    assert _readiness_grade_cap(0, 5) == "C"


def test_rf3013_cap_grade_never_raises_and_spares_incomplete():
    assert _cap_grade("A", "C") == "C"      # lowered
    assert _cap_grade("B", "C") == "C"
    assert _cap_grade("C", "A") == "C"      # NEVER raised
    assert _cap_grade("D", "C") == "D"      # already below the cap
    assert _cap_grade("INCOMPLETE", "C") == "INCOMPLETE"   # withheld grade untouched
    assert _cap_grade("A", "A") == "A"


# A report whose EVIDENCE DEPTH is genuinely Grade A (score 100, no blockers).
def _grade_a_depth() -> dict:
    return {
        "identity": {
            "registration_status": "active",
            "incorporation_date": "1985-03-07",
            "registration_number": "01893220",
            "sanctions_screen": {"verified_sources": ["OFAC SDN", "UK OFSI"]},
        },
        "compliance": {"export_control": {"recommendation": "no licence required"}},
        "digital": {
            "press_coverage": [{"i": i} for i in range(8)],
            "source_tier_breakdown": {"T1": 5},
        },
        "verification": {
            "citations_checked": 5, "citations_grounded": 5, "citation_grounding_rate": 1.0,
        },
        "adverse_media": {
            "ok": True, "templates_searched": 10, "findings_count": 3,
            "search_backends_answered": True,
        },
        "confidence_gate_triggered": False,
    }


def test_rf3013_fixture_is_grade_a_without_the_cap():
    # sanity: with NO scorecard present, the depth grade really is A (else the cap
    # test below would be vacuous)
    r = _grade_a_depth()
    assert _dd_quality_assessment(r)["grade"] == "A"


def test_rf3013_quality_assessment_caps_by_persisted_scorecard():
    r = _grade_a_depth()
    r["decision_readiness"] = {"answered": 3, "required": 5}   # vault/BLUF path
    qa = _dd_quality_assessment(r)
    assert qa["grade"] == "C", "grade-A depth on a 3/5 scorecard must cap to C"
    assert any("only 3/5" in b for b in qa["blocking_reasons"])
    # a fully-answered scorecard leaves Grade A intact
    r["decision_readiness"] = {"answered": 5, "required": 5}
    assert _dd_quality_assessment(r)["grade"] == "A"


def test_rf3013_header_grade_capped_end_to_end():
    # _dd_decision_readiness answers only identity+sanctions+adverse (3/5) for this
    # fixture (no ownership, no financial) — the header evidence_grade MUST cap to C.
    r = _grade_a_depth()
    dr = _dd_decision_readiness(r)
    assert dr["answered"] == 3, "fixture answers exactly 3 of 5"
    assert dr["evidence_grade"] == "C", "header can no longer show grade A on 3/5 (was A — the bug)"


def test_rf3013_full_report_still_earns_grade_a():
    # add named ownership + a financial verdict → 5/5 answered → cap allows A
    r = _grade_a_depth()
    r["identity"]["shareholders"] = [{"name": "Parent Holdings Ltd", "kind": "corporate"}]
    r["compliance"]["financial_health"] = {"data_available": True, "health_verdict": "STRONG"}
    dr = _dd_decision_readiness(r)
    assert dr["answered"] == 5
    assert dr["evidence_grade"] == "A", "a genuinely decision-ready report must still reach A"
