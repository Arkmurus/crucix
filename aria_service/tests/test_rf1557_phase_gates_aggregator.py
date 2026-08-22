"""R-F1557 — /phase/gates aggregator must call functions that ACTUALLY EXIST.

Bug: gate #1 called `autonomy_scorer.composite_score()` and gate #2 called
`autonomy_scorer.heatmap()` — neither function exists. Each raised
AttributeError which the per-gate `except Exception` swallowed, so gates #1 and
#2 silently reported status "unknown" and the operator's Phase-A dashboard was
blind to the two headline gates.

Capability test: invoke the REAL endpoint (phase_gates_ep) and assert gates #1
and #2 are not "unknown" purely because the underlying function is missing. Per
CLAUDE.md §3c this drives the broken path, not a helper.
"""
import asyncio

# R-F2784 (2026-07-19): these tests drove the real endpoint via
# `asyncio.get_event_loop().run_until_complete(...)`, which raises
# "no current event loop" on Python 3.10+ when no loop is running in the thread
# — the traceback fired BEFORE the endpoint, so the gate looked broken when only
# the harness was. Switched to `asyncio.run(...)`; the assertions (real
# phase_gates_ep contract) are unchanged (§23).
import pytest

from aria_service.intel import autonomy_scorer, student
from aria_service.routes.aria import phase_gates_ep


def test_real_functions_exist_old_names_gone():
    # The exact root cause: old names absent, real names present.
    assert not hasattr(autonomy_scorer, "composite_score"), (
        "composite_score() must not exist — gate #1 must use compute_composite()"
    )
    assert not hasattr(autonomy_scorer, "heatmap"), (
        "autonomy_scorer.heatmap() must not exist — gate #2 must use "
        "student.get_regional_heatmap()"
    )
    assert hasattr(autonomy_scorer, "compute_composite")
    assert hasattr(student, "get_regional_heatmap")


def test_phase_gates_gate1_resolves_not_unknown():
    """Gate #1: composite always computes (neutral-prior fallback), so the real
    endpoint must give a verdict, not 'unknown' (the old AttributeError symptom)."""
    result = asyncio.run(phase_gates_ep())
    gates = {g["id"]: g for g in result["gates"]}
    assert set(gates) == {1, 2, 3, 4, 5, 6, 7}, "all 7 gates present"
    # R-F4231 — gate #1 has a LEGITIMATE no-data 'unknown' now, exactly as gate #2
    # has had since R-F1557 (see the next test: "with an EMPTY heatmap the gate
    # legitimately returns 'unknown' — that is no-data, not the old bug"). When an
    # axis of the composite is unmeasured, the renormalised score is not comparable
    # to the full-weight target, so `unknown` is the honest verdict.
    #
    # What R-F1557 exists to catch is the OTHER unknown: compute_composite raising
    # (the AttributeError that was swallowed). That one carries `error` and
    # "FAILED" evidence and NO `unmeasured_signals`, so this still fails on it.
    if gates[1]["status"] == "unknown":
        assert gates[1].get("unmeasured_signals"), (
            f"gate #1 unknown WITHOUT a measured reason — the composite fn is not "
            f"resolving (the R-F1557 symptom): {gates[1]}"
        )
        assert not gates[1].get("error"), (
            f"gate #1 unknown because compute_composite RAISED: {gates[1]}"
        )
        assert "FAILED" not in gates[1]["evidence"]
    else:
        assert gates[1]["status"] in {"open", "closed"}, (
            f"gate #1 status is not a recognised verdict: {gates[1]}"
        )
    assert "compute_composite" in gates[1]["evidence"]
    assert "get_regional_heatmap" in gates[2]["evidence"]


def test_phase_gates_gate2_floor_verdict(monkeypatch):
    """Gate #2: with real heatmap data the endpoint must compute the floor (min
    mastery cell) and verdict — proving the path no longer AttributeErrors and
    the aggregation is correct. Patches the function that previously didn't exist.

    Note: with an EMPTY heatmap (no mastery data, e.g. a fresh local store) the
    gate legitimately returns 'unknown' — that is no-data, not the old bug. So we
    drive it with controlled data to assert the verdict."""
    async def _fake_open():
        return {
            "heatmap": {"sanctions": {"RU": 0.82, "IR": 0.55}},  # floor 0.55 < 0.70
            "floor_breach_cells": [{"topic": "sanctions", "region": "IR", "score": 0.55}],
            "gate_2_floor_target": 0.70,
        }

    async def _fake_closed():
        return {
            "heatmap": {"sanctions": {"RU": 0.82, "IR": 0.91}},  # floor 0.82 ≥ 0.70
            "floor_breach_cells": [],
            "gate_2_floor_target": 0.70,
        }

    monkeypatch.setattr(student, "get_regional_heatmap", _fake_open)
    res_open = asyncio.run(phase_gates_ep())
    g2_open = next(g for g in res_open["gates"] if g["id"] == 2)
    assert g2_open["status"] == "open"
    assert g2_open["value"] == 0.55
    assert g2_open["floor_breach_cells"]

    monkeypatch.setattr(student, "get_regional_heatmap", _fake_closed)
    res_closed = asyncio.run(phase_gates_ep())
    g2_closed = next(g for g in res_closed["gates"] if g["id"] == 2)
    assert g2_closed["status"] == "closed"
    assert g2_closed["value"] == 0.82


def test_gate5_reads_aria_prefixed_env(monkeypatch):
    """Gate #5 must honour the ARIA_-prefixed live secret names, not bare ones."""
    monkeypatch.setenv("ARIA_AUTONOMOUS_ENABLED", "1")
    monkeypatch.setenv("ARIA_OUTPUT_HARVEST_ENABLED", "1")
    monkeypatch.setenv("ARIA_AUTONOMY_LEVEL", "3")
    # Ensure bare names are NOT what closes the gate.
    monkeypatch.delenv("AUTONOMOUS_ENABLED", raising=False)
    monkeypatch.delenv("HARVEST_ENABLED", raising=False)
    monkeypatch.delenv("AUTONOMY_LEVEL", raising=False)

    result = asyncio.run(phase_gates_ep())
    g5 = next(g for g in result["gates"] if g["id"] == 5)
    assert g5["status"] == "closed", f"gate #5 should close on ARIA_ names: {g5}"
    assert g5["value"]["missing"] == []


def test_gate5_open_when_unset(monkeypatch):
    for n in (
        "ARIA_AUTONOMOUS_ENABLED", "AUTONOMOUS_ENABLED",
        "ARIA_OUTPUT_HARVEST_ENABLED", "HARVEST_ENABLED",
        "ARIA_AUTONOMY_LEVEL", "AUTONOMY_LEVEL",
    ):
        monkeypatch.delenv(n, raising=False)
    result = asyncio.run(phase_gates_ep())
    g5 = next(g for g in result["gates"] if g["id"] == 5)
    assert g5["status"] == "open"
    assert len(g5["value"]["missing"]) == 3


if __name__ == "__main__":
    import sys
    sys.exit(pytest.main([__file__, "-v"]))
