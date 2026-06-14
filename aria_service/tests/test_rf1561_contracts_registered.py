"""R-F1561 — all background-agent contracts must be REGISTERED, not just defined.

R-F1554 created AgentContract objects in main.py for research_engine, self_improve,
the student loops, proactive_watch, weekly_report, watchlist_rescreen,
tender_monitor and self_healing — but never registered them (dead local
variables). Only web_integrity's contract was wired via _register_agent(contract=).
R-F1561 adds _register_all_contracts() to actually register them.

Tests:
  1. (mechanism) registering these contracts via the REAL CONTRACT_REGISTRY makes
     them retrievable — proving the path the fix uses works.
  2. (wiring) main.py contains the _register_all_contracts wiring for all of them,
     so the fix is actually in the boot path (the closure can't be called directly).
"""
import asyncio
from pathlib import Path

import pytest

from aria_service.intel.agent_contract import AgentContract, CONTRACT_REGISTRY

_AGENT_IDS = [
    "research_engine", "self_improve", "student_quiz", "student_reading",
    "library_consolidation", "proactive_watch", "weekly_report",
    "watchlist_rescreen", "tender_monitor", "self_healing",
]


def test_contracts_register_and_are_retrievable():
    async def _run():
        for aid in _AGENT_IDS:
            c = AgentContract(
                agent_id=aid, version="1.0.0",
                directives=["test directive"], inputs=["x"], outputs=["y"],
                error_modes=["e"], dependencies=[], check_interval_s=3600,
                critical=False,
            )
            await CONTRACT_REGISTRY.register_contract(c)
        for aid in _AGENT_IDS:
            got = await CONTRACT_REGISTRY.get_contract(aid)
            assert got is not None, f"contract not retrievable for {aid}"
            assert got.agent_id == aid
    asyncio.run(_run())


def test_main_py_wires_register_all_contracts():
    src = (Path(__file__).resolve().parents[1] / "main.py").read_text(encoding="utf-8")
    assert "_register_all_contracts" in src, "main.py must wire _register_all_contracts (R-F1561)"
    assert "asyncio.create_task(_register_all_contracts())" in src, "registration task must be scheduled"
    # every defined contract must be in the registration tuple
    reg_block = src.split("_register_all_contracts")[1][:1200]
    for var in (
        "_research_contract", "_self_improve_contract", "_student_quiz_contract",
        "_student_reading_contract", "_library_consolidation_contract",
        "_proactive_watch_contract", "_weekly_report_contract",
        "_watchlist_rescreen_contract", "_tender_monitor_contract",
        "_self_healing_contract",
    ):
        assert var in reg_block, f"{var} not registered in _register_all_contracts"


if __name__ == "__main__":
    import sys
    sys.exit(pytest.main([__file__, "-v"]))
