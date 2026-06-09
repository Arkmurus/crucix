"""R-F1462 — Unit + capability tests for contamination check and held-out split.

Modules tested:
  1. ContaminationChecker — exact seed_id match + embedding-cosine near-dup
  2. HeldOutSplit — deterministic 80/20 split + zero-overlap assertion

Both are pure logic modules (no LLM calls, no GPU needed for unit tests).
"""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

import pytest


# ════════════════════════════════════════════════════════════════════════════
# CONTAMINATION CHECK
# ════════════════════════════════════════════════════════════════════════════

class TestContaminationCheckSeedId:
    """Unit tests for exact seed_id match detection."""

    def _make_eval_set(self, seed_ids: list[str]) -> Path:
        """Create a temporary eval set JSONL with the given seed_ids."""
        tmp = Path(tempfile.mktemp(suffix=".jsonl"))
        with tmp.open("w", encoding="utf-8") as f:
            for i, sid in enumerate(seed_ids):
                f.write(json.dumps({
                    "seed_id": sid,
                    "question": f"Test question {i}",
                    "expected_answer": f"Answer {i}",
                    "topic": "test",
                }) + "\n")
        return tmp

    @pytest.mark.asyncio
    async def test_clean_example_passes(self) -> None:
        """An example with a seed_id not in the eval set should pass."""
        from aria_service.learning.contamination_check import ContaminationChecker
        eval_path = self._make_eval_set(["seed_001", "seed_002", "seed_003"])
        try:
            checker = ContaminationChecker(eval_set_path=eval_path)
            result = await checker.check(seed_id="seed_999")
            assert not result.contaminated
            assert result.reason == "clean"
        finally:
            eval_path.unlink(missing_ok=True)

    @pytest.mark.asyncio
    async def test_contaminated_seed_id_detected(self) -> None:
        """An example with a seed_id IN the eval set should be flagged."""
        from aria_service.learning.contamination_check import ContaminationChecker
        eval_path = self._make_eval_set(["seed_001", "seed_002", "seed_003"])
        try:
            checker = ContaminationChecker(eval_set_path=eval_path)
            result = await checker.check(seed_id="seed_002")
            assert result.contaminated
            assert "seed_id match" in result.reason
            assert result.matched_seed_id == "seed_002"
        finally:
            eval_path.unlink(missing_ok=True)

    @pytest.mark.asyncio
    async def test_empty_seed_id_skips_check(self) -> None:
        """An empty seed_id should skip the seed_id check (not flag)."""
        from aria_service.learning.contamination_check import ContaminationChecker
        eval_path = self._make_eval_set(["seed_001"])
        try:
            checker = ContaminationChecker(eval_set_path=eval_path)
            result = await checker.check(seed_id="")
            assert not result.contaminated
        finally:
            eval_path.unlink(missing_ok=True)

    @pytest.mark.asyncio
    async def test_missing_eval_set_skips_check(self) -> None:
        """When the eval set file doesn't exist, checks should pass cleanly."""
        from aria_service.learning.contamination_check import ContaminationChecker
        checker = ContaminationChecker(eval_set_path="/tmp/nonexistent_eval_set.jsonl")
        result = await checker.check(seed_id="anything")
        assert not result.contaminated
        assert result.reason == "clean"

    @pytest.mark.asyncio
    async def test_multiple_seed_ids_all_checked(self) -> None:
        """Multiple seed_ids should all be checked against the eval set."""
        from aria_service.learning.contamination_check import ContaminationChecker
        eval_path = self._make_eval_set([f"seed_{i:03d}" for i in range(100)])
        try:
            checker = ContaminationChecker(eval_set_path=eval_path)
            # Check a contaminated one
            r1 = await checker.check(seed_id="seed_042")
            assert r1.contaminated
            # Check a clean one
            r2 = await checker.check(seed_id="seed_999")
            assert not r2.contaminated
        finally:
            eval_path.unlink(missing_ok=True)


