"""R-F1926 — reproduce test for the coder first-gold canary.

This test encodes the DOCUMENTED contract of coder_canary.clamp_percentage.
It FAILS on the intentional canary bug (over-range collapses to 0.0) and PASSES
once the coder restores the contract (over-range clamps to 100.0). That
FAIL-on-unfixed -> PASS-on-fixed transition is exactly what the gold gate
(reproduce_fail_to_pass) requires, so this is the canary's reproduce test.
"""
from __future__ import annotations

from aria_service.intel.coder_canary import clamp_percentage


def test_clamp_percentage_upper_bound():
    # The canary bug: returns 0.0 for over-range instead of clamping to 100.0.
    assert clamp_percentage(150) == 100.0


def test_clamp_percentage_lower_bound():
    assert clamp_percentage(-5) == 0.0


def test_clamp_percentage_in_range():
    assert clamp_percentage(42) == 42


def test_clamp_percentage_exact_bounds():
    assert clamp_percentage(0) == 0
    assert clamp_percentage(100) == 100
