"""R-F452 (2026-05-13) — honest mastery capability tests.

DD audit P0 finding: pre-R-F452, every chat-response mastery update
called `student.update_mastery(topics, correct=True, weight=0.15)`
unconditionally. Headline mastery therefore tracked chat *volume*,
not chat *correctness* — the exact lie the headline-mastery rebalance
work (aria_core_mastery_topics memo) tried to fix months ago, still
present on both /chat and /chat/stream paths.

R-F452 introduces `_chat_correctness_signal()` and
`_update_mastery_honestly()` in aria_engine.py. The chat paths now
inspect the LLM's raw response_text for hedging markers ([UNVERIFIED]
≥3 times, [CONTRADICTED], [UNKNOWN], [CANNOT VERIFY]) and pass a
truthful correct/weight tuple instead of the hard-coded True. Short
or empty responses skip the update entirely.

These tests prove the user-visible symptom (mastery growing on every
chat regardless of correctness) is fixed, not just internal logic.
"""
from __future__ import annotations

import asyncio
import inspect
from unittest.mock import AsyncMock, patch

import pytest


# ── Test 1: clean response → correct=True with normal weight ────────────


def test_clean_response_marks_correct_true():
    """A normal response with no hedging markers should produce
    correct=True with the default weight. This is the path that runs
    on most chats."""
    from aria_service.aria_engine import _chat_correctness_signal

    text = (
        "Angola's defence procurement falls under [CONFIRMED] MINDEN "
        "with [ASSESSED] additional oversight from the Office of the "
        "Chief of General Staff."
    )
    correct, weight = _chat_correctness_signal(text, default_weight=0.15)
    assert correct is True
    assert weight == 0.15


# ── Test 2: 3+ UNVERIFIED tags → correct=False with reduced weight ──────


def test_hedged_response_with_many_unverified_marks_correct_false():
    """The exact pattern the headline-mastery memo flagged — LLM
    hedges on every factual claim. Pre-R-F452, this still rewarded
    mastery as correct=True. Now it penalises lightly."""
    from aria_service.aria_engine import _chat_correctness_signal

    text = (
        "[UNVERIFIED] The new MOD director was appointed in March 2026. "
        "[UNVERIFIED] The procurement budget rose to $4.2B. "
        "[UNVERIFIED] BAE Systems won the tender. "
        "[UNVERIFIED] Final delivery is set for Q3 2027."
    )
    correct, weight = _chat_correctness_signal(text, default_weight=0.15)
    assert correct is False, f"4× [UNVERIFIED] should flip correctness; got {correct}"
    # Soft-negative path uses default_weight*0.3 floor 0.03
    assert 0.03 <= weight <= 0.10, f"Soft-negative weight should be small; got {weight}"


# ── Test 3: 1 UNVERIFIED tag → still correct=True (single-tag normal) ───


def test_single_unverified_tag_does_not_flip_correctness():
    """One hedged claim in an otherwise confident reply is normal —
    threshold gates the false-positive case. Mastery still rewards."""
    from aria_service.aria_engine import _chat_correctness_signal

    text = (
        "Angola's defence framework runs through [CONFIRMED] MINDEN. "
        "The 2026 procurement plan total is [UNVERIFIED] pending the "
        "operator's confirmation. [ASSESSED] adjacent oversight from "
        "the Office of the Chief of General Staff."
    )
    correct, weight = _chat_correctness_signal(text, default_weight=0.15)
    assert correct is True


# ── Test 4: explicit CONTRADICTED marker → correct=False ────────────────


def test_contradicted_marker_flips_correctness():
    """When the LLM explicitly tags a claim [CONTRADICTED] (it found
    contradictory evidence in tool_context), mastery should not credit
    that response as correct."""
    from aria_service.aria_engine import _chat_correctness_signal

    text = (
        "The website says the company is HQ in Turkey, but the extract "
        "actually mentions Czechoslovak Group. [CONTRADICTED]"
    )
    correct, weight = _chat_correctness_signal(text, default_weight=0.15)
    assert correct is False
    assert weight <= 0.10


# ── Test 5: empty / too-short response → None (skip the update) ─────────


@pytest.mark.parametrize("text", ["", "   ", None, "ok.", "Yes."])
def test_short_or_empty_skips_update(text):
    """Responses too short to derive a verdict should return None so
    `_update_mastery_honestly` skips the call entirely. We don't want
    one-word affirmations polluting mastery in either direction."""
    from aria_service.aria_engine import _chat_correctness_signal

    correct, weight = _chat_correctness_signal(text or "", default_weight=0.15)
    assert correct is None
    assert weight == 0.0


