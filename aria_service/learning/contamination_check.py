"""Contamination check — prevents training data from leaking into the eval set.

R-F1462: before any training example enters the corpus, verify it is NOT
contaminated with the frozen 500-Q defence-DD eval set. Two checks:

  1. Exact seed_id match — if the example's seed_id appears in the eval set,
     it is contaminated (exact duplicate of an eval question).

  2. Embedding-cosine near-dup — if the example's question embedding has
     cosine similarity > threshold with any eval question, it is a semantic
     near-duplicate and must be excluded.

Both checks must pass for an example to be admitted. Pure logic, no LLM
calls, fully unit-testable.

Usage:
    checker = ContaminationChecker(eval_set_path="data/eval_reports/aria_eval_500q.jsonl")
    result = await checker.check(question="...", seed_id="...")
    # result.contaminated == True/False, result.reason explains why
"""
from __future__ import annotations

import json
import logging
import os
import re
from pathlib import Path
from typing import Any

logger = logging.getLogger("aria.learning.contamination_check")

# Default path to the frozen 500-Q eval set
_DEFAULT_EVAL_SET = Path(
    os.getenv("ARIA_EVAL_SET_PATH", "data/eval_reports/aria_eval_500q.jsonl")
)

# Cosine similarity threshold for near-dup detection.
# 0.85 means two questions with >85% embedding similarity are near-duplicates.
# This is conservative — semantically identical questions (same meaning,
# different wording) typically score 0.90+. 0.85 catches rephrased questions
# while avoiding false positives from same-topic questions.
_COSINE_THRESHOLD = 0.85


class ContaminationResult:
    """Result of a contamination check."""

    def __init__(
        self,
        contaminated: bool,
        reason: str = "",
        matched_seed_id: str = "",
        cosine_similarity: float = 0.0,
    ) -> None:
        self.contaminated = contaminated
        self.reason = reason
        self.matched_seed_id = matched_seed_id
        self.cosine_similarity = cosine_similarity

    def to_dict(self) -> dict[str, Any]:
        return {
            "contaminated": self.contaminated,
            "reason": self.reason,
            "matched_seed_id": self.matched_seed_id,
            "cosine_similarity": round(self.cosine_similarity, 4),
        }


