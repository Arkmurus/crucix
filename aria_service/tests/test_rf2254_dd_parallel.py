"""R-F2254 — DD network + digital layers run CONCURRENTLY (dd-reviewer #1 speed fix).

Safe because both only READ report.identity + write DISJOINT sections; compliance
(which MUTATES report.identity) runs SERIALLY before them. Source-contract locks so a
future edit can't reintroduce the serial chain or move a layer into the race window.
"""
from __future__ import annotations
from pathlib import Path

_DDO = (Path(__file__).resolve().parent.parent / "intel" / "dd_orchestrator.py").read_text(encoding="utf-8")


def test_network_and_digital_run_in_a_gather():
    assert "async def _run_network_layer" in _DDO
    assert "async def _run_digital_layer" in _DDO
    assert "await asyncio.gather(_run_network_layer(), _run_digital_layer(), return_exceptions=True)" in _DDO


def test_gather_is_gated_off_hard_stop():
    # the concurrent block must not run on a sanctions short-circuit
    i_gather = _DDO.index("_run_network_layer(), _run_digital_layer()")
    guard = _DDO.rindex("if not hard_stop:", 0, i_gather)
    # the two closure defs sit between the guard and the gather (~2.9k chars), so allow slack
    assert guard != -1 and (i_gather - guard) < 4000, "gather must be under an 'if not hard_stop' guard"


def test_compliance_runs_serial_BEFORE_the_concurrent_readers():
    # compliance mutates report.identity, so it must complete before the concurrent
    # identity-readers start (its await sits above the gather).
    i_comp = _DDO.index("_run_compliance(target, report)")
    i_gather = _DDO.index("await asyncio.gather(_run_network_layer()")
    assert i_comp < i_gather, "compliance must run before the network||digital gather"


def test_no_serial_network_await_remains_outside_the_closure():
    # the OLD serial 'await asyncio.wait_for(_run_network(...' should now live ONLY
    # inside _run_network_layer (exactly one call site).
    assert _DDO.count("await asyncio.wait_for(_run_network(target, report)") == 1
