"""A HARD_STOP must never ship beside a CLEAN screen of the same list in silence.

MEASURED — delivered run dd_29368fbb8b3d (2026-08-03), one document:

    Key findings : "OFAC SDN match: D.G.D. INVESTMENTS LTD."   HARD_STOP
    Compliance   : "US Treasury - OFAC - SDN List - CLEAN"

Root cause is not the renderer. R-F3219 re-screens the REGISTERED legal name once
Companies House resolves it, and overwrites screen["verified_sources"] with the
re-screen's result (dd_orchestrator ~3328). That re-screen was clean, so the
TABLE went clean — while the findings raised by the FIRST screen (the
customer-supplied name) stayed put. Table describes screen #2, findings describe
screen #1, nothing reconciles them.

detect_screen_contradictions() DETECTS. It deliberately does not adjudicate: a
sanctions HARD_STOP is never silently withdrawn because a second screen came back
clean — the first screen may be the right one. What ends is shipping both claims
without saying they disagree.

NOTE: no R-number — data/r_number_reservations.json is the peer's ledger.
"""
from __future__ import annotations

from aria_service.intel._sanctions_classify import detect_screen_contradictions


def _sources(**kw):
    return {name: {"status": status} for name, status in kw.items()}


def test_the_batsela_contradiction_is_detected():
    """The exact pair that shipped."""
    findings = [{
        "severity": "hard_stop",
        "source": "sources.ofac_sdn",
        "detail": "OFAC SDN match: D.G.D. INVESTMENTS LTD. Match score 0.85.",
    }]
    vs = {"OFAC SDN": {"status": "CLEAN"}}
    hits = detect_screen_contradictions(findings, vs)
    assert len(hits) == 1, (
        "a HARD_STOP citing OFAC SDN shipped beside 'OFAC SDN — CLEAN' and "
        "nothing objected"
    )
    assert hits[0]["list"] == "ofac sdn"


def test_a_hard_stop_on_a_list_reported_HIT_is_not_a_contradiction():
    """The normal, correct case must stay silent."""
    findings = [{
        "severity": "hard_stop",
        "source": "sources.ofac_sdn",
        "detail": "OFAC SDN match: REAL SANCTIONED ENTITY",
    }]
    assert detect_screen_contradictions(findings, {"OFAC SDN": {"status": "HIT"}}) == []


def test_an_unrelated_clean_list_does_not_trigger():
    """A HARD_STOP on OFAC must not be flagged because UN SC came back clean."""
    findings = [{
        "severity": "hard_stop",
        "source": "sources.ofac_sdn",
        "detail": "OFAC SDN match",
    }]
    vs = _sources(**{"UN Security Council Consolidated": "CLEAN"})
    assert detect_screen_contradictions(findings, vs) == []


def test_info_and_amber_findings_are_not_contradictions():
    """Only a decision-driving severity contradicts a CLEAN screen."""
    findings = [
        {"severity": "info", "source": "sources.ofac_sdn", "detail": "OFAC SDN note"},
        {"severity": "amber", "source": "sources.ofac_sdn", "detail": "OFAC SDN note"},
    ]
    assert detect_screen_contradictions(findings, {"OFAC SDN": {"status": "CLEAN"}}) == []


def test_object_findings_are_supported_not_just_dicts():
    """Findings are dataclasses in the live report."""
    class _F:
        severity = "hard_stop"
        source = "sources.ofac_sdn"
        detail = "OFAC SDN match: D.G.D. INVESTMENTS LTD."
    assert len(detect_screen_contradictions([_F()], {"OFAC SDN": {"status": "CLEAN"}})) == 1


def test_unavailable_is_not_clean():
    """An UNAVAILABLE list is unchecked, so a HARD_STOP does not contradict it."""
    findings = [{"severity": "hard_stop", "source": "sources.ofac_sdn", "detail": "OFAC SDN"}]
    assert detect_screen_contradictions(findings, {"OFAC SDN": {"status": "UNAVAILABLE"}}) == []


def test_degenerate_inputs_are_safe():
    assert detect_screen_contradictions([], {}) == []
    assert detect_screen_contradictions(None, None) == []
    assert detect_screen_contradictions([{"severity": "hard_stop"}], {"X": {"status": "CLEAN"}}) == []
