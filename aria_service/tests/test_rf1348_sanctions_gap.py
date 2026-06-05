"""R-F1348 — a DD sanctions screen that FAILS must never be silently dropped.

Audit 2026-06-05 P0-3: dd_orchestrator caught screen exceptions, logged at
DEBUG, and `continue`d — no data_gap, no record. The DD report then rendered
clean for screening that never ran (a compliance false negative the operator
cannot see). Fix: _note_dd_screen_gap surfaces every failure as a visible
data_gap. These tests drive that helper directly.
"""
import os
import sys
import types

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from aria_service.intel import dd_orchestrator as dd


def _fake_report():
    r = types.SimpleNamespace()
    r.identity = types.SimpleNamespace(data_gaps=[])
    return r


def test_screen_failure_surfaces_a_visible_data_gap():
    r = _fake_report()
    dd._note_dd_screen_gap(r, "Osama bin Laden", RuntimeError("screen timeout"))
    assert len(r.identity.data_gaps) == 1
    gap = r.identity.data_gaps[0]
    assert "Osama bin Laden" in gap
    assert "NOT screened" in gap          # operator-visible: screening did NOT run
    assert "MANUAL" in gap                # tells the operator what to do


def test_never_raises_on_malformed_report():
    # Best-effort: a report missing .identity must not blow up the DD run.
    broken = types.SimpleNamespace()  # no .identity
    dd._note_dd_screen_gap(broken, "Some Entity", ValueError("x"))  # must not raise


def test_long_name_and_exc_are_truncated():
    r = _fake_report()
    dd._note_dd_screen_gap(r, "Z" * 500, RuntimeError("E" * 500))
    gap = r.identity.data_gaps[0]
    assert len(gap) < 300            # bounded (name[:80] + exc[:100] + wrapper)
    assert "Z" * 80 in gap and "Z" * 81 not in gap   # name capped at 80
    assert "E" * 100 in gap and "E" * 101 not in gap  # exc capped at 100
