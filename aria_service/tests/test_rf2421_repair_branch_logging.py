"""R-F2421 — maybe_repair_grounding names its decision branch (`reason`).

Measured live 2026-07-04: every turn shipped repaired=False, which looked like
the repair never fires. Diagnosis via the branch `reason` shows this is largely
CORRECT, not a bug:
  - Rosoboronexport recorded grounded_rate 0.7778 (>= 0.7 threshold) → the safety
    net correctly SKIPS (skip_already_grounded); the lift was genuine first-pass.
  - zero-source turns (grounded_rate 0.0, tool returned nothing) → the repair
    fires but cannot ground without sources → kept_original_no_lift.
The repair only flips repaired=True on a weak-BUT-improvable turn (0 < gr < 0.7
AND the sources support a better re-synthesis).

These tests drive maybe_repair_grounding() and assert the `reason` for each
branch — so the logs (and this contract) make the behaviour observable.
"""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

from aria_service.routes import aria as aria_mod


def _run(coro):
    return asyncio.run(coro)


def _j(n, supported, status="ok"):
    return {"status": status, "claims": [f"c{i}" for i in range(n)],
            "supported_count": supported, "verdicts": []}


_CTX = "Snippet #1: OFAC lists X.\n\nSnippet #2: EU lists X."
_TAGGED = "X is on the OFAC list (OFAC) [CONFIRMED]. X is designated [CONFIRMED]."


def _patches(judge_side_effect, regen_return="REPAIRED [CONFIRMED]", flag=True):
    return [
        patch.object(aria_mod, "_grounding_markers_enabled", return_value=flag),
        patch("aria_service.intel.honesty_judge.judge_response",
              new=AsyncMock(side_effect=judge_side_effect)),
        patch.object(aria_mod, "_regenerate_with_stricter_grounding",
                     new=AsyncMock(return_value=regen_return)),
    ]


def _call(judge_side_effect, **kw):
    ps = _patches(judge_side_effect, **kw)
    for p in ps: p.start()
    try:
        return _run(aria_mod.maybe_repair_grounding(object(), "q", _CTX, _TAGGED, session_id="t"))
    finally:
        for p in ps: p.stop()


def test_reason_skip_already_grounded():
    """gr >= 0.7 → repaired=False is CORRECT (safety net not needed)."""
    out = _call([_j(2, 2)])              # 1.0 >= 0.7
    assert out["reason"] == "skip_already_grounded"
    assert out["repaired"] is False


def test_reason_kept_original_no_lift_zero_source():
    """Weak first pass (0.0) but the regenerate also can't ground (no sources) →
    kept_original_no_lift, NOT a bug."""
    out = _call([_j(2, 0), _j(2, 0)])   # orig 0.0, repaired 0.0
    assert out["reason"] == "kept_original_no_lift"
    assert out["repaired"] is False


def test_reason_repaired_improved():
    """Weak first pass that the re-synthesis genuinely improves → repaired_improved."""
    out = _call([_j(4, 1), _j(2, 2)])   # orig 0.25, repaired 1.0
    assert out["reason"] == "repaired_improved"
    assert out["repaired"] is True


def test_reason_kept_original_regen_empty():
    """Repair fires but the constrained re-synthesis returns empty → keep original."""
    out = _call([_j(4, 0)], regen_return="")
    assert out["reason"] == "kept_original_regen_empty"
    assert out["repaired"] is False


def test_reason_skip_grounded_rate_none():
    """Judge produced no numeric support (no_source) → nothing to score, skip."""
    out = _call([_j(1, 0, status="no_source")])
    assert out["reason"] == "skip_grounded_rate_none"
    assert out["repaired"] is False


def test_reason_skip_precondition_when_flag_off():
    out = _call([_j(2, 2)], flag=False)
    assert out["reason"] == "skip_precondition"
    assert out["repaired"] is False


def test_reason_skip_no_tags():
    ps = _patches([_j(2, 2)])
    for p in ps: p.start()
    try:
        out = _run(aria_mod.maybe_repair_grounding(object(), "q", _CTX,
                                                   "plain answer no tags", session_id="t"))
    finally:
        for p in ps: p.stop()
    assert out["reason"] == "skip_no_confidence_tags"
    assert out["repaired"] is False
