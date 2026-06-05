"""R-F1343 — RunPod cost-guard math. The guard that prevents a repeat of the
2026-06-05 balance-drain pod loss must never miscalculate. Pure-function tests.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "scripts", "admin"))

import runpod_cost_guard as g


def test_runway_basic():
    assert g.compute_runway(10.0, 2.0) == 5.0
    assert g.compute_runway(0.65, 2.89) < 0.25   # the actual 2026-06-05 situation
    assert g.compute_runway(5.0, 0.0) == float("inf")  # nothing burning


def test_refuses_below_balance_floor():
    ok, why = g.is_safe_to_provision(0.65, 1.49, min_balance=5.0, min_hours=2.0)
    assert ok is False
    assert "top up" in why.lower()


def test_refuses_insufficient_runway():
    # $4 balance, but floor is $3 so it passes the balance gate; runway check bites
    ok, why = g.is_safe_to_provision(4.0, 2.89, min_balance=3.0, min_hours=2.0)
    assert ok is False
    assert "runway" in why.lower()


def test_allows_when_safe():
    ok, why = g.is_safe_to_provision(20.0, 1.49, min_balance=5.0, min_hours=2.0)
    assert ok is True
    assert "OK" in why


def test_the_exact_incident_is_blocked():
    """The 2026-06-05 conditions ($0.65 balance) must be refused outright."""
    ok, _ = g.is_safe_to_provision(0.65, 2.89, min_balance=5.0, min_hours=2.0)
    assert ok is False


def test_cheapest_gpu_preference_order():
    # H100 (most expensive) must be LAST, A40 (cheapest) FIRST.
    assert g.GPU_PREFERENCE[0] == "NVIDIA A40"
    assert g.GPU_PREFERENCE[-1] == "NVIDIA H100 PCIe"
