"""R-F2406 — LIVE grounding repair (chat_ep + stream) via maybe_repair_grounding.

Measured live 2026-07-04: with the grounding contract ON, DeepSeek formatted
citations well but over-asserted — it tagged [CONFIRMED] on training-knowledge
facts NOT in the retrieved sources; the honesty judge caught ~8/11 as
unsupported → grounded_rate 0.27. R-F2406 runs the judge's support check on the
shipped answer and, when a tool-backed tagged turn is weakly grounded,
REGENERATES ONCE with a stricter demote-or-drop contract and ships the repaired
answer — keeping the repair ONLY if it genuinely grounds better.

These capability tests drive maybe_repair_grounding() (the real decision path)
with the judge + regenerate stubbed, and assert the integrity properties the
design requires. verify_response runs FOR REAL so grounded_rate is computed
honestly from the (stubbed) support verdict.
"""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

import pytest

from aria_service.routes import aria as aria_mod


def _run(coro):
    return asyncio.run(coro)


def _j(n_claims, supported, status="ok"):
    return {"status": status, "claims": [f"c{i}" for i in range(n_claims)],
            "supported_count": supported, "verdicts": []}


_TOOL_CTX = "Snippet #1: OFAC SDN lists the entity.\n\nSnippet #2: EU lists it under 833/2014."
# Over-asserted answer: many [CONFIRMED] tags, tool ran.
_OVER = ("The entity is on the OFAC SDN list (OFAC) [CONFIRMED]. It was designated in 2014 "
         "[CONFIRMED]. It is on the BIS Entity List (BIS) [CONFIRMED]. Its CEO is Ivan Petrov "
         "[CONFIRMED].")
# Repaired answer: keeps CONFIRMED only for source-backed facts.
_REPAIRED = ("The entity is on the OFAC SDN list (OFAC) [CONFIRMED]. It is listed by the EU "
             "under Regulation 833/2014 (EU) [CONFIRMED]. Its designation date and CEO are not "
             "supported by the provided sources.")


def _patches(judge_side_effect, regen_return):
    return [
        patch.object(aria_mod, "_grounding_markers_enabled", return_value=True),
        patch("aria_service.intel.honesty_judge.judge_response",
              new=AsyncMock(side_effect=judge_side_effect)),
        patch.object(aria_mod, "_regenerate_with_stricter_grounding",
                     new=AsyncMock(return_value=regen_return)),
    ]


def test_repair_fires_and_grounded_rate_rises_honestly():
    """Over-asserted turn (judge finds 1/4 supported → 0.25) → repair regenerates;
    repaired answer is judged 2/2 supported → 1.0. grounded_rate rises because the
    shipped answer now only CONFIRMEDs source-backed facts."""
    judge = [_j(4, 1), _j(2, 2)]  # original: 0.25 ; repaired: 1.0
    ps = _patches(judge, _REPAIRED)
    for p in ps: p.start()
    try:
        out = _run(aria_mod.maybe_repair_grounding(object(), "q", _TOOL_CTX, _OVER))
    finally:
        for p in ps: p.stop()
    assert out["repaired"] is True
    assert out["response"] == _REPAIRED
    assert out["verification"]["grounded_rate"] == 1.0          # rose from 0.25
    assert out["judgment"]["supported_count"] == 2              # judgment is for the SHIPPED answer


def test_already_grounded_turn_is_not_repaired():
    """A genuinely grounded turn (>= threshold) → no regenerate, unchanged."""
    regen = AsyncMock(return_value="SHOULD NOT BE USED")
    ps = [
        patch.object(aria_mod, "_grounding_markers_enabled", return_value=True),
        patch("aria_service.intel.honesty_judge.judge_response",
              new=AsyncMock(return_value=_j(4, 4))),   # 1.0 >= 0.7
        patch.object(aria_mod, "_regenerate_with_stricter_grounding", new=regen),
    ]
    for p in ps: p.start()
    try:
        out = _run(aria_mod.maybe_repair_grounding(object(), "q", _TOOL_CTX, _OVER))
    finally:
        for p in ps: p.stop()
    assert out["repaired"] is False
    assert out["response"] == _OVER
    regen.assert_not_awaited()   # zero extra LLM calls beyond the one gating judge


def test_repair_not_kept_if_it_does_not_ground_better_anti_inflation():
    """If the regenerate is ALSO unsupported (no honest lift) → keep the original,
    never fabricate an improvement."""
    judge = [_j(4, 0), _j(3, 0)]   # original 0.0 ; repaired 0.0 → no lift
    ps = _patches(judge, _REPAIRED)
    for p in ps: p.start()
    try:
        out = _run(aria_mod.maybe_repair_grounding(object(), "q", _TOOL_CTX, _OVER))
    finally:
        for p in ps: p.stop()
    assert out["repaired"] is False
    assert out["response"] == _OVER


def test_short_circuits_when_flag_off():
    judge = AsyncMock()
    ps = [
        patch.object(aria_mod, "_grounding_markers_enabled", return_value=False),
        patch("aria_service.intel.honesty_judge.judge_response", new=judge),
    ]
    for p in ps: p.start()
    try:
        out = _run(aria_mod.maybe_repair_grounding(object(), "q", _TOOL_CTX, _OVER))
    finally:
        for p in ps: p.stop()
    assert out["repaired"] is False and out["judgment"] is None
    judge.assert_not_awaited()   # zero LLM calls when disabled


def test_short_circuits_on_no_tool_and_no_tags():
    judge = AsyncMock()
    with patch.object(aria_mod, "_grounding_markers_enabled", return_value=True), \
         patch("aria_service.intel.honesty_judge.judge_response", new=judge):
        # no tool_context
        out1 = _run(aria_mod.maybe_repair_grounding(object(), "q", "", _OVER))
        # tool ran but answer has NO confidence tags
        out2 = _run(aria_mod.maybe_repair_grounding(object(), "q", _TOOL_CTX,
                                                    "plain answer, no tags"))
    assert out1["repaired"] is False and out2["repaired"] is False
    judge.assert_not_awaited()   # zero LLM calls on both short-circuits


def test_contract_tighten_confirmed_only_if_in_sources():
    """The framing contract must explicitly forbid CONFIRMED on facts not in sources."""
    from aria_service.intel import source_verifier as sv
    framed = sv.frame_tool_context_for_citation("OFAC lists X.\n\nEU lists X.")
    low = framed.lower()
    assert "only a fact that is actually stated in the snippets may be marked confirmed" in low
    assert "never confirmed" in low