class TestContaminationCheckCosine:
    """Unit tests for embedding-cosine near-dup detection.

    These tests require sentence-transformers. If not installed, they skip.
    """

    @pytest.mark.asyncio
    async def test_identical_question_detected(self) -> None:
        """An identical question should be detected as near-dup."""
        pytest.importorskip("sentence_transformers")
        from aria_service.learning.contamination_check import ContaminationChecker

        eval_path = Path(tempfile.mktemp(suffix=".jsonl"))
        try:
            with eval_path.open("w", encoding="utf-8") as f:
                f.write(json.dumps({
                    "seed_id": "seed_001",
                    "question": "What is the capital of France?",
                    "expected_answer": "Paris",
                    "topic": "geography",
                }) + "\n")

            checker = ContaminationChecker(eval_set_path=eval_path, cosine_threshold=0.80)
            result = await checker.check(
                question="What is the capital of France?",
            )
            assert result.contaminated
            assert "cosine" in result.reason or "near-dup" in result.reason
            assert result.cosine_similarity >= 0.80
        finally:
            eval_path.unlink(missing_ok=True)

    @pytest.mark.asyncio
    async def test_rephrased_question_detected(self) -> None:
        """A rephrased question should be detected as near-dup."""
        pytest.importorskip("sentence_transformers")
        from aria_service.learning.contamination_check import ContaminationChecker

        eval_path = Path(tempfile.mktemp(suffix=".jsonl"))
        try:
            with eval_path.open("w", encoding="utf-8") as f:
                f.write(json.dumps({
                    "seed_id": "seed_001",
                    "question": "What is the population of Angola?",
                    "expected_answer": "~33 million",
                    "topic": "demographics",
                }) + "\n")

            checker = ContaminationChecker(eval_set_path=eval_path, cosine_threshold=0.70)
            result = await checker.check(
                question="How many people live in Angola?",
            )
            assert result.contaminated
            assert result.cosine_similarity >= 0.70
        finally:
            eval_path.unlink(missing_ok=True)

    @pytest.mark.asyncio
    async def test_unrelated_question_passes(self) -> None:
        """A completely unrelated question should pass the cosine check."""
        pytest.importorskip("sentence_transformers")
        from aria_service.learning.contamination_check import ContaminationChecker

        eval_path = Path(tempfile.mktemp(suffix=".jsonl"))
        try:
            with eval_path.open("w", encoding="utf-8") as f:
                f.write(json.dumps({
                    "seed_id": "seed_001",
                    "question": "What is the capital of France?",
                    "expected_answer": "Paris",
                    "topic": "geography",
                }) + "\n")

            checker = ContaminationChecker(eval_set_path=eval_path, cosine_threshold=0.85)
            result = await checker.check(
                question="What is the GDP growth rate of Brazil?",
            )
            assert not result.contaminated
        finally:
            eval_path.unlink(missing_ok=True)

    @pytest.mark.asyncio
    async def test_empty_question_skips_cosine(self) -> None:
        """An empty question should skip the cosine check."""
        from aria_service.learning.contamination_check import ContaminationChecker
        checker = ContaminationChecker()
        result = await checker.check(question="")
        assert not result.contaminated

    @pytest.mark.asyncio
    async def test_contamination_result_to_dict(self) -> None:
        """ContaminationResult.to_dict() should return the expected shape."""
        from aria_service.learning.contamination_check import ContaminationResult
        r = ContaminationResult(
            contaminated=True,
            reason="test reason",
            matched_seed_id="seed_001",
            cosine_similarity=0.95,
        )
        d = r.to_dict()
        assert d["contaminated"] is True
        assert d["reason"] == "test reason"
        assert d["matched_seed_id"] == "seed_001"
        assert d["cosine_similarity"] == 0.95


# ════════════════════════════════════════════════════════════════════════════
# HELD-OUT 80/20 SPLIT
# ════════════════════════════════════════════════════════════════════════════

