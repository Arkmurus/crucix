"""R-F3901 — two modules were absent from MODULE_GAP_TYPES, so GATE B failed 5x.

Both fell to `_default` = `agent_cycle_failure`, which is right for the autonomous
loops it was chosen for and wrong for these. THE DECORATORS WERE ALREADY CORRECT:
`brave_usage` meters a PAID SEARCH API and `search_engine_health` tracks whether
search SOURCES still answer — a failure in either is an ENGINE failure. Filing them
under the agent-cycle domain would bury a search-backend outage in agent-loop noise.

Verbatim the R-F3428 precedent, which refused to rewrite sixty vetting decorators to
match a default that did not describe them: "the tail wagging the dog".
"""
from __future__ import annotations

from aria_service.intel import wiring_harness as wh


def test_both_modules_are_registered_with_the_domain_they_actually_have():
    assert wh.get_gap_type("brave_usage") == "engine_failure"
    assert wh.get_gap_type("search_engine_health") == "engine_failure"


def test_an_unregistered_module_still_falls_to_the_default():
    """The converse control (R-F3858) — registering two modules must not weaken the
    rule for everything else, or the gate stops meaning anything."""
    assert wh.get_gap_type("a_module_that_does_not_exist") == "agent_cycle_failure"


def test_gate_b_is_clean():
    """CAPABILITY TEST — the gate that actually failed in CI, run for real."""
    results = wh.run_all_gates()
    assert results.get("gate_b") == [], results.get("gate_b")
    assert not wh.has_blocking_violations(results)
