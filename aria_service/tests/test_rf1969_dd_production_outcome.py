"""R-F1969 — DD orchestrator production outcome (§25a, capability test).

The DD orchestrator reported did-the-LAYERS-RUN (engine_wiring) but never whether
ARIA actually produced a REAL report — a confidence-gated INSUFFICIENT_EVIDENCE
report ran cleanly yet is not a real answer. R-F1969 records a delivery-grade
PRODUCTION outcome via outcome_wire (surface "dd"), so empty DDs trigger a gap
and show on the per-channel dashboard.
"""
import asyncio

from aria_service.intel.dd_orchestrator import dd_production_outcome
from aria_service.intel.outcome_wire import (
    OutcomeRecord, record_outcome, get_all_surface_health, KNOWN_SURFACES,
)


def test_real_report_is_delivered_real_answer():
    assert dd_production_outcome(all_layers_ok=True, gate_triggered=False) == ("delivered_real_answer", "")


def test_insufficient_evidence_is_a_failure():
    # All layers ran, but the confidence gate fired → NOT a real answer.
    assert dd_production_outcome(all_layers_ok=True, gate_triggered=True) == ("error", "insufficient_evidence")


def test_layer_failure_is_a_failure_with_reason():
    assert dd_production_outcome(all_layers_ok=False, gate_triggered=False, reason="2 layers errored") \
        == ("error", "2 layers errored")
    # falls back to a generic reason when none supplied
    assert dd_production_outcome(all_layers_ok=False, gate_triggered=True)[0] == "error"


def test_dd_is_a_known_surface_and_flows_to_dashboard():
    async def run():
        # A DD production outcome must roll up into the all-surfaces dashboard.
        await record_outcome(OutcomeRecord("dd", "rf1969-empty", "dd_report", "error", 100, "insufficient_evidence"))
        res = await get_all_surface_health(24)
        assert "dd" in KNOWN_SURFACES
        assert "dd" in res["surfaces"], "DD production outcomes must appear on the dashboard"
    asyncio.run(run())
