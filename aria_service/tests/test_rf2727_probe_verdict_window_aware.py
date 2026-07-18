"""R-F2727 — Prospector finding #4: the ecosystem diagnostic probes cried FALSE FAILURES by
asserting fixed lifetime-magnitude thresholds on WINDOWED brain stats and by treating
"not signalled in the last N hours" as a hard FAIL. That confuses "not observed recently"
with "missing/broken" (an idle event-driven subsystem was branded a failure).

This drives the pure verdict helpers the probe now uses and pins the honest contract:
windowed magnitude / recency are PASS or WARN — never a hard FAIL.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "scripts"))

from probe_verdict import windowed_ok, recency_ok  # noqa: E402


def test_windowed_metric_below_floor_is_WARN_not_FAIL():
    # a quiet window (below the soft floor) must NOT be a hard failure
    assert windowed_ok(1200, 50000) is None, "below floor → WARN"
    assert windowed_ok(0, 185) is None, "empty window → WARN, not FAIL"
    # above the floor is a genuine PASS
    assert windowed_ok(60000, 50000) is True
    # absent/None is indeterminate → WARN (the 'stats broken' case is a separate check)
    assert windowed_ok(None, 50000) is None
    # a non-numeric value never explodes into a crash/FAIL
    assert windowed_ok("weird", 10) is None


def test_windowed_ok_NEVER_hard_fails():
    for v in (None, 0, 5, 10_000, "x", -3):
        assert windowed_ok(v, 100) is not False, "windowed magnitude must never be a hard FAIL"


def test_recency_stale_is_WARN_not_FAIL():
    # observed recently → PASS
    assert recency_ok(0.2, 1) is True
    assert recency_ok(3, 24) is True
    # stale → WARN (idle event-driven ≠ dead; recency alone can't tell)
    assert recency_ok(5, 1) is None
    assert recency_ok(99, 24) is None
    # no reading at all → WARN (indeterminate), never FAIL
    assert recency_ok(None, 1) is None


def test_recency_ok_NEVER_hard_fails():
    for ago in (None, 0, 0.5, 50, 99, "x"):
        assert recency_ok(ago, 1) is not False, "recency must never be a hard FAIL (not-observed ≠ broken)"
