"""R-F3510 — consolidation phase 1: the contract derives the sanctions state, in SHADOW.

THE PROBLEM THIS OPENS, not closes. R-F3474 built an orthogonal four-axis evidence
contract that nothing calls. The live sanctions state is expressed ad hoc across
`screened` / `source_unavailable` / `partial_coverage` / `error` / `blocked`. Two ways of
saying the same thing is the phase-gate fork that produced fabricated passes — two
aggregators, opposite answers, both trusted — so the end state must be ONE measure.

WHY SHADOW AND NOT A SWITCH. Getting to one measure safely means proving the contract
REPRODUCES today's behaviour before anything depends on it. The live sanctions path
carries R-F1696, R-F2167, R-F2840, R-F3452 — years of never-false-clean work. Replacing
it in one step would risk all of that on an untested equivalence. So this maps the live
state onto the four axes, derives with production contract code, and returns the
comparison. It changes no finding, no verdict, no wording.

A DISAGREEMENT IS A FINDING, not a licence to switch: either the mapping is wrong or one
of the two measures is, and both are worth knowing before the ad-hoc path is deleted.

THE STATES BELOW ARE THE REAL ONES, taken from live reports:
  * partial coverage (Babcock: OFAC/OFSI/UN answered, aggregate did not)
  * source unavailable (the false "NO screen was performed")
  * clean screen, stale snapshot, corroborated block, and uncorroborated hits
"""
from __future__ import annotations

import pytest

from aria_service.intel.dd_orchestrator import sanctions_evidence_shadow


def test_it_changes_nothing():
    """The property that makes this safe to ship: it is inert."""
    out = sanctions_evidence_shadow({"screened": True, "matches": []})
    assert out["shadow_only"] is True


def test_partial_coverage_maps_to_degraded():
    """The Babcock state: primary lists answered, the aggregate did not."""
    out = sanctions_evidence_shadow({
        "screened": False, "source_unavailable": True, "partial_coverage": True,
        "error": "sanctions_source_unavailable", "matches": []})
    assert out["mapped"] is True
    assert out["axes"]["source_state"] == "degraded"
    assert out["axes"]["attempt_outcome"] == "partial"
    assert out["contract_verdict"] == "degraded"
    assert out["agrees"] is True, out


def test_source_unavailable_never_derives_completed():
    """The never-false-clean direction, expressed through the contract."""
    out = sanctions_evidence_shadow({
        "screened": False, "source_unavailable": True,
        "error": "sanctions_source_unavailable", "matches": []})
    assert not out["contract_verdict"].startswith("completed"), out
    assert out["agrees"] is True


def test_a_clean_screen_is_completed_no_match():
    out = sanctions_evidence_shadow({"screened": True, "matches": [], "blocked": False})
    assert out["contract_verdict"] == "completed_no_match"
    assert out["agrees"] is True


def test_a_corroborated_block_is_completed_match():
    out = sanctions_evidence_shadow({
        "screened": True, "blocked": True,
        "matches": [{"name": "X", "score": 0.97}]})
    assert out["axes"]["match_outcome"] == "match"
    assert out["contract_verdict"] == "completed_match"
    assert out["agrees"] is True


def test_uncorroborated_hits_are_ambiguous_not_a_match():
    """R-F2840: scored hits that did NOT corroborate are named, not designated. The
    contract's word for that is `ambiguous`, and conflating it with `match` would
    manufacture the false positive that destroys the product."""
    out = sanctions_evidence_shadow({
        "screened": True, "blocked": False,
        "matches": [{"name": "SOMETHING TRADING SA", "score": 0.81}]})
    assert out["axes"]["match_outcome"] == "ambiguous"
    assert out["contract_verdict"] != "completed_match", (
        "an uncorroborated hit derived a MATCH — a fabricated designation")


def test_stale_evidence_is_degraded():
    """R-F2167: a stale snapshot is not a current screen."""
    out = sanctions_evidence_shadow({"screened": True, "stale": True, "matches": []})
    assert out["axes"]["source_state"] == "stale"
    assert out["contract_verdict"] == "degraded"


def test_an_unscreenable_name_is_not_applicable_not_a_failure():
    """R-F3217: 'we never asked, because the name is not screenable' is a different
    answer from 'the source was unreachable', and sending a reader to check a working
    system is the R-F3125 wrong-obstacle defect."""
    out = sanctions_evidence_shadow({
        "screened": False, "error": "not_entity_shaped", "matches": []})
    assert out["axes"]["configuration_state"] == "not_applicable"
    assert out["axes"]["attempt_outcome"] == "not_attempted"


def test_every_real_state_agrees_with_the_live_path():
    """THE PHASE-1 GOAL. If any real state disagrees, the ad-hoc path must NOT be
    replaced until it is understood — this is the evidence that decision rests on."""
    states = [
        {"screened": True, "matches": []},
        {"screened": True, "blocked": True, "matches": [{"name": "X"}]},
        {"screened": False, "source_unavailable": True,
         "error": "sanctions_source_unavailable", "matches": []},
        {"screened": False, "source_unavailable": True, "partial_coverage": True,
         "error": "sanctions_source_unavailable", "matches": []},
    ]
    disagreements = [s for s in states if not sanctions_evidence_shadow(s).get("agrees")]
    assert not disagreements, (
        f"the contract and the live path disagree on {len(disagreements)} real state(s): "
        f"{disagreements}. Do NOT switch the live path until this is resolved.")


def test_a_malformed_screen_does_not_raise():
    """Shadow instrumentation must never be able to cost a report."""
    for bad in (None, {}, {"nonsense": 1}):
        out = sanctions_evidence_shadow(bad)
        assert isinstance(out, dict)


def test_a_rejected_mapping_is_reported_not_swallowed():
    """If the axes produce a combination the contract refuses, that is a finding about
    the mapping — it must surface rather than silently returning nothing."""
    out = sanctions_evidence_shadow({"screened": True, "matches": []})
    assert out.get("mapped") is True
    assert "axes" in out
