"""R-F2951 — the boot state-regression detector must not read a still-loading
neural graph as a -100% regression (which resets the Phase A gate-#3 streak).

The neural graph loads via an async incremental boot warmup (~10 min), so an
early-boot `neural_memory.get_stats()` returns total_edges=0 UNTIL `_loaded`
flips True. R-F251 diffs boot counters and `logger.error(...)` on any >5% drop;
that error is mirrored (error_log_handler → record_error("log:error")) into the
gate-#3 error ledger, where `log:error` is a reset type (error_streak.py:94) —
so every deploy falsely reset the 7-day clean streak. Live 2026-07-23:
"[R-F251] STATE REGRESSION DETECTED — neural_edges: 1242156 → 0 (-100.0%)".

Fix: get_stats() reports a `loaded` flag; the R-F251 snapshot emits a
non-numeric "loading" for neural_edges while not loaded, so the numeric-only
diff skips it. A genuine drop-to-0 (loaded=True) is still caught.
"""
from __future__ import annotations

import asyncio

from aria_service.intel import neural_memory as nm


def _run(coro):
    return asyncio.run(coro)


def test_get_stats_reports_loaded_false_before_warmup(monkeypatch):
    monkeypatch.setattr(nm, "_neurons", {})      # nothing loaded yet
    monkeypatch.setattr(nm, "_loaded", False)
    stats = _run(nm.get_stats())
    assert stats["total_edges"] == 0
    assert stats["loaded"] is False, "an unloaded graph must report loaded=False"


def test_get_stats_reports_loaded_true_when_populated(monkeypatch):
    monkeypatch.setattr(nm, "_neurons", {"n1": {"category": "c", "activation": 1.0,
                                                "label": "x", "confidence": 1.0,
                                                "evidence_count": 1, "id": "n1"}})
    monkeypatch.setattr(nm, "_edges", {"n1": {"n2": 1.0}})
    monkeypatch.setattr(nm, "_loaded", True)
    monkeypatch.setattr(nm, "_meta", {"born": 1_700_000_000.0, "total_activations": 0})
    stats = _run(nm.get_stats())
    assert stats["loaded"] is True
    assert stats["total_edges"] == 1


def _snapshot_neural_edges(loaded: bool, total_edges):
    """Replicates main.py's R-F2951 snapshot decision exactly."""
    nm_stats = {"loaded": loaded, "total_edges": total_edges}
    if nm_stats.get("loaded", True):
        return nm_stats.get("total_edges", "n/a")
    return "loading"


def _regression_flagged(cur_val, prv_val) -> bool:
    """Replicates main.py's R-F251 numeric-only diff."""
    if isinstance(cur_val, (int, float)) and isinstance(prv_val, (int, float)):
        return prv_val > 0 and cur_val < prv_val * 0.95
    return False


def test_loading_neural_edges_does_not_trip_the_regression():
    cur = _snapshot_neural_edges(loaded=False, total_edges=0)   # still warming up
    assert cur == "loading"
    assert _regression_flagged(cur, 1242156) is False, \
        "a still-loading neural graph must NOT be read as a regression (no gate-#3 reset)"


def test_genuine_neural_edges_loss_is_still_caught():
    cur = _snapshot_neural_edges(loaded=True, total_edges=0)    # loaded AND truly 0
    assert cur == 0
    assert _regression_flagged(cur, 1242156) is True, \
        "a REAL drop to 0 (loaded=True) must still be flagged — the fix must not blind us"


def test_normal_edge_count_is_not_a_regression():
    cur = _snapshot_neural_edges(loaded=True, total_edges=1242156)
    assert _regression_flagged(cur, 1242156) is False
