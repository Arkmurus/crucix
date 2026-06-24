"""R-F1859 — controlled first-gold demonstration target (operator-approved 2026-06-24).

Isolated; imported by NOTHING in production. Exists ONLY to give the autonomous
coder a real, reproducible bug to fix end-to-end, proving the verifiable-reward
flywheel writes a gold=true row on the live system. The fix is a one-line change
(the autonomous coder is expected to produce it). Remove after first gold is
verified. See memory: coder_fuel_contamination_rf1857_2026_06_24.
"""
from __future__ import annotations


def clamp_percentage(value: float) -> float:
    """Clamp ``value`` to the inclusive percentage range [0.0, 100.0].

    A value below 0 returns ``0.0``; a value above 100 returns ``100.0``; a
    value already within the range is returned unchanged.

    Examples:
        clamp_percentage(-5.0)  -> 0.0
        clamp_percentage(42.0)  -> 42.0
        clamp_percentage(150.0) -> 100.0
    """
    if value < 0.0:
        return 0.0
    if value > 100.0:
        # BUG (seeded): the upper bound is not clamped — values above 100 are
        # returned unchanged instead of being capped at 100.0. Correct
        # behaviour per the docstring is to return 100.0.
        return value
    return value
