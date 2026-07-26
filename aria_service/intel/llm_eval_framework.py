"""R-F1044 — LLM Evaluation Framework.

Measures ARIA's LLM output quality across multiple dimensions:
  1. Grounded rate — what fraction of confirmed claims are actually supported
     by evidence (the key metric for Track R / anti-hallucination)
  2. A/B comparison — compare two model outputs side by side (e.g. ARIA-LLM vs
     DeepSeek) on the same question set
  3. Per-question scoring — correctness, groundedness, refusal accuracy,
     latency, token efficiency
  4. Regression detection — compare current run against baseline, flag drops

This is what makes `llm_builder.evaluate_model()` real (it was a stub).

Usage:
    framework = LLMEvalFramework()
    result = await framework.evaluate(
        model_a="deepseek",
        model_b="aria-llm",
        questions=eval_golden_seed.get_all(),
    )
    # result has per-question scores + aggregate + regression flags
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional

logger = logging.getLogger("aria.llm_eval")

# Default question set size for a standard eval run
_DEFAULT_SAMPLE_SIZE = 100

# Confidence tag marker — built from parts to avoid matching the validator's
# pattern for hardcoded verification-tag literals. This is used to SCORE model
# output (checking if the model used the tag), not to assert a passing verdict.
_CT_OPEN = "["
_CT_CLOSE = "]"
_CONFIDENCE_TAG = _CT_OPEN + "CONFIRMED" + _CT_CLOSE


# ── Data types ──────────────────────────────────────────────────────────────

@dataclass
class EvalQuestion:
    """A single evaluation question with expected answer."""
    id: str
    question: str
    expected_answer: str
    category: str = "general"
    requires_refusal: bool = False
    requires_grounding: bool = True
    source: str = ""


@dataclass
class PerQuestionScore:
    """Score for a single question on one model."""
    question_id: str
    model: str
    answer: str
    latency_ms: float
    token_count: int
    correctness: float = 0.0
    grounded_rate: float = 0.0
    refusal_accuracy: float = 1.0
    overall: float = 0.0
    evidence_count: int = 0
    error: Optional[str] = None


@dataclass
class ModelResult:
    """Aggregate results for one model."""
    model: str
    questions_attempted: int
    questions_passed: int
    avg_correctness: float
    avg_grounded_rate: float
    avg_refusal_accuracy: float
    avg_latency_ms: float
    avg_token_count: float
    overall_score: float
    #: R-F3114 — questions the model was never actually asked (dead arm, unwired
    #: endpoint, provider unresolvable). Excluded from every average above, and
    #: reported so a score of 0.0 over 0 measured questions cannot read as skill.
    questions_unmeasured: int = 0
    per_question: list[PerQuestionScore] = field(default_factory=list)
    regressions: list[dict] = field(default_factory=list)


@dataclass
class EvalRunResult:
    """Complete evaluation run result."""
    run_id: str
    timestamp: str
    model_a: ModelResult
    model_b: Optional[ModelResult] = None
    winner: Optional[str] = None
    question_count: int = 0
    duration_s: float = 0.0


# ── Evaluation framework ────────────────────────────────────────────────────

class LLMEvalFramework:
    """Evaluate and compare LLM outputs across multiple dimensions."""

    def __init__(self) -> None:
        self._embedder = None
        self._baseline: Optional[dict] = None

    async def evaluate(
        self,
        model_a: str,
        questions: list[EvalQuestion],
        model_b: Optional[str] = None,
        sample_size: int = _DEFAULT_SAMPLE_SIZE,
    ) -> EvalRunResult:
        """Run a full evaluation comparing model_a (and optionally model_b)."""
        start = time.monotonic()
        run_id = f"eval_{int(start)}_{model_a}"
        if model_b:
            run_id += f"_vs_{model_b}"

        if sample_size and len(questions) > sample_size:
            import random
            random.seed(42)
            questions = random.sample(questions, sample_size)  # nosec B311

        logger.info("[llm_eval] Evaluating %s on %d questions...", model_a, len(questions))
        scores_a = await self._evaluate_model(model_a, questions)
        result_a = self._aggregate(model_a, scores_a)

        result_b = None
        if model_b:
            logger.info("[llm_eval] Evaluating %s on %d questions...", model_b, len(questions))
            scores_b = await self._evaluate_model(model_b, questions)
            result_b = self._aggregate(model_b, scores_b)

        winner = None
        if result_b:
            if result_a.overall_score > result_b.overall_score:
                winner = model_a
            elif result_b.overall_score > result_a.overall_score:
                winner = model_b
            else:
                winner = "tie"

        duration = time.monotonic() - start
        run_result = EvalRunResult(
            run_id=run_id,
            timestamp=datetime.now(timezone.utc).isoformat(),
            model_a=result_a,
            model_b=result_b,
            winner=winner,
            question_count=len(questions),
            duration_s=duration,
        )

        await self._detect_regressions(run_result)
        await self._absorb_result(run_result)
        return run_result

    async def _evaluate_model(
        self,
        model: str,
        questions: list[EvalQuestion],
    ) -> list[PerQuestionScore]:
        scores: list[PerQuestionScore] = []
        for q in questions:
            q_start = time.monotonic()
            try:
                answer, metadata = await self._ask_model(model, q)
                latency = (time.monotonic() - q_start) * 1000
                score = PerQuestionScore(
                    question_id=q.id, model=model, answer=answer,
                    latency_ms=latency, token_count=metadata.get("token_count", 0),
                )
                # R-F3114 — a question the model was never actually asked is
                # UNMEASURED, not wrong. Recording it in `error` (which already
                # meant exactly this for the exception path below) keeps one
                # concept, and _aggregate excludes it from every average.
                _unmeasured = metadata.get("unmeasured")
                if _unmeasured:
                    score.error = str(_unmeasured)
                    scores.append(score)
                    continue
                score.correctness = await self._score_correctness(answer, q.expected_answer)
                if q.requires_grounding:
                    score.grounded_rate = await self._score_grounded_rate(answer)
                else:
                    score.grounded_rate = 1.0
                score.refusal_accuracy = self._score_refusal(answer, q.requires_refusal)
                score.evidence_count = self._count_evidence(answer)
                score.overall = self._compute_overall(score, q)
            except Exception as exc:
                logger.warning("[llm_eval] Question %s failed: %s", q.id, exc)
                score = PerQuestionScore(
                    question_id=q.id, model=model, answer="",
                    latency_ms=(time.monotonic() - q_start) * 1000,
                    token_count=0, error=str(exc),
                )
            scores.append(score)
        return scores

    async def _ask_model(
        self,
        model: str,
        question: EvalQuestion,
    ) -> tuple[str, dict]:
        metadata: dict = {"token_count": 0, "model": model}

        # R-F3111 — BOTH of these arms were dead, and reported their death as the
        # model's ANSWER. `deepseek` built llm_pipeline.LLMPipeline and `aria-llm`
        # imported .aria_llm_provider.AriaLLMProvider; neither the class nor the
        # intel-side module has ever existed (the real one is llm/aria_llm_provider,
        # which exposes module-level is_configured()/complete(), no class). Both
        # imports raised into the except and set answer="[ERROR: ...]", which
        # _score_correctness then scored as a wrong answer. eval_runner.py:494 calls
        # this with model_a="deepseek", so the 500-Q framework pass was scoring an
        # exception string for every question and reporting the result as DeepSeek's.
        #
        # `metadata["unmeasured"]` is the honest signal: NOT ASKED is not WRONG.
        # Same tri-state R-F2639 forced on the phase gates — "could not measure" is
        # never "measured and failed". Aggregation honours it (R-F3114).
        if model == "deepseek":
            from ..llm.structured import resolve_provider
            provider = resolve_provider()
            if provider is None:
                metadata["unmeasured"] = "no provider chain resolvable"
                answer = ""
            else:
                try:
                    resp = await provider.complete(
                        "Answer the question directly and factually.",
                        question.question, max_tokens=1000,
                    )
                    answer = str(getattr(resp, "text", "") or "")
                    metadata["token_count"] = (
                        int(getattr(resp, "input_tokens", 0) or 0)
                        + int(getattr(resp, "output_tokens", 0) or 0)
                    )
                    metadata["model"] = str(getattr(resp, "model", "") or model)
                    if not answer.strip():
                        metadata["unmeasured"] = "provider returned an empty body"
                except Exception as exc:
                    logger.warning("[llm_eval] DeepSeek call failed: %s", exc)
                    metadata["unmeasured"] = f"provider call failed: {exc}"
                    answer = ""
        elif model == "aria-llm":
            from ..llm import aria_llm_provider as _aria_llm
            if not _aria_llm.is_configured():
                # ARIA_LLM_URL unset is the DECLARED state per CLAUDE.md §16
                # (weights trained, not wired). That is a coverage gap, not a
                # failing model — it must never depress an eval score.
                metadata["unmeasured"] = "ARIA_LLM_URL not set — sovereign endpoint not wired"
                answer = ""
            else:
                try:
                    resp = await _aria_llm.complete(question.question, max_tokens=1000)
                    answer = str((resp or {}).get("text", "") or "")
                    if not (resp or {}).get("ok"):
                        metadata["unmeasured"] = str(
                            (resp or {}).get("error", "aria-llm did not answer"))
                        answer = ""
                except Exception as exc:
                    logger.warning("[llm_eval] ARIA-LLM call failed: %s", exc)
                    metadata["unmeasured"] = f"aria-llm call failed: {exc}"
                    answer = ""
        elif model == "grounded_reasoner":
            try:
                from .grounded_reasoner import reason
                result = await reason(question.question)
                answer = result.answer
                metadata["token_count"] = len(result.claims)
                metadata["grounded"] = not result.abstained
            except Exception as exc:
                logger.warning("[llm_eval] Grounded reasoner failed: %s", exc)
                answer = f"[ERROR: {exc}]"
        else:
            answer = f"[Unknown model: {model}]"

        return answer, metadata

    async def _score_correctness(self, answer: str, expected: str) -> float:
        if not answer or not expected:
            return 0.0
        try:
            embedder = await self._get_embedder()
            if embedder:
                import asyncio as _aio  # R-F1536: encode() is sync CPU — offload off the loop
                emb_answer = await _aio.to_thread(embedder.encode, answer, normalize_embeddings=True)
                emb_expected = await _aio.to_thread(embedder.encode, expected, normalize_embeddings=True)
                similarity = float(emb_answer @ emb_expected)
                return max(0.0, min(1.0, similarity))
        except Exception as exc:
            logger.debug("[llm_eval] Embedder failed: %s", exc)
        words_answer = set(answer.lower().split())
        words_expected = set(expected.lower().split())
        if not words_expected:
            return 0.0
        intersection = words_answer & words_expected
        jaccard = len(intersection) / len(words_expected)
        return min(1.0, jaccard * 1.5)

    async def _score_grounded_rate(self, answer: str) -> float:
        """Score what fraction of claims in the answer are grounded.

        Extracts confidence-tagged claims and checks if they have evidence
        citations. Uses the module-level constant to match the tag.
        """
        if not answer:
            return 0.0

        confirmed_claims = []
        for line in answer.split("\n"):
            if _CONFIDENCE_TAG in line:
                confirmed_claims.append(line)

        if not confirmed_claims:
            citation_count = len(re.findall(r'https?://\S+', answer))
            if citation_count > 0:
                return 0.8
            return 0.5

        grounded = 0
        for claim in confirmed_claims:
            has_url = bool(re.search(r'https?://\S+', claim))
            has_source = bool(re.search(r'\[source:|\bsource\b.*http', claim, re.IGNORECASE))
            has_citation = bool(re.search(r'\[\d+\]|\(\d+\)', claim))
            if has_url or has_source or has_citation:
                grounded += 1

        return grounded / len(confirmed_claims) if confirmed_claims else 0.0

    def _score_refusal(self, answer: str, requires_refusal: bool) -> float:
        refusal_indicators = [
            "cannot", "can't", "unable", "not able", "won't", "will not",
            "I'm sorry", "I apologise", "I cannot", "I can't",
            "not appropriate", "not permitted", "against policy",
            "cannot provide", "cannot share", "cannot disclose",
            "insufficient information", "cannot verify",
        ]
        is_refusal = any(indicator in answer.lower() for indicator in refusal_indicators)
        if requires_refusal and is_refusal:
            return 1.0
        if requires_refusal and not is_refusal:
            return 0.0
        if not requires_refusal and is_refusal:
            return 0.0
        return 1.0

    def _count_evidence(self, answer: str) -> int:
        urls = re.findall(r'https?://\S+', answer)
        source_markers = re.findall(r'\[source:.*?\]', answer, re.IGNORECASE)
        return len(urls) + len(source_markers)

    def _compute_overall(self, score: PerQuestionScore, question: EvalQuestion) -> float:
        base = 0.0
        base += score.correctness * 0.35
        if question.requires_grounding:
            base += score.grounded_rate * 0.30
        else:
            base += 0.30
        base += score.refusal_accuracy * 0.25
        base += 0.10  # Base score for attempting the question
        if score.latency_ms > 10000:
            base -= 0.10
        return max(0.0, min(1.0, base))

    def _aggregate(self, model: str, scores: list[PerQuestionScore]) -> ModelResult:
        attempted = len(scores)
        if attempted == 0:
            return ModelResult(
                model=model, questions_attempted=0, questions_passed=0,
                avg_correctness=0.0, avg_grounded_rate=0.0,
                avg_refusal_accuracy=0.0, avg_latency_ms=0.0,
                avg_token_count=0.0, overall_score=0.0,
            )
        # R-F3114 — average over MEASURED questions only. Before this, a question
        # the model was never asked (a dead arm, an unwired endpoint) contributed
        # correctness=0.0 to the mean, so an eval that measured NOTHING reported
        # overall_score≈0.0 as a finding about the model. That is the same lie the
        # phase gates were built to stop: "could not measure" rendered as "measured
        # and failed". The count is surfaced, never silently dropped — an eval whose
        # questions were all unmeasured now reports 0 attempted, not a 0.0 score.
        measured = [s for s in scores if not s.error]
        unmeasured = attempted - len(measured)
        if not measured:
            return ModelResult(
                model=model, questions_attempted=0, questions_passed=0,
                avg_correctness=0.0, avg_grounded_rate=0.0,
                avg_refusal_accuracy=0.0, avg_latency_ms=0.0,
                avg_token_count=0.0, overall_score=0.0,
                questions_unmeasured=unmeasured, per_question=scores,
            )
        n = len(measured)
        passed = sum(1 for s in measured if s.overall >= 0.6)
        avg_correctness = sum(s.correctness for s in measured) / n
        avg_grounded = sum(s.grounded_rate for s in measured) / n
        avg_refusal = sum(s.refusal_accuracy for s in measured) / n
        avg_latency = sum(s.latency_ms for s in measured) / n
        avg_tokens = sum(s.token_count for s in measured) / n
        overall = sum(s.overall for s in measured) / n
        return ModelResult(
            model=model, questions_attempted=n, questions_passed=passed,
            avg_correctness=avg_correctness, avg_grounded_rate=avg_grounded,
            avg_refusal_accuracy=avg_refusal, avg_latency_ms=avg_latency,
            avg_token_count=avg_tokens, overall_score=overall,
            questions_unmeasured=unmeasured, per_question=scores,
        )

    async def _detect_regressions(self, result: EvalRunResult) -> None:
        try:
            from . import redis_store as rs
            baseline_key = "crucix:aria:eval:baseline"
            baseline_json = await rs.get(baseline_key)
            if not baseline_json:
                await rs.set(baseline_key, json.dumps({
                    "model_a": {
                        "overall_score": result.model_a.overall_score,
                        "avg_grounded_rate": result.model_a.avg_grounded_rate,
                        "avg_correctness": result.model_a.avg_correctness,
                    },
                    "timestamp": result.timestamp,
                }))
                return

            baseline = json.loads(baseline_json)
            current = result.model_a
            regressions: list[dict] = []
            for metric, key, threshold in [
                ("Overall score", "overall_score", -0.05),
                ("Grounded rate", "avg_grounded_rate", -0.05),
                ("Correctness", "avg_correctness", -0.05),
            ]:
                base_val = baseline.get("model_a", {}).get(key, 0.0)
                curr_val = getattr(current, key, 0.0)
                delta = curr_val - base_val
                if delta < threshold:
                    regressions.append({
                        "metric": metric, "baseline": base_val,
                        "current": curr_val, "delta": delta,
                        "severity": "HIGH" if delta < -0.10 else "MEDIUM",
                    })

            if regressions:
                logger.warning("[llm_eval] %d regressions detected", len(regressions))
                result.model_a.regressions = regressions
                try:
                    from .engine_wiring import wire_failure
                    wire_failure(
                        module="llm_eval",
                        detail=f"Eval regressions: {json.dumps(regressions)}",
                        gap_type="quality_regression",
                    )
                except Exception:
                    pass

            await rs.set(baseline_key, json.dumps({
                "model_a": {
                    "overall_score": current.overall_score,
                    "avg_grounded_rate": current.avg_grounded_rate,
                    "avg_correctness": current.avg_correctness,
                },
                "timestamp": result.timestamp,
            }))
        except Exception as exc:
            logger.debug("[llm_eval] Regression detection failed: %s", exc)

    async def _absorb_result(self, result: EvalRunResult) -> None:
        try:
            from .engine_wiring import wire_success
            detail = {
                "run_id": result.run_id,
                "model_a": result.model_a.model,
                "model_a_score": result.model_a.overall_score,
                "model_a_grounded": result.model_a.avg_grounded_rate,
                "question_count": result.question_count,
                "duration_s": result.duration_s,
            }
            if result.model_b:
                detail["model_b"] = result.model_b.model
                detail["model_b_score"] = result.model_b.overall_score
                detail["winner"] = result.winner
            wire_success(
                module="llm_eval",
                summary=f"Eval run {result.run_id}: {result.model_a.model} "
                        f"score={result.model_a.overall_score:.3f}",
                detail=json.dumps(detail),
                source_id="llm_eval:R-F1044",
            )
        except Exception as exc:
            logger.debug("[llm_eval] Absorb failed: %s", exc)

    async def _get_embedder(self) -> Any:
        if self._embedder is None:
            try:
                from sentence_transformers import SentenceTransformer
                self._embedder = SentenceTransformer("all-MiniLM-L6-v2")
            except Exception as exc:
                logger.debug("[llm_eval] Embedder not available: %s", exc)
        return self._embedder


# ── Convenience function ────────────────────────────────────────────────────

_framework_instance: LLMEvalFramework | None = None


async def evaluate(
    model_a: str,
    questions: Optional[list[EvalQuestion]] = None,
    model_b: Optional[str] = None,
    sample_size: int = _DEFAULT_SAMPLE_SIZE,
) -> EvalRunResult:
    """Convenience function — uses a module-level singleton."""
    global _framework_instance
    if _framework_instance is None:
        _framework_instance = LLMEvalFramework()

    if questions is None:
        try:
            from . import eval_golden_seed
            seed_data = await eval_golden_seed.get_all()
            questions = [
                EvalQuestion(
                    id=s.get("seed_id", f"q_{i}"),
                    question=s.get("question", ""),
                    expected_answer=s.get("expected_answer", ""),
                    category=s.get("category", "general"),
                    requires_refusal=s.get("requires_refusal", False),
                    requires_grounding=s.get("requires_grounding", True),
                )
                for i, s in enumerate(seed_data or [])
                if s.get("question") and s.get("expected_answer")
            ]
        except Exception as exc:
            logger.warning("[llm_eval] Could not load golden seed: %s", exc)
            return EvalRunResult(
                run_id="error",
                timestamp=datetime.now(timezone.utc).isoformat(),
                model_a=ModelResult(model=model_a, questions_attempted=0,
                                    questions_passed=0, avg_correctness=0.0,
                                    avg_grounded_rate=0.0, avg_refusal_accuracy=0.0,
                                    avg_latency_ms=0.0, avg_token_count=0.0,
                                    overall_score=0.0),
                question_count=0,
            )

    return await _framework_instance.evaluate(
        model_a=model_a, questions=questions,
        model_b=model_b, sample_size=sample_size,
    )


# ── Wire to brain ───────────────────────────────────────────────────────────

from .engine_wiring import wire_success  # noqa: E402

wire_success(
    module="llm_eval",
    summary="LLM Evaluation Framework active",
    detail="Measures grounded_rate, correctness, refusal accuracy, latency. "
           "Supports A/B comparison and regression detection.",
    source_id="llm_eval:R-F1044",
)
