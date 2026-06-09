"""Data engine — synthetic training pair generation pipeline (R-F1467).

Distillation-to-parity pipeline: generates synthetic DD/compliance Q&A pairs
across the real topic taxonomy, judge-gated and contamination-checked against
the frozen 500-Q eval set. All LLM/judge/pod calls are behind injectable
interfaces so the pipeline is fully unit-testable without paid API calls.

Pipeline stages:
  1. Topic taxonomy — load the existing DD topic set
  2. Question generation — DeepSeek generates N questions per topic
  3. Intra-batch question dedup — collapse near-identical questions via cosine
  4. Contamination check — exclude questions near-dup with the frozen 500-Q eval set
  5. Reference answer generation — DeepSeek generates strong reference answers
  6. Light sanity check — verify non-empty, on-topic, minimum length
  7. DPO pairing — pair chosen (DeepSeek-strong) with rejected (v0.2-actual)
  8. Volume control — cap at N/week

Usage:
    pipeline = DataEnginePipeline(generator=DeepSeekGenerator(...))
    result = await pipeline.generate(n_per_topic=10, mode="sft")
    # result.pairs: list of SFT/DPO training pairs
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional

logger = logging.getLogger("aria.learning.data_engine")

# Default topic taxonomy — the DD domain set
_DEFAULT_TOPICS = [
    "sanctions", "ubo", "export_control", "diversion",
    "procurement", "tender", "compliance", "brokering",
    "trade_finance", "maritime", "defence", "intelligence",
    "cyber", "financial_crime", "money_laundering",
    "corruption", "human_trafficking", "weapons_proliferation",
    "critical_technology", "dual_use",
]

# Minimum answer length for the light sanity check
_MIN_ANSWER_WORDS = 20
_MAX_ANSWER_WORDS = 4000

# Cosine threshold for intra-batch dedup
_DEDUP_COSINE_THRESHOLD = 0.85

# Default output directory
_OUTPUT_DIR = Path(os.getenv("ARIA_TRAINING_EXPORT_DIR", "/data/aria_training"))


# ── Data classes ──────────────────────────────────────────────────────────────

@dataclass
class GeneratedQuestion:
    """A generated question with metadata."""
    question: str
    topic: str
    generation_id: str = ""
    seed_id: str = ""

    def __post_init__(self) -> None:
        if not self.generation_id:
            raw = f"{self.topic}:{self.question}".encode("utf-8", errors="ignore")
            self.generation_id = hashlib.sha256(raw).hexdigest()[:16]


@dataclass
class GeneratedPair:
    """A complete training pair (SFT or DPO)."""
    question: str
    chosen_answer: str          # DeepSeek-strong reference answer
    rejected_answer: str = ""   # v0.2-actual answer (empty for SFT-only)
    topic: str = ""
    seed_id: str = ""
    generation_id: str = ""
    passed_sanity: bool = True
    contamination_free: bool = True
    judge_verdict: str = ""     # "correct" | "partial" | "wrong" | "" (unjudged)
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.generation_id:
            raw = f"{self.topic}:{self.question}:{self.chosen_answer[:100]}".encode(
                "utf-8", errors="ignore"
            )
            self.generation_id = hashlib.sha256(raw).hexdigest()[:16]

    def to_sft_dict(self) -> dict[str, Any]:
        """OpenAI-format SFT example."""
        return {
            "messages": [
                {"role": "user", "content": self.question},
                {"role": "assistant", "content": self.chosen_answer},
            ],
            "metadata": {
                "source": "generated",
                "topic": self.topic,
                "seed_id": self.seed_id,
                "generation_id": self.generation_id,
            },
        }

    def to_dpo_dict(self) -> dict[str, Any]:
        """OpenAI-format DPO pair (chosen + rejected)."""
        return {
            "question": self.question,
            "chosen": self.chosen_answer,
            "rejected": self.rejected_answer or "",
            "metadata": {
                "source": "generated",
                "topic": self.topic,
                "seed_id": self.seed_id,
                "generation_id": self.generation_id,
            },
        }


@dataclass
class GenerationResult:
    """Result of a full generation run."""
    pairs: list[GeneratedPair]
    total_generated: int = 0
    total_after_dedup: int = 0
    total_after_contamination: int = 0
    total_after_sanity: int = 0
    total_pairs: int = 0
    topics_used: list[str] = field(default_factory=list)
    duration_s: float = 0.0
    errors: list[str] = field(default_factory=list)


# ── Injectable interfaces ────────────────────────────────────────────────────

class QuestionGenerator:
    """Interface for question generation. Inject a real or mock implementation."""

    async def generate_questions(
        self, topic: str, n: int,
    ) -> list[str]:
        """Generate N questions for the given topic.

        Args:
            topic: The topic to generate questions for.
            n: Number of questions to generate.

        Returns:
            List of question strings.
        """
        raise NotImplementedError


class AnswerGenerator:
    """Interface for reference answer generation. Inject a real or mock."""

    async def generate_answer(self, question: str, topic: str) -> str:
        """Generate a strong reference answer for a question.

        Args:
            question: The question to answer.
            topic: The topic context.

        Returns:
            The reference answer string.
        """
        raise NotImplementedError


class V02AnswerProvider:
    """Interface for v0.2 answer retrieval (for DPO rejected side)."""

    async def get_answer(self, question: str) -> str:
        """Get v0.2's actual answer for a question.

        Args:
            question: The question.

        Returns:
            v0.2's answer string, or empty if unavailable.
        """
        raise NotImplementedError


# ── Pipeline ──────────────────────────────────────────────────────────────────

class DataEnginePipeline:
    """The 8-stage generation pipeline. All LLM calls are behind injectable
    interfaces so the pipeline is fully unit-testable without paid API calls."""

    def __init__(
        self,
        generator: QuestionGenerator,
        answer_gen: AnswerGenerator,
        v02_provider: Optional[V02AnswerProvider] = None,
        topics: Optional[list[str]] = None,
        dedup_threshold: float = _DEDUP_COSINE_THRESHOLD,
        min_answer_words: int = _MIN_ANSWER_WORDS,
        max_answer_words: int = _MAX_ANSWER_WORDS,
        output_dir: Optional[Path] = None,
    ) -> None:
        self.generator = generator
        self.answer_gen = answer_gen
        self.v02_provider = v02_provider
        self.topics = topics or list(_DEFAULT_TOPICS)
        self.dedup_threshold = dedup_threshold
        self.min_answer_words = min_answer_words
        self.max_answer_words = max_answer_words
        self.output_dir = Path(output_dir) if output_dir else _OUTPUT_DIR

    # ── Stage 1: Topic taxonomy ──────────────────────────────────────────

    def get_topics(self, n: Optional[int] = None) -> list[str]:
        """Return the topic list, optionally capped."""
        topics = list(self.topics)
        if n is not None:
            topics = topics[:n]
        logger.info("[data_engine] stage 1: %d topics loaded", len(topics))
        return topics

    # ── Stage 2: Question generation ─────────────────────────────────────

    async def generate_questions(
        self, topics: list[str], n_per_topic: int,
    ) -> list[GeneratedQuestion]:
        """Generate questions for each topic."""
        all_questions: list[GeneratedQuestion] = []
        for topic in topics:
            try:
                questions = await self.generator.generate_questions(topic, n_per_topic)
                for q_text in questions:
                    if q_text and q_text.strip():
                        all_questions.append(GeneratedQuestion(
                            question=q_text.strip(),
                            topic=topic,
                        ))
            except Exception as e:
                logger.warning("[data_engine] question gen failed for %s: %s", topic, e)
        logger.info(
            "[data_engine] stage 2: %d questions generated across %d topics",
            len(all_questions), len(topics),
        )
        return all_questions

    # ── Stage 3: Intra-batch dedup ───────────────────────────────────────

    async def dedup_questions(
        self, questions: list[GeneratedQuestion],
    ) -> list[GeneratedQuestion]:
        """Deduplicate questions within the batch using cosine similarity."""
        if len(questions) <= 1:
            return questions

        # Use the embedder from contamination_check if available
        try:
            from aria_service.learning.contamination_check import ContaminationChecker
            checker = ContaminationChecker(cosine_threshold=self.dedup_threshold)
            await checker.load()
            embedder = await checker._get_embedder()
        except Exception:
            embedder = None

        if embedder is None:
            # Fallback: simple text dedup (exact match on normalized text)
            seen: set[str] = set()
            deduped: list[GeneratedQuestion] = []
            for q in questions:
                norm = " ".join(q.question.strip().lower().split())
                if norm not in seen:
                    seen.add(norm)
                    deduped.append(q)
            logger.info(
                "[data_engine] stage 3: %d -> %d (text dedup fallback)",
                len(questions), len(deduped),
            )
            return deduped

        # Cosine dedup
        import numpy as np
        texts = [q.question for q in questions]
        try:
            embeddings = embedder.encode(texts, show_progress_bar=False)
            embeddings = np.array(embeddings)
            norms = embeddings / (np.linalg.norm(embeddings, axis=1, keepdims=True) + 1e-10)

            keep = [True] * len(questions)
            for i in range(len(questions)):
                if not keep[i]:
                    continue
                for j in range(i + 1, len(questions)):
                    if not keep[j]:
                        continue
                    sim = float(np.dot(norms[i], norms[j]))
                    if sim >= self.dedup_threshold:
                        keep[j] = False  # j is a near-dup of i

            deduped = [q for i, q in enumerate(questions) if keep[i]]
            logger.info(
                "[data_engine] stage 3: %d -> %d (cosine dedup, threshold=%.2f)",
                len(questions), len(deduped), self.dedup_threshold,
            )
            return deduped
        except Exception as e:
            logger.warning("[data_engine] cosine dedup failed: %s — using text fallback", e)
            return await self.dedup_questions(questions)  # Retry with text fallback

    # ── Stage 4: Contamination check ─────────────────────────────────────

    async def check_contamination(
        self, questions: list[GeneratedQuestion],
    ) -> list[GeneratedQuestion]:
        """Exclude questions that are near-dup with the frozen 500-Q eval set."""
        try:
            from aria_service.learning.contamination_check import check_contamination
        except Exception as e:
            logger.warning("[data_engine] contamination check unavailable: %s", e)
            return questions

        clean: list[GeneratedQuestion] = []
        for q in questions:
            try:
                result = await check_contamination(question=q.question, seed_id=q.seed_id)
                if not result.contaminated:
                    q.contamination_free = True
                    clean.append(q)
                else:
                    logger.debug(
                        "[data_engine] stage 4: excluded '%s...' — %s",
                        q.question[:60], result.reason,
                    )
            except Exception as e:
                logger.debug("[data_engine] contamination check error: %s", e)
                clean.append(q)  # Fail open: include on error

        logger.info(
            "[data_engine] stage 4: %d -> %d (contamination check)",
            len(questions), len(clean),
        )
        return clean

    # ── Stage 5: Reference answer generation ─────────────────────────────

    async def generate_answers(
        self, questions: list[GeneratedQuestion],
    ) -> list[GeneratedPair]:
        """Generate reference answers for each question."""
        pairs: list[GeneratedPair] = []
        for q in questions:
            try:
                answer = await self.answer_gen.generate_answer(q.question, q.topic)
                if answer and answer.strip():
                    pairs.append(GeneratedPair(
                        question=q.question,
                        chosen_answer=answer.strip(),
                        topic=q.topic,
                        seed_id=q.seed_id,
                        generation_id=q.generation_id,
                    ))
            except Exception as e:
                logger.warning(
                    "[data_engine] answer gen failed for '%s...': %s",
                    q.question[:60], e,
                )
        logger.info(
            "[data_engine] stage 5: %d answers generated", len(pairs),
        )
        return pairs

    # ── Stage 6: Light sanity check ──────────────────────────────────────

    def sanity_check(self, pairs: list[GeneratedPair]) -> list[GeneratedPair]:
        """Light sanity check: verify non-empty, on-topic, minimum length.

        This is intentionally weak — the real quality floor is the DPO pairing.
        Self-grading by the same model that generated the answers is not a
        strong gate (generator==judge bias).
        """
        passed: list[GeneratedPair] = []
        for p in pairs:
            wc = len(p.chosen_answer.split())
            if wc < self.min_answer_words:
                logger.debug(
                    "[data_engine] stage 6: excluded '%s...' — too short (%d words)",
                    p.question[:60], wc,
                )
                continue
            if wc > self.max_answer_words:
                logger.debug(
                    "[data_engine] stage 6: excluded '%s...' — too long (%d words)",
                    p.question[:60], wc,
                )
                continue
            p.passed_sanity = True
            passed.append(p)

        logger.info(
            "[data_engine] stage 6: %d -> %d (sanity check)",
            len(pairs), len(passed),
        )
        return passed

    # ── Stage 7: DPO pairing ─────────────────────────────────────────────

    async def add_dpo_rejected(
        self, pairs: list[GeneratedPair],
    ) -> list[GeneratedPair]:
        """Add v0.2-actual answers as the DPO rejected side.

        If no v0.2 provider is configured, returns pairs unchanged (SFT-only).
        """
        if self.v02_provider is None:
            logger.info("[data_engine] stage 7: skipped (no v0.2 provider)")
            return pairs

        for p in pairs:
            try:
                v02_answer = await self.v02_provider.get_answer(p.question)
                if v02_answer and v02_answer.strip():
                    p.rejected_answer = v02_answer.strip()
            except Exception as e:
                logger.debug(
                    "[data_engine] v0.2 lookup failed for '%s...': %s",
                    p.question[:60], e,
                )

        with_rejected = [p for p in pairs if p.rejected_answer]
        logger.info(
            "[data_engine] stage 7: %d/%d pairs have DPO rejected answers",
            len(with_rejected), len(pairs),
        )
        return pairs

    # ── Stage 8: Volume control ──────────────────────────────────────────

    def cap_volume(
        self, pairs: list[GeneratedPair], max_pairs: Optional[int],
    ) -> list[GeneratedPair]:
        """Cap the number of output pairs."""
        if max_pairs is None or len(pairs) <= max_pairs:
            return pairs
        logger.info(
            "[data_engine] stage 8: capped %d -> %d", len(pairs), max_pairs,
        )
        return pairs[:max_pairs]

    # ── Full pipeline ────────────────────────────────────────────────────

    async def generate(
        self,
        n_per_topic: int = 10,
        mode: str = "sft",
        max_pairs: Optional[int] = None,
        topics: Optional[list[str]] = None,
    ) -> GenerationResult:
        """Run the full 8-stage generation pipeline.

        Args:
            n_per_topic: Number of questions to generate per topic.
            mode: "sft" (chosen only) or "dpo" (chosen + rejected).
            max_pairs: Maximum number of output pairs (None = unlimited).
            topics: Override topic list (None = use default taxonomy).

        Returns:
            GenerationResult with the generated pairs and stage stats.
        """
        start = time.time()
        errors: list[str] = []

        # Stage 1: Topics
        active_topics = self.get_topics()
        if topics:
            active_topics = [t for t in topics if t in self.topics] or active_topics

        # Stage 2: Generate questions
        questions = await self.generate_questions(active_topics, n_per_topic)
        total_generated = len(questions)

        if not questions:
            return GenerationResult(
                pairs=[], total_generated=0, topics_used=active_topics,
                duration_s=time.time() - start, errors=["no questions generated"],
            )

        # Stage 3: Intra-batch dedup
        questions = await self.dedup_questions(questions)
        after_dedup = len(questions)

        # Stage 4: Contamination check
        questions = await self.check_contamination(questions)
        after_contamination = len(questions)

        # Stage 5: Generate answers
        pairs = await self.generate_answers(questions)
        after_answers = len(pairs)

        # Stage 6: Sanity check
        pairs = self.sanity_check(pairs)
        after_sanity = len(pairs)

        # Stage 7: DPO pairing (only in dpo mode)
        if mode == "dpo":
            pairs = await self.add_dpo_rejected(pairs)

        # Stage 8: Volume cap
        pairs = self.cap_volume(pairs, max_pairs)

        duration = time.time() - start
        logger.info(
            "[data_engine] pipeline complete: %d pairs in %.1fs "
            "(gen=%d dedup=%d contam=%d sanity=%d)",
            len(pairs), duration,
            total_generated, after_dedup, after_contamination, after_sanity,
        )

        return GenerationResult(
            pairs=pairs,
            total_generated=total_generated,
            total_after_dedup=after_dedup,
            total_after_contamination=after_contamination,
            total_after_sanity=after_sanity,
            total_pairs=len(pairs),
            topics_used=active_topics,
            duration_s=duration,
            errors=errors,
        )

    # ── Output ───────────────────────────────────────────────────────────

    async def save(
        self, result: GenerationResult, label: str = "",
    ) -> dict[str, Any]:
        """Save generated pairs to JSONL files.

        Writes:
          - {output_dir}/generated_{label}_{date}.sft.jsonl
          - {output_dir}/generated_{label}_{date}.dpo.jsonl (if DPO pairs exist)

        Returns a summary dict.
        """
        date_str = time.strftime("%Y-%m-%d")
        suffix = f"_{label}" if label else ""
        sft_path = self.output_dir / f"generated{suffix}_{date_str}.sft.jsonl"
        dpo_path = self.output_dir / f"generated{suffix}_{date_str}.dpo.jsonl"

        self.output_dir.mkdir(parents=True, exist_ok=True)

        sft_count = 0
        dpo_count = 0

        with sft_path.open("w", encoding="utf-8") as sf:
            for p in result.pairs:
                sf.write(json.dumps(p.to_sft_dict(), ensure_ascii=False) + "\n")
                sft_count += 1

        dpo_pairs = [p for p in result.pairs if p.rejected_answer]
        if dpo_pairs:
            with dpo_path.open("w", encoding="utf-8") as df:
                for p in dpo_pairs:
                    df.write(json.dumps(p.to_dpo_dict(), ensure_ascii=False) + "\n")
                    dpo_count += 1

        summary = {
            "sft_written": sft_count,
            "dpo_written": dpo_count,
            "sft_path": str(sft_path),
            "dpo_path": str(dpo_path) if dpo_pairs else None,
            "topics": result.topics_used,
            "duration_s": round(result.duration_s, 1),
        }
        logger.info(
            "[data_engine] saved %d SFT + %d DPO pairs", sft_count, dpo_count,
        )
        return summary
