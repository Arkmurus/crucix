"""Capability test: verify the training_export judge gate.

R-F1458: the judge gate filters out factually incorrect examples before
they enter the training corpus. This test verifies:
1. The gate passes all examples when disabled (default)
2. The gate rejects empty/near-empty answers
3. The gate handles judge API failures gracefully
"""
import pytest
import os
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from aria_service.learning import training_export as te


@pytest.mark.asyncio
async def test_judge_gate_disabled_passes_all():
    """When the judge gate is disabled (default), all examples pass through."""
    examples = [
        {"user": "What is the capital of France?", "assistant": "Paris."},
        {"user": "What is 2+2?", "assistant": "4."},
    ]
    # Gate is disabled by default (ARIA_TRAINING_JUDGE_GATE=0)
    result = await te._apply_judge_gate(examples)
    assert len(result) == 2, "Disabled gate must pass all examples"


@pytest.mark.asyncio
async def test_judge_gate_no_api_key_returns_all():
    """Without a judge API key, the gate passes all examples through."""
    examples = [
        {"user": "Test question?", "assistant": "Test answer."},
    ]
    result = await te._apply_judge_gate(examples)
    assert len(result) == 1


@pytest.mark.asyncio
async def test_judge_example_empty_answer():
    """Empty answers should be scored as 'wrong' without calling the judge."""
    result = await te._judge_example("Question?", "")
    assert result.get("verdict") == "wrong"
    assert result.get("ok") is True
    assert result.get("score") == 0.0


@pytest.mark.asyncio
async def test_judge_example_no_api_key():
    """Without API key, judge returns unscored gracefully for non-empty answers.
    
    Note: when DEEPSEEK_API_KEY is set in the environment, the judge will
    actually grade the answer (returning 'correct' for a factually right answer).
    This test handles both cases.
    """
    result = await te._judge_example("What is the capital of France?", "The capital of France is Paris, which is known for the Eiffel Tower.")
    # Either unscored (no key) or correct (key available, answer is right)
    assert result.get("verdict") in ("unscored", "correct"), f"Unexpected verdict: {result.get('verdict')}"
    if result.get("verdict") == "unscored":
        assert result.get("ok") is False
    else:
        assert result.get("ok") is True


def test_judge_gate_disabled_by_default():
    """The judge gate must be OFF by default (backward compatible)."""
    assert te._JUDGE_GATE_ENABLED is False, (
        "Judge gate must be disabled by default. "
        "Set ARIA_TRAINING_JUDGE_GATE=1 to enable."
    )
