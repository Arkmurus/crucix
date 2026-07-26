"""R-F1044 — Tests for the LLM Evaluation Framework.

Covers:
  1. Data types (EvalQuestion, PerQuestionScore, ModelResult, EvalRunResult)
  2. Correctness scoring (semantic + keyword fallback)
  3. Grounded rate scoring
  4. Refusal accuracy scoring
  5. Evidence counting
  6. Overall score computation
  7. Aggregation
  8. Regression detection
  9. Capability test
"""
from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from aria_service.intel.llm_eval_framework import (
    LLMEvalFramework,
    EvalQuestion,
    PerQuestionScore,
    ModelResult,
    EvalRunResult,
    evaluate,
)


# ════════════════════════════════════════════════════════════════════════════
# Data type tests
# ════════════════════════════════════════════════════════════════════════════

class TestDataTypes:
    def test_eval_question_defaults(self) -> None:
        q = EvalQuestion(id="q1", question="test?", expected_answer="answer")
        assert q.category == "general"
        assert not q.requires_refusal
        assert q.requires_grounding

    def test_per_question_score_defaults(self) -> None:
        s = PerQuestionScore(
            question_id="q1", model="deepseek", answer="test",
            latency_ms=100, token_count=50,
        )
        assert s.correctness == 0.0
        assert s.grounded_rate == 0.0
        assert s.refusal_accuracy == 1.0
        assert s.overall == 0.0
        assert s.error is None

    def test_model_result_defaults(self) -> None:
        m = ModelResult(
            model="deepseek", questions_attempted=0, questions_passed=0,
            avg_correctness=0.0, avg_grounded_rate=0.0,
            avg_refusal_accuracy=0.0, avg_latency_ms=0.0,
            avg_token_count=0.0, overall_score=0.0,
        )
        assert m.per_question == []
        assert m.regressions == []

    def test_eval_run_result_defaults(self) -> None:
        m = ModelResult(
            model="deepseek", questions_attempted=0, questions_passed=0,
            avg_correctness=0.0, avg_grounded_rate=0.0,
            avg_refusal_accuracy=0.0, avg_latency_ms=0.0,
            avg_token_count=0.0, overall_score=0.0,
        )
        r = EvalRunResult(run_id="test", timestamp="now", model_a=m)
        assert r.model_b is None
        assert r.winner is None
        assert r.question_count == 0


# ════════════════════════════════════════════════════════════════════════════
# Correctness scoring
# ════════════════════════════════════════════════════════════════════════════

class TestCorrectnessScoring:
    @pytest.mark.asyncio
    async def test_empty_answer_returns_zero(self) -> None:
        framework = LLMEvalFramework()
        score = await framework._score_correctness("", "expected")
        assert score == 0.0

    @pytest.mark.asyncio
    async def test_empty_expected_returns_zero(self) -> None:
        framework = LLMEvalFramework()
        score = await framework._score_correctness("answer", "")
        assert score == 0.0

    @pytest.mark.asyncio
    async def test_keyword_overlap_fallback(self) -> None:
        framework = LLMEvalFramework()
        score = await framework._score_correctness(
            "Paris is the capital of France",
            "Paris is the capital city of France",
        )
        assert score > 0.0
        assert score <= 1.0

    @pytest.mark.asyncio
    async def test_no_overlap_returns_low(self) -> None:
        framework = LLMEvalFramework()
        score = await framework._score_correctness(
            "The sky is blue",
            "Python is a programming language",
        )
        assert score < 0.5


# ════════════════════════════════════════════════════════════════════════════
# Grounded rate scoring
# ════════════════════════════════════════════════════════════════════════════

class TestGroundedRateScoring:
    @pytest.mark.asyncio
    async def test_empty_answer_returns_zero(self) -> None:
        framework = LLMEvalFramework()
        score = await framework._score_grounded_rate("")
        assert score == 0.0

    @pytest.mark.asyncio
    async def test_answer_with_citations_returns_high(self) -> None:
        framework = LLMEvalFramework()
        score = await framework._score_grounded_rate(
            "Paris is the capital of France. https://en.wikipedia.org/wiki/Paris"
        )
        assert score >= 0.5

    @pytest.mark.asyncio
    async def test_answer_with_confidence_tag_and_url(self) -> None:
        framework = LLMEvalFramework()
        # Use the module-level constant
        from aria_service.intel.llm_eval_framework import _CONFIDENCE_TAG
        score = await framework._score_grounded_rate(
            f"Paris is the capital of France {_CONFIDENCE_TAG} https://example.com"
        )
        assert score > 0.0

    @pytest.mark.asyncio
    async def test_answer_with_no_evidence_returns_uncertain(self) -> None:
        framework = LLMEvalFramework()
        score = await framework._score_grounded_rate("I think Paris is the capital.")
        assert score == 0.5  # No claims, no citations