class TestHeldOutSplit:
    """Unit tests for HeldOutSplit."""

    def _make_example(self, seed_id: str, question: str = "") -> dict:
        return {
            "user": question or f"Test question for {seed_id}",
            "assistant": f"Test answer for {seed_id}",
            "meta": {"seed_id": seed_id, "source": "test"},
        }

    def test_split_returns_two_lists(self) -> None:
        """split() should return (train, eval) tuples."""
        from aria_service.learning.held_out_split import HeldOutSplit
        examples = [self._make_example(f"seed_{i:03d}") for i in range(100)]
        splitter = HeldOutSplit(seed=42)
        train, eval_set = splitter.split(examples)
        assert isinstance(train, list)
        assert isinstance(eval_set, list)
        assert len(train) + len(eval_set) == 100

    def test_split_is_deterministic(self) -> None:
        """The same seed should produce the same split every time."""
        from aria_service.learning.held_out_split import HeldOutSplit
        examples = [self._make_example(f"seed_{i:03d}") for i in range(100)]
        splitter = HeldOutSplit(seed=42)
        train1, eval1 = splitter.split(examples)
        train2, eval2 = splitter.split(examples)
        assert len(train1) == len(train2)
        assert len(eval1) == len(eval2)
        # Same examples should be in the same split
        for ex in examples:
            sid = ex["meta"]["seed_id"]
            in_train1 = any(e["meta"]["seed_id"] == sid for e in train1)
            in_train2 = any(e["meta"]["seed_id"] == sid for e in train2)
            assert in_train1 == in_train2, f"{sid} changed splits"

    def test_different_seed_different_split(self) -> None:
        """Different seeds should produce different splits."""
        from aria_service.learning.held_out_split import HeldOutSplit
        examples = [self._make_example(f"seed_{i:03d}") for i in range(100)]
        splitter1 = HeldOutSplit(seed=42)
        splitter2 = HeldOutSplit(seed=99)
        train1, _ = splitter1.split(examples)
        train2, _ = splitter2.split(examples)
        # Different seeds should assign different examples to train
        # (very unlikely to be identical for 100 examples)
        sids1 = {e["meta"]["seed_id"] for e in train1}
        sids2 = {e["meta"]["seed_id"] for e in train2}
        assert sids1 != sids2, "Different seeds produced identical splits"

    def test_approximate_80_20_ratio(self) -> None:
        """The split should be approximately 80/20."""
        from aria_service.learning.held_out_split import HeldOutSplit
        examples = [self._make_example(f"seed_{i:03d}") for i in range(1000)]
        splitter = HeldOutSplit(seed=42)
        train, eval_set = splitter.split(examples)
        total = len(train) + len(eval_set)
        train_pct = len(train) / total
        # Allow ±5% tolerance
        assert 0.75 <= train_pct <= 0.85, f"Train ratio {train_pct:.3f} outside 0.75-0.85"

    def test_zero_overlap(self) -> None:
        """verify_no_overlap should return True for clean splits."""
        from aria_service.learning.held_out_split import HeldOutSplit
        examples = [self._make_example(f"seed_{i:03d}") for i in range(100)]
        splitter = HeldOutSplit(seed=42)
        train, eval_set = splitter.split(examples)
        assert HeldOutSplit.verify_no_overlap(train, eval_set)

    def test_overlap_detected(self) -> None:
        """verify_no_overlap should return False when overlap exists."""
        from aria_service.learning.held_out_split import HeldOutSplit
        ex = self._make_example("seed_001")
        assert not HeldOutSplit.verify_no_overlap([ex], [ex])

    def test_fallback_to_question_text(self) -> None:
        """Examples without seed_id should use question text as key."""
        from aria_service.learning.held_out_split import HeldOutSplit
        examples = [
            {"user": f"Question {i}", "assistant": f"Answer {i}", "meta": {}}
            for i in range(100)
        ]
        splitter = HeldOutSplit(seed=42)
        train, eval_set = splitter.split(examples)
        assert len(train) + len(eval_set) == 100
        assert HeldOutSplit.verify_no_overlap(train, eval_set)

    def test_duplicate_keys_deduplicated(self) -> None:
        """Duplicate keys should be deduplicated (first assignment wins)."""
        from aria_service.learning.held_out_split import HeldOutSplit
        ex = self._make_example("seed_001")
        examples = [ex, ex, ex]  # Three copies of the same example
        splitter = HeldOutSplit(seed=42)
        train, eval_set = splitter.split(examples)
        # Should only appear once total
        assert len(train) + len(eval_set) == 1

    def test_invalid_ratio_raises(self) -> None:
        """Invalid train_ratio should raise ValueError."""
        from aria_service.learning.held_out_split import HeldOutSplit
        with pytest.raises(ValueError):
            HeldOutSplit(train_ratio=0.0)
        with pytest.raises(ValueError):
            HeldOutSplit(train_ratio=1.0)
        with pytest.raises(ValueError):
            HeldOutSplit(train_ratio=-0.1)

    def test_convenience_function(self) -> None:
        """The convenience function should work."""
        from aria_service.learning.held_out_split import split_examples
        examples = [
            {"user": f"Q{i}", "assistant": f"A{i}", "meta": {"seed_id": f"s{i}"}}
            for i in range(50)
        ]
        train, eval_set = split_examples(examples)
        assert len(train) + len(eval_set) == 50
