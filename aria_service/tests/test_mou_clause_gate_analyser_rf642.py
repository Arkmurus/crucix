"""R-F642 — MOU clause-gate analyser."""
from __future__ import annotations


# Sample text mirroring the actual Arkmurus-ADSM MOU V2.0 Clause 4.5
_ADSM_MOU_45_TEXT = """
4.5 Arkmurus shall not be required to disclose the identity of any
Introduced Party, or any other item of Protected Information, unless and until:
(a) this Agreement has been signed by ADSM;
(b) ADSM has provided a formal Request for Quotation or Letter of Intent;
(c) ADSM has confirmed in writing the final end user, the intended end use
and ADSM's role in the procurement chain;
(d) the Parties have agreed in writing the applicable Commission;
(e) Arkmurus is satisfied, acting reasonably, that the proposed Transaction
may proceed in accordance with applicable compliance, export control,
sanctions, end user and licensing requirements.
"""


def test_rf642_detects_45e_gate_in_adsm_mou():
    from aria_service.intel.mou_clause_gate_analyser import detect_gates
    gates = detect_gates(_ADSM_MOU_45_TEXT)
    assert len(gates) >= 1
    # At least one gate should reference 4.5
    labels = {g.clause_label for g in gates}
    assert any("4.5" in lbl for lbl in labels)


def test_rf642_extracts_evidence_domains_for_45e():
    from aria_service.intel.mou_clause_gate_analyser import detect_gates
    gates = detect_gates(_ADSM_MOU_45_TEXT)
    # The 4.5(e) gate touches compliance / sanctions / export control /
    # end_user / licensing — at least 4 of these should surface
    all_domains: set[str] = set()
    for g in gates:
        for d in g.evidence_domains:
            all_domains.add(d)
    assert "sanctions" in all_domains
    assert "export_control" in all_domains
    assert "end_user" in all_domains
    assert "licensing_general" in all_domains


def test_rf642_detects_sole_discretion_phrasing():
    from aria_service.intel.mou_clause_gate_analyser import detect_gates
    text = """
    Section 7.2 The Buyer may, in its sole discretion, refuse to accept
    any goods that fail to meet the sanctions and export control standards.
    """
    gates = detect_gates(text)
    assert len(gates) >= 1
    assert any("sole discretion" in g.satisfaction_language for g in gates)


def test_rf642_detects_to_its_satisfaction():
    from aria_service.intel.mou_clause_gate_analyser import detect_gates
    text = """
    8.1 The Seller shall provide compliance documentation to its satisfaction
    before any release of goods.
    """
    gates = detect_gates(text)
    assert len(gates) >= 1


def test_rf642_no_gates_in_routine_text():
    """Routine text without satisfaction-discretion language should
    yield zero gates."""
    from aria_service.intel.mou_clause_gate_analyser import detect_gates
    text = "The party shall deliver the goods within thirty days. Payment terms are net 60."
    gates = detect_gates(text)
    assert gates == []


def test_rf642_empty_or_none_safe():
    from aria_service.intel.mou_clause_gate_analyser import detect_gates
    assert detect_gates("") == []
    assert detect_gates(None) == []  # type: ignore[arg-type]


def test_rf642_render_finding_emits_orchestrator_shape():
    from aria_service.intel.mou_clause_gate_analyser import render_finding
    findings = render_finding(_ADSM_MOU_45_TEXT)
    assert len(findings) >= 1
    for f in findings:
        assert "Discretionary-satisfaction gate" in f["title"]
        assert "R-F642" in f["source"]
        assert f["confidence"] == "PROBABLE"


def test_rf642_known_patterns_enumerated():
    from aria_service.intel.mou_clause_gate_analyser import (
        list_known_satisfaction_patterns,
    )
    patterns = list_known_satisfaction_patterns()
    assert len(patterns) >= 8
    joined = " ".join(patterns)
    assert "reasonably satisfied" in joined
    assert "sole discretion" in joined


def test_rf642_dedups_overlapping_matches():
    """Same satisfaction phrase on same clause label should only
    surface once."""
    from aria_service.intel.mou_clause_gate_analyser import detect_gates
    gates = detect_gates(_ADSM_MOU_45_TEXT)
    seen = set()
    for g in gates:
        key = (g.clause_label, g.satisfaction_language)
        assert key not in seen, f"Duplicate gate {key}"
        seen.add(key)