# ════════════════════════════════════════════════════════════════════════════
# Refusal accuracy
# ════════════════════════════════════════════════════════════════════════════

class TestRefusalAccuracy:
    def test_correctly_refuses(self) -> None:
        framework = LLMEvalFramework()
        score = framework._score_refusal("I cannot answer that question.", True)
        assert score == 1.0

    def test_incorrectly_answers_when_should_refuse(self) -> None:
        framework = LLMEvalFramework()
        score = framework._score_refusal("Here is the answer...", True)
        assert score == 0.0

    def test_correctly_answers(self) -> None:
        framework = LLMEvalFramework()
        score = framework._score_refusal("Here is the answer.", False)
        assert score == 1.0

    def test_incorrectly_refuses(self) -> None:
        framework = LLMEvalFramework()
        score = framework._score_refusal("I cannot answer that.", False)
        assert score == 0.0


# ════════════════════════════════════════════════════════════════════════════
# Evidence counting
# ════════════════════════════════════════════════════════════════════════════

class TestEvidenceCounting:
    def test_counts_urls(self) -> None:
        framework = LLMEvalFramework()
        count = framework._count_evidence(
            "See https://example.com and https://test.org"
        )
        assert count == 2

    def test_counts_source_markers(self) -> None:
        framework = LLMEvalFramework()
        count = framework._count_evidence(
            "[source: knowledge_base] and [source: rag_store]"
        )
        assert count == 2

    def test_no_evidence_returns_zero(self) -> None:
        framework = LLMEvalFramework()
        count = framework._count_evidence("Just a plain answer.")
        assert count == 0


# ════════════════════════════════════════════════════════════════════════════
# Overall score computation
# ════════════════════════════════════════════════════════════════════════════

class TestOverallScore:
    def test_perfect_score(self) -> None:
        framework = LLMEvalFramework()
        score = PerQuestionScore(
            question_id="q1", model="deepseek", answer="correct",
            latency_ms=100, token_count=50,
            correctness=1.0, grounded_rate=1.0, refusal_accuracy=1.0,
        )
        q = EvalQuestion(id="q1", question="test?", expected_answer="correct")
        overall = framework._compute_overall(score, q)
        assert overall == pytest.approx(1.0)

    def test_latency_penalty(self) -> None:
        framework = LLMEvalFramework()
        score = PerQuestionScore(
            question_id="q1", model="deepseek", answer="correct",
            latency_ms=15000, token_count=50,
            correctness=1.0, grounded_rate=1.0, refusal_accuracy=1.0,
        )
        q = EvalQuestion(id="q1", question="test?", expected_answer="correct")
        overall = framework._compute_overall(score, q)
        assert overall == pytest.approx(0.9)  # 1.0 - 0.1 latency penalty

    def test_zero_score(self) -> None:
        framework = LLMEvalFramework()
        score = PerQuestionScore(
            question_id="q1", model="deepseek", answer="wrong",
            latency_ms=100, token_count=50,
            correctness=0.0, grounded_rate=0.0, refusal_accuracy=0.0,
        )
        q = EvalQuestion(id="q1", question="test?", expected_answer="correct")
        overall = framework._compute_overall(score, q)
        assert overall == pytest.approx(0.1)  # Base score for attempting


# ════════════════════════════════════════════════════════════════════════════
# Aggregation
# ════════════════════════════════════════════════════════════════════════════

class TestAggregation:
    def test_empty_scores(self) -> None:
        framework = LLMEvalFramework()
        result = framework._aggregate("deepseek", [])
        assert result.questions_attempted == 0
        assert result.overall_score == 0.0

    def test_single_score(self) -> None:
        framework = LLMEvalFramework()
        scores = [
            PerQuestionScore(
                question_id="q1", model="deepseek", answer="correct",
                latency_ms=100, token_count=50,
                correctness=0.8, grounded_rate=0.9, refusal_accuracy=1.0,
                overall=0.85,
            ),
        ]
        result = framework._aggregate("deepseek", scores)
        assert result.questions_attempted == 1
        assert result.questions_passed == 1
        assert result.avg_correctness == 0.8
        assert result.avg_grounded_rate == 0.9

    def test_multiple_scores(self) -> None:
        framework = LLMEvalFramework()
        scores = [
            PerQuestionScore(
                question_id="q1", model="deepseek", answer="a",
                latency_ms=100, token_count=50,
                correctness=0.8, grounded_rate=0.9, refusal_accuracy=1.0,
                overall=0.85,
            ),
            PerQuestionScore(
                question_id="q2", model="deepseek", answer="b",
                latency_ms=200, token_count=100,
                correctness=0.6, grounded_rate=0.7, refusal_accuracy=1.0,
                overall=0.65,
            ),
        ]
        result = framework._aggregate("deepseek", scores)
        assert result.questions_attempted == 2
        assert result.questions_passed == 2  # Both >= 0.6
        assert result.avg_correctness == 0.7
        assert result.avg_grounded_rate == 0.8