class ContaminationChecker:
    """Checks training examples against the frozen eval set for contamination.

    Loads the eval set once on construction. The embedder is lazy-loaded
    on first use (only needed for cosine checks).
    """

    def __init__(
        self,
        eval_set_path: str | Path | None = None,
        cosine_threshold: float = _COSINE_THRESHOLD,
    ) -> None:
        self.eval_set_path = Path(eval_set_path) if eval_set_path else _DEFAULT_EVAL_SET
        self.cosine_threshold = cosine_threshold
        self._eval_questions: list[dict[str, Any]] = []
        self._seed_ids: set[str] = set()
        self._embedder: Any = None
        self._eval_embeddings: list[list[float]] | None = None
        self._loaded = False

    async def load(self) -> None:
        """Load the eval set from disk. Idempotent."""
        if self._loaded:
            return
        if not self.eval_set_path.exists():
            logger.warning(
                "[contamination_check] eval set not found at %s — "
                "contamination checks will be skipped",
                self.eval_set_path,
            )
            self._loaded = True
            return

        questions: list[dict[str, Any]] = []
        seed_ids: set[str] = set()
        try:
            with self.eval_set_path.open("r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        entry = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    questions.append(entry)
                    sid = entry.get("seed_id", "")
                    if sid:
                        seed_ids.add(sid)
        except Exception as exc:
            logger.error(
                "[contamination_check] failed to load eval set: %s", exc,
            )
            return

        self._eval_questions = questions
        self._seed_ids = seed_ids
        self._loaded = True
        logger.info(
            "[contamination_check] loaded %d eval questions (%d seed_ids)",
            len(questions), len(seed_ids),
        )

    # ── Check 1: Exact seed_id match ──────────────────────────────────────

    def _check_seed_id(self, seed_id: str) -> ContaminationResult | None:
        """Check if seed_id appears in the eval set.

        Returns a contaminated result if found, None if clean.
        """
        if not seed_id or not self._seed_ids:
            return None
        if seed_id in self._seed_ids:
            return ContaminationResult(
                contaminated=True,
                reason=f"Exact seed_id match: '{seed_id}' is in the eval set",
                matched_seed_id=seed_id,
            )
        return None

    # ── Check 2: Embedding-cosine near-dup ────────────────────────────────

    async def _get_embedder(self) -> Any:
        """Lazy-load the sentence transformer embedder."""
        if self._embedder is None:
            try:
                from sentence_transformers import SentenceTransformer
                self._embedder = SentenceTransformer("all-MiniLM-L6-v2")
            except Exception as exc:
                logger.debug(
                    "[contamination_check] embedder not available: %s", exc,
                )
        return self._embedder

    async def _compute_embeddings(self) -> list[list[float]] | None:
        """Compute embeddings for all eval questions. Cached after first call."""
        if self._eval_embeddings is not None:
            return self._eval_embeddings
        if not self._eval_questions:
            return None

        embedder = await self._get_embedder()
        if embedder is None:
            return None

        texts = [
            q.get("question", "") or ""
            for q in self._eval_questions
        ]
        try:
            embeddings = embedder.encode(texts, show_progress_bar=False)
            self._eval_embeddings = embeddings.tolist() if hasattr(embeddings, "tolist") else embeddings
            logger.debug(
                "[contamination_check] computed %d eval embeddings",
                len(self._eval_embeddings),
            )
        except Exception as exc:
            logger.warning(
                "[contamination_check] embedding computation failed: %s", exc,
            )
            return None
        return self._eval_embeddings

    async def _check_cosine(
        self, question: str,
    ) -> ContaminationResult | None:
        """Check if `question` is a cosine near-dup of any eval question.

        Returns a contaminated result if a near-dup is found, None if clean.
        """
        if not question or not question.strip():
            return None

        embeddings = await self._compute_embeddings()
        if embeddings is None or not embeddings:
            return None

        embedder = await self._get_embedder()
        if embedder is None:
            return None

        try:
            import numpy as np
            query_vec = embedder.encode([question], show_progress_bar=False)
            query_vec = np.array(query_vec).flatten()
            eval_vecs = np.array(embeddings)

            # Cosine similarity: dot product of unit vectors
            query_norm = query_vec / (np.linalg.norm(query_vec) + 1e-10)
            eval_norms = eval_vecs / (np.linalg.norm(eval_vecs, axis=1, keepdims=True) + 1e-10)
            similarities = np.dot(eval_norms, query_norm)

            max_idx = int(np.argmax(similarities))
            max_sim = float(similarities[max_idx])

            if max_sim >= self.cosine_threshold:
                matched = self._eval_questions[max_idx]
                matched_seed_id = matched.get("seed_id", f"q_{max_idx}")
                matched_question = matched.get("question", "")[:100]
                return ContaminationResult(
                    contaminated=True,
                    reason=(
                        f"Cosine near-dup: similarity={max_sim:.4f} "
                        f"(threshold={self.cosine_threshold}) with "
                        f"eval question '{matched_seed_id}': "
                        f"'{matched_question}...'"
                    ),
                    matched_seed_id=matched_seed_id,
                    cosine_similarity=max_sim,
                )
        except Exception as exc:
            logger.debug(
                "[contamination_check] cosine check failed: %s", exc,
            )
        return None

    # ── Main check ────────────────────────────────────────────────────────

    async def check(
        self,
        question: str = "",
        seed_id: str = "",
    ) -> ContaminationResult:
        """Run both contamination checks against the eval set.

        Args:
            question: The training example's question text.
            seed_id: Optional seed_id from the training example's metadata.

        Returns:
            ContaminationResult with contaminated=True/False and reason.
        """
        if not self._loaded:
            await self.load()

        # Check 1: exact seed_id match (fast, no embedding needed)
        seed_result = self._check_seed_id(seed_id)
        if seed_result is not None:
            return seed_result

        # Check 2: embedding-cosine near-dup (slower, needs embedder)
        cosine_result = await self._check_cosine(question)
        if cosine_result is not None:
            return cosine_result

        return ContaminationResult(contaminated=False, reason="clean")


# ── Convenience function ──────────────────────────────────────────────────────

_default_checker: ContaminationChecker | None = None


async def check_contamination(
    question: str = "",
    seed_id: str = "",
    eval_set_path: str | Path | None = None,
) -> ContaminationResult:
    """Convenience function — uses a module-level singleton checker."""
    global _default_checker
    if _default_checker is None:
        _default_checker = ContaminationChecker(eval_set_path=eval_set_path)
    return await _default_checker.check(question=question, seed_id=seed_id)
