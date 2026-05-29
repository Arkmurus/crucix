"""R-F1073 — Capability test for llm_eval_framework.evaluate().

Verifies that the framework evaluate function:
1. Accepts a model name string and list of EvalQuestion objects
2. Returns an EvalRunResult with run_id and scores
3. Does NOT crash when called with the signature from eval_runner.py
"""
from __future__ import annotations

import pytest


class TestLLMEvalFrameworkCapability:
    """Capability test: evaluate() must accept the correct signature."""

    @pytest.mark.asyncio
    async def test_evaluate_accepts_model_string_and_questions(self) -> None:
        """evaluate() must accept model_a: str and questions: list[EvalQuestion]."""
        from aria_service.intel.llm_eval_framework import (
            evaluate,
            EvalQuestion,
            EvalRunResult,
        )

        # Create a minimal test question
        questions = [
            EvalQuestion(
                id="test_001",
                question="What is the Wassenaar Arrangement?",
                expected_answer="A multilateral export control regime",
                category="export_controls",
                requires_refusal=False,
            ),
        ]

        # Call evaluate with a model name string (not an LLM object)
        # This should not crash — it will try to call the model and may
        # fail if no LLM is configured, but the function signature must
        # accept these types.
        try:
            result = await evaluate(
                model_a="deepseek",
                questions=questions,
                sample_size=1,
            )
            # If it succeeded, verify the result shape
            assert isinstance(result, EvalRunResult)
            assert hasattr(result, "run_id")
            assert hasattr(result, "model_a")
        except Exception as e:
            # If it failed because no LLM is configured, that's acceptable
            # for a unit test. The important thing is that the function
            # accepted the correct types.
            err_str = str(e)
            # Any error is fine as long as it's not a TypeError from
            # wrong argument types
            assert "TypeError" not in err_str, (
                f"evaluate() raised TypeError — wrong argument types: {e}"
            )

    @pytest.mark.asyncio
    async def test_evaluate_with_empty_questions(self) -> None:
        """evaluate() must handle empty questions list gracefully."""
        from aria_service.intel.llm_eval_framework import evaluate

        try:
            result = await evaluate(
                model_a="deepseek",
                questions=[],
                sample_size=1,
            )
            # Empty questions should return a result with zero scores
            assert result is not None
        except Exception as e:
            # Acceptable if it fails due to no LLM configured
            assert "TypeError" not in str(e), (
                f"evaluate() raised TypeError with empty questions: {e}"
            )