# ════════════════════════════════════════════════════════════════════════════
# Regression detection
# ════════════════════════════════════════════════════════════════════════════

class TestRegressionDetection:
    @pytest.mark.asyncio
    async def test_no_baseline_sets_baseline(self) -> None:
        framework = LLMEvalFramework()
        m = ModelResult(
            model="deepseek", questions_attempted=1, questions_passed=1,
            avg_correctness=0.8, avg_grounded_rate=0.9,
            avg_refusal_accuracy=1.0, avg_latency_ms=100,
            avg_token_count=50, overall_score=0.85,
        )
        result = EvalRunResult(run_id="test", timestamp="now", model_a=m)

        with patch("aria_service.intel.redis_store") as mock_rs:
            mock_rs.get = AsyncMock(return_value=None)
            mock_rs.set = AsyncMock()
            await framework._detect_regressions(result)

            # Should have set the baseline
            mock_rs.set.assert_called_once()
            args = mock_rs.set.call_args[0]
            assert "baseline" in args[0]

    @pytest.mark.asyncio
    async def test_no_regression_with_stable_scores(self) -> None:
        framework = LLMEvalFramework()
        m = ModelResult(
            model="deepseek", questions_attempted=1, questions_passed=1,
            avg_correctness=0.8, avg_grounded_rate=0.9,
            avg_refusal_accuracy=1.0, avg_latency_ms=100,
            avg_token_count=50, overall_score=0.85,
        )
        result = EvalRunResult(run_id="test", timestamp="now", model_a=m)

        baseline = json.dumps({
            "model_a": {
                "overall_score": 0.85,
                "avg_grounded_rate": 0.9,
                "avg_correctness": 0.8,
            },
        })

        with patch("aria_service.intel.redis_store") as mock_rs:
            mock_rs.get = AsyncMock(return_value=baseline)
            mock_rs.set = AsyncMock()
            await framework._detect_regressions(result)

            assert len(result.model_a.regressions) == 0


# ════════════════════════════════════════════════════════════════════════════
# Capability test
# ════════════════════════════════════════════════════════════════════════════

class TestCapability:
    @pytest.mark.asyncio
    async def test_evaluate_returns_run_result(self) -> None:
        """Capability test: evaluate() returns a properly structured
        EvalRunResult even with minimal input."""
        questions = [
            EvalQuestion(id="q1", question="What is 2+2?", expected_answer="4"),
        ]
        result = await evaluate(
            model_a="deepseek",
            questions=questions,
            sample_size=None,  # Use all questions
        )
        assert isinstance(result, EvalRunResult)
        assert result.model_a is not None
        assert result.question_count == 1

    @pytest.mark.asyncio
    async def test_evaluate_with_ab_comparison(self) -> None:
        """Capability test: A/B comparison produces both model results."""
        questions = [
            EvalQuestion(id="q1", question="What is 2+2?", expected_answer="4"),
        ]
        result = await evaluate(
            model_a="deepseek",
            questions=questions,
            model_b="aria-llm",
            sample_size=None,
        )
        assert isinstance(result, EvalRunResult)
        assert result.model_a is not None
        assert result.model_b is not None
        # R-F3114 — this used to assert questions_attempted == 1 for BOTH arms, and
        # passed for the wrong reason: both arms were DEAD (R-F3111 — they built
        # llm_pipeline.LLMPipeline / AriaLLMProvider, neither of which exists), so
        # each returned "[ERROR: ...]" as the model's answer and that error string
        # was counted as an attempt and scored as a wrong answer.
        #
        # A question the model was never actually asked is UNMEASURED, not failed —
        # the same tri-state R-F2639 forced on the phase gates. In this environment
        # there is no provider chain and ARIA_LLM_URL is unset, so the honest count
        # is 0 measured / 1 unmeasured per arm. Do NOT "fix" a 0 here by counting
        # unasked questions again; that restores the trophy.
        for arm in (result.model_a, result.model_b):
            assert arm.questions_attempted + arm.questions_unmeasured == 1, (
                "every question must be accounted for as measured or unmeasured")
            assert arm.questions_unmeasured == 1, (
                "no provider is configured in tests, so nothing could be measured")
            assert arm.per_question and arm.per_question[0].error, (
                "the reason a question went unmeasured must be recorded, not dropped")
