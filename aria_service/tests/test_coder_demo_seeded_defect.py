"""R-F1859 — reproduce test for the controlled first-gold demonstration.

This test is RED against the seeded bug in
aria_service/coder_demo/seeded_defect.py (the upper percentage bound is not
clamped). The autonomous coder must fix `clamp_percentage` so this goes
FAIL-on-unfixed -> PASS-on-fixed (the gold-gate requirement, R-F1685). Remove
together with the sandbox module once first gold is verified.
"""
from aria_service.coder_demo.seeded_defect import clamp_percentage


def test_clamps_above_100():
    # RED on the seeded bug: returns 150.0 instead of clamping to 100.0.
    assert clamp_percentage(150.0) == 100.0


def test_clamps_below_0():
    assert clamp_percentage(-5.0) == 0.0


def test_passthrough_in_range():
    assert clamp_percentage(42.0) == 42.0
