"""R-F2665 — gate #1 must not falsely certify ≥71% on LOW-confidence (thin) evidence.

Pre-R-F2665, gate #1 `pass` was pure threshold: `cs >= 0.71` with NO confidence gate.
compute_composite renormalises over MEASURED signals only, so when verification (45%)
and honesty (25%) have <5 samples in 24h they drop out and the composite becomes
mastery ALONE at confidence 0.30. A mastery-only 0.71 would then FALSELY close gate #1
with ARIA's honesty/grounding axis (the moat) entirely unmeasured.

R-F2665: pass requires `cs >= 0.71 AND not low_confidence` (confidence >= MIN 0.60).
Drives the REAL compute_phase_gates(); verified to FAIL against the pre-fix tree.
"""
from __future__ import annotations

import asyncio

import pytest

from aria_service.intel import phase_gates
from aria_service.intel import autonomy_scorer


def _run(coro):
    # Loop-safe (don't poison sibling suites that use the legacy get_event_loop idiom).
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()
        asyncio.set_event_loop(asyncio.new_event_loop())


def _gate1(monkeypatch, score, confidence, low_confidence):
    async def _c():
        return {"composite_score": score, "confidence": confidence,
                "low_confidence": low_confidence}
    monkeypatch.setattr(autonomy_scorer, "compute_composite", _c)
    res = _run(phase_gates.compute_phase_gates())
    return res["gates"]["gate_1_composite"]


def test_high_score_low_confidence_does_not_pass(monkeypatch):
    """THE fix: 0.75 at confidence 0.30 (mastery-only, honesty axis unmeasured) must
    NOT certify gate #1."""
    g = _gate1(monkeypatch, 0.75, 0.30, True)
    assert g["pass"] is False, (
        "gate #1 must NOT pass on a low-confidence score — thin evidence (R-F2665)")
    assert g["value"] == 0.75 and g["low_confidence"] is True


def test_high_score_high_confidence_passes(monkeypatch):
    """0.75 with real confidence (both honesty signals present) → honestly passes."""
    g = _gate1(monkeypatch, 0.75, 0.85, False)
    assert g["pass"] is True, f"a well-measured 0.75 should close gate #1: {g}"


def test_below_threshold_never_passes(monkeypatch):
    """Below 0.71 never passes, even at full confidence."""
    g = _gate1(monkeypatch, 0.60, 0.95, False)
    assert g["pass"] is False


if __name__ == "__main__":
    import sys
    sys.exit(pytest.main([__file__, "-v"]))
