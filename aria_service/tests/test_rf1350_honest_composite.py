"""R-F1350 — the autonomy/honesty composite must MEASURE honesty, not flatter.

Wave-5 finding: predictor_gate (20% weight) = 1.0 - blocks_24h/10, but
blocks_24h is written only by the autonomous task-block loop, so in normal
operation it is a permanent 1.0 = a +0.20 constant unrelated to honesty —
inflating Phase A gate #1's composite. Fix: predictor is override-only now;
its 20% went to verification + honesty; the composite renormalises over
signals with REAL data (no 0.5 padding) and reports its own confidence.
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

from aria_service.intel import autonomy_scorer as sc


@pytest.fixture
def mock_signals(monkeypatch):
    """Patch the four signal sources + redis persistence to fixed values."""
    state = {"mastery": 0.5, "grounded": 0.5, "honesty": 0.5, "blocks": 0}

    async def _mastery():
        return {"headline_mastery": state["mastery"], "topics": {}, "weak_topics": []}

    async def _verif():
        return {"avg_grounded_rate": state["grounded"], "rate_sample_size": 100}

    async def _honesty():
        return {"avg_honesty_score": state["honesty"], "scored_sample_size": 100}

    class _RS:
        async def get(self, k):
            return str(state["blocks"]) if "blocks" in k else None
        async def set_json(self, *a, **k): return None
        async def get_json(self, *a, **k): return []
    rs = _RS()

    import aria_service.intel.student as _s
    import aria_service.intel.source_verifier as _sv
    import aria_service.intel.honesty_judge as _hj
    import aria_service.intel.redis_store as _rs
    import aria_service.intel.engine_wiring as _ew
    monkeypatch.setattr(_s, "get_mastery_report", _mastery)
    monkeypatch.setattr(_sv, "get_verification_stats", _verif)
    monkeypatch.setattr(_hj, "get_honesty_stats", _honesty)
    monkeypatch.setattr(_rs, "get", rs.get)
    monkeypatch.setattr(_rs, "set_json", rs.set_json)
    monkeypatch.setattr(_rs, "get_json", rs.get_json)
    monkeypatch.setattr(_ew, "wire_success", lambda **k: None)
    return state


def test_zero_blocks_no_longer_inflates_by_020(mock_signals):
    """The core fix: all real signals at 0.5 + zero predictor blocks must give
    composite 0.5 — NOT 0.6 (the old +0.20 predictor constant)."""
    r = asyncio.run(sc.compute_composite())
    assert r["composite_score"] == pytest.approx(0.5, abs=0.001)
    assert "predictor_gate" not in r["signals"]      # removed from weights
    assert "predictor_gate" not in r["weights"]


def test_composite_tracks_real_honesty(mock_signals):
    """No constant floor: genuinely low honesty/grounding → low composite."""
    mock_signals.update(mastery=0.2, grounded=0.1, honesty=0.1)
    r = asyncio.run(sc.compute_composite())
    # weighted: 0.2*0.30 + 0.1*0.45 + 0.1*0.25 = 0.06+0.045+0.025 = 0.13
    assert r["composite_score"] == pytest.approx(0.13, abs=0.01)
    assert r["composite_score"] < 0.35  # would be NONE/LOW — honest, not flattered


def test_high_honesty_scores_high(mock_signals):
    mock_signals.update(mastery=0.9, grounded=0.9, honesty=0.9)
    r = asyncio.run(sc.compute_composite())
    assert r["composite_score"] == pytest.approx(0.9, abs=0.01)


def test_confidence_drops_when_a_signal_has_no_data(mock_signals, monkeypatch):
    """Missing signal → renormalise over present + flag low confidence, instead
    of silently padding 0.5 (which flattered the number)."""
    async def _no_verif():
        return {"avg_grounded_rate": None, "by_verdict": {}}
    import aria_service.intel.source_verifier as _sv
    monkeypatch.setattr(_sv, "get_verification_stats", _no_verif)
    r = asyncio.run(sc.compute_composite())
    # only mastery(0.30)+honesty(0.25)=0.55 of weight present
    assert r["confidence"] == pytest.approx(0.55, abs=0.01)
    assert r["low_confidence"] is True
    assert "verification" not in r["details"]["signals_measured"]


def test_hard_override_still_fires(mock_signals):
    """Predictor remains the safety override even though it's no longer weighted."""
    mock_signals.update(mastery=0.9, grounded=0.9, honesty=0.9, blocks=6)
    r = asyncio.run(sc.compute_composite())
    assert r["tier_name"] == "NONE"
    assert r["override"] is not None and "blocked" in r["override"]
