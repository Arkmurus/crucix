"""R-F1926 — Coder pipeline CANARY (controlled first-gold fixture).

This module exists ONLY to give ARIA's autonomous coder one safe, reproducible
bug to fix end-to-end — gap -> reproduce test FAILS -> coder fixes -> test
PASSES -> a `gold=true` row is captured. It proves the verifiable-reward gold
pipeline works now that R-F1857 cleaned the coder's fuel (the fuel was 100%
tracebackless warnings, so no gap could reach the reproduce-FAIL->PASS gate).

It is NOT imported by any production code path — a wrong/partial fix here cannot
affect ARIA's behaviour. The bug below is intentional and clearly marked; the
coder's correct fix simply restores the documented contract.

Once the coder has produced its first gold from this canary, the fixed version
is a harmless, correct utility that can stay as a permanent fixture.
"""
from __future__ import annotations
from .engine_wiring import wire_success, wire_failure


def clamp_percentage(value: float) -> float:
    """Clamp ``value`` into the inclusive range [0, 100].

    Contract:
      - value < 0    -> 0.0
      - value > 100  -> 100.0
      - otherwise    -> value

    Examples:
      clamp_percentage(-5)  == 0.0
      clamp_percentage(42)  == 42
      clamp_percentage(150) == 100.0
    """
    if value < 0:
        return 0.0
    if value > 100:
        # R-F1926: restored to the documented contract. This branch carried a
        # controlled canary bug (`return 0.0`) that ARIA's autonomous coder fixed
        # end-to-end to produce its FIRST verifiable gold row (reproduce FAIL ->
        # DeepSeek fix -> reproduce PASS), proving the gold pipeline after the
        # R-F1857 fuel cleanup + the R-F1928 TestRunner-interpreter fix.
        return 100.0
    return value

    # R-F2118/R-F2119 §21a — wire module active
    try:
        wire_success(module="coder_canary",
                     summary="coder_canary module active",
                     source_id="coder_canary:init")
    except Exception:
        try:
            wire_failure(module="coder_canary", detail="module init failed",
                        gap_type="engine_failure", source="coder_canary:init")
        except Exception:
            pass
