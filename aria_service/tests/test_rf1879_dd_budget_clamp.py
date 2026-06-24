"""R-F1879 — DD verification + synthesis must be budget-CLAMPED, not fixed.

Live 2026-06-24: a DD with a 240s budget overran to the 390s hard deadline
('impl overran on unclamped work'). Root cause: every heavy layer clamped its
timeout to the remaining budget via _clamp(), but the final two layers —
verification and synthesis — used FIXED 30s / 10s timeouts that ignored the
budget entirely. Fix: heavy layers clamp to (budget − synthesis reserve);
verification + synthesis clamp to the full budget (the reserved tail) via
_clamp_final(). Both are now bounded; neither is an unclamped fixed timeout.

This is a control-flow guarantee, verified statically (the nested clamp closures
can't be unit-driven without standing up the whole orchestrator).
"""
from __future__ import annotations

import inspect

import aria_service.intel.dd_orchestrator as dd


def test_verification_and_synthesis_are_budget_clamped():
    src = inspect.getsource(dd._orchestrate_dd_impl)
    # the old unclamped fixed timeouts must be gone
    assert "timeout=30)" not in src, "verification still uses a fixed 30s timeout (unclamped)"
    assert "timeout=10)" not in src, "synthesis still uses a fixed 10s timeout (unclamped)"
    # both final layers must clamp to the reserved tail
    assert "_run_verification(target, report), timeout=_clamp_final(" in src
    assert "_run_synthesis(target, report), timeout=_clamp_final(" in src
    # the reserve + heavy deadline machinery must exist
    assert "_SYNTH_RESERVE_S" in src and "_heavy_deadline" in src
    assert "def _clamp_final" in src


def test_heavy_clamp_uses_reserved_deadline():
    """Heavy-layer _clamp must clamp to _heavy_deadline (budget − reserve), so
    synthesis always has its reserve."""
    src = inspect.getsource(dd._orchestrate_dd_impl)
    # _clamp computes from _heavy_deadline; _clamp_final from _overall_deadline
    assert "rem = _heavy_deadline - time.time()" in src
    assert "rem = _overall_deadline - time.time()" in src
