"""R-F1580 — Invariant test: every registered agent must have a contract.

This is a CROSS-REFERENCE INVARIANT test. It asserts that the set of
agent IDs passed to _register_agent() in main.py is a SUBSET of the
agent IDs passed to _register_all_contracts(). If they diverge, CI
fails immediately — preventing the class of bug where a new agent is
registered but its contract is not.

This is the executable enforcement of the pattern Claude identified:
every gap in the Deep DD was two things that must agree but nothing
forced them to. This test forces them to agree.
"""
from __future__ import annotations

import re


def test_every_registered_agent_has_a_contract():
    """Parse main.py and assert _register_agent IDs ⊆ _register_all_contracts IDs."""
    with open("aria_service/main.py", "r", encoding="utf-8") as f:
        main = f.read()

    # Extract all agent IDs from _register_agent() calls
    registered_agents: set[str] = set()
    for m in re.finditer(r'_register_agent\(\s*"([^"]+)"', main):
        registered_agents.add(m.group(1))

    # Extract all contract variable names from _register_all_contracts()
    idx = main.find("_register_all_contracts")
    assert idx >= 0, "_register_all_contracts() not found in main.py"

    # Find the for-loop tuple
    tuple_start = main.find("for _c in (", idx)
    assert tuple_start >= 0, "contract registration for-loop not found"
    tuple_end = main.find("):", tuple_start)
    tuple_section = main[tuple_start:tuple_end]

    # Extract contract variable names from the tuple
    contract_vars: set[str] = set()
    for m in re.finditer(r'_(\w+)_contract', tuple_section):
        contract_vars.add(m.group(0))  # e.g. _research_contract

    # Map contract variable names to agent IDs
    # Convention: _<agent_id_snake>_contract -> agent_id
    contracted_agents: set[str] = set()
    for var in contract_vars:
        # Strip leading _ and trailing _contract
        inner = var[1:-len("_contract")]
        # Convert snake_case to the agent_id convention
        # Most agent_ids match the snake_case directly
        mapping = {
            "research": "research_engine",
            "self_improve": "self_improve",
            "student_quiz": "student_quiz",
            "student_reading": "student_reading",
            "library_consolidation": "library_consolidation",
            "proactive_watch": "proactive_watch",
            "weekly_report": "weekly_report",
            "watchlist_rescreen": "watchlist_rescreen",
            "tender_monitor": "tender_monitor",
            "self_healing": "self_healing",
            "web_integrity": "web_integrity",
            "autonomous_scheduler": "autonomous_scheduler",
            "wiring_monitor": "wiring_monitor",
        }
        # R-F3916 — default to IDENTITY. The map exists only for the handful of
        # contract vars whose name differs from the agent id (`_research_contract`
        # -> `research_engine`); every other entry was an identity pair listed by
        # hand, so a new agent whose contract var matches its id was silently
        # treated as uncontracted. That is how `regional_snapshot` went red.
        # Identity default cannot mask a genuine mismatch: a contract named for the
        # wrong agent still leaves the registered id in `missing`.
        contracted_agents.add(mapping.get(inner, inner))

    # The invariant: every registered agent must have a contract
    missing = registered_agents - contracted_agents
    assert not missing, (
        f"Agents registered without contracts: {sorted(missing)}. "
        "Every agent in _register_agent() must also have a contract "
        "in _register_all_contracts(). Add the contract definition "
        "and include it in the registration tuple."
    )

    # Also check: no contract for an agent that isn't registered
    extra = contracted_agents - registered_agents
    if extra:
        # This is a warning, not a failure — a contract can exist for
        # a future agent that hasn't been registered yet
        import logging
        logging.getLogger("aria.tests").warning(
            "Contracts without registered agents: %s", sorted(extra)
        )