# ── Test 6: _update_mastery_honestly skips when verdict is None ─────────


def test_update_mastery_honestly_skips_on_short_response():
    """Integration check: when the helper returns None, no call to
    student.update_mastery should fire. Wired via mock."""
    from aria_service import aria_engine as ae

    async def _run():
        with patch.object(ae.student, "update_mastery", new=AsyncMock()) as mock_update, \
             patch.object(ae.student, "update_regional_mastery", new=AsyncMock()) as mock_regional:
            await ae._update_mastery_honestly(
                topics=["lang:en"], regions=["UK"], response_text="ok",
            )
            assert mock_update.call_count == 0, (
                "Short response must skip update_mastery entirely"
            )
            assert mock_regional.call_count == 0

    asyncio.run(_run())


# ── Test 7: _update_mastery_honestly passes hedged verdict through ──────


def test_update_mastery_honestly_passes_hedged_verdict():
    """When the response is hedged, update_mastery must be called with
    correct=False, not correct=True. This is the exact lie R-F452
    fixes — verify the wire-up actually changes the call shape."""
    from aria_service import aria_engine as ae

    text = (
        "[UNVERIFIED] First claim. "
        "[UNVERIFIED] Second claim. "
        "[UNVERIFIED] Third claim. "
        "[UNVERIFIED] Fourth claim."
    )

    async def _run():
        with patch.object(ae.student, "update_mastery", new=AsyncMock()) as mock_update, \
             patch.object(ae.student, "update_regional_mastery", new=AsyncMock()) as mock_regional:
            await ae._update_mastery_honestly(
                topics=["lang:en", "sanctions"],
                regions=["UK"],
                response_text=text,
            )
            # Both update_mastery and update_regional_mastery must have
            # been called WITH correct=False
            assert mock_update.call_count == 1
            kwargs = mock_update.call_args.kwargs
            assert kwargs["correct"] is False, (
                f"Hedged response must pass correct=False; got {kwargs}"
            )
            assert mock_regional.call_count == 1
            r_kwargs = mock_regional.call_args.kwargs
            assert r_kwargs["correct"] is False, (
                f"Hedged response must pass correct=False to regional; got {r_kwargs}"
            )

    asyncio.run(_run())


# ── Test 8: empty topics list early-exits without errors ────────────────


def test_update_mastery_honestly_no_topics_no_op():
    """Defensive: empty topics list must not crash and must not call
    student.update_mastery."""
    from aria_service import aria_engine as ae

    async def _run():
        with patch.object(ae.student, "update_mastery", new=AsyncMock()) as mock_update:
            await ae._update_mastery_honestly(
                topics=[], regions=None,
                response_text="any text long enough to pass threshold here.",
            )
            assert mock_update.call_count == 0

    asyncio.run(_run())


# ── Test 9: structural — chat-response sites use the new helper ─────────


def test_aria_engine_chat_paths_use_honest_helper():
    """Structural guard: the 3 chat-response mastery sites must call
    `_update_mastery_honestly`, not `student.update_mastery(...,
    correct=True, ...)` directly. If a future merge silently reverts
    one of them, this test fires."""
    import aria_service.aria_engine as ae

    source = inspect.getsource(ae)
    # Find each chat-response block and confirm it calls the helper
    assert "_update_mastery_honestly" in source, (
        "aria_engine.py must define + call _update_mastery_honestly"
    )
    # Sanity: the pre-R-F452 hard-coded pattern should NOT appear in
    # chat-response context any more. Only legitimate uses (local
    # reasoning success in the local_attempt block, student.py internal
    # paths) remain. Match real `await student.update_mastery(...)`
    # calls, not the docstring example.
    chat_hardcoded_count = source.count("await student.update_mastery(topics, correct=True")
    # 1 legitimate site allowed: the local_attempt success block (where
    # ARIA's own local reasoning matched, which IS a correctness
    # signal). Both chat-response sites should now go via the helper.
    assert chat_hardcoded_count <= 1, (
        f"Expected at most 1 hard-coded `correct=True` await-call in "
        f"aria_engine.py (local_attempt success); got {chat_hardcoded_count}. "
        f"R-F452 was supposed to remove the chat-response ones."
    )
    # The two chat-response sites should reference the helper
    assert source.count("_update_mastery_honestly(") >= 2, (
        "At least 2 callers of _update_mastery_honestly expected "
        "(non-stream chat + stream chat); fewer suggests a site reverted"
    )
