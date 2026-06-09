"""R-F1467 — Full test suite for data_engine generation pipeline + pair_builder.

Tests every stage with mocked LLM/judge/pod interfaces. No paid API calls.
Covers:
  - DataEnginePipeline: all 8 stages individually + end-to-end
  - PairBuilder: assembly, contamination assertion, manifest, integrity
  - Injectable interfaces: mock QuestionGenerator, AnswerGenerator, V02AnswerProvider
"""
from __future__ import annotations

import json
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from aria_service.learning.data_engine_generate import (
    DataEnginePipeline,
    GeneratedPair,
    GeneratedQuestion,
    GenerationResult,
    QuestionGenerator,
    AnswerGenerator,
    V02AnswerProvider,
)
from aria_service.learning.pair_builder import PairBuilder, BuildResult


# ════════════════════════════════════════════════════════════════════════════
# Mock implementations of injectable interfaces
# ════════════════════════════════════════════════════════════════════════════

class MockQuestionGenerator(QuestionGenerator):
    """Mock question generator — returns canned questions."""

    def __init__(self, questions_per_topic: int = 3) -> None:
        self.questions_per_topic = questions_per_topic
        self.call_count = 0

    async def generate_questions(self, topic: str, n: int) -> list[str]:
        self.call_count += 1
        return [f"Mock {topic} question {i}?" for i in range(min(n, self.questions_per_topic))]


class MockAnswerGenerator(AnswerGenerator):
    """Mock answer generator — returns canned answers."""

    def __init__(self, fail_on: set[str] | None = None) -> None:
        self.fail_on = fail_on or set()
        self.call_count = 0

    async def generate_answer(self, question: str, topic: str) -> str:
        self.call_count += 1
        if question in self.fail_on:
            raise RuntimeError(f"Mock failure for {question}")
        # Ensure answer is long enough to pass the sanity check (min 20 words)
        return "Mock answer. " * 25 + f"(topic: {topic})"


class MockV02Provider(V02AnswerProvider):
    """Mock v0.2 answer provider — returns canned v0.2 answers."""

    def __init__(self, fail_on: set[str] | None = None) -> None:
        self.fail_on = fail_on or set()
        self.call_count = 0

    async def get_answer(self, question: str) -> str:
        self.call_count += 1
        if question in self.fail_on:
            return ""
        return f"v0.2 weak answer for '{question[:40]}...'."


# ════════════════════════════════════════════════════════════════════════════
# DataEnginePipeline — stage-by-stage tests
# ════════════════════════════════════════════════════════════════════════════

class TestDataEngineStages:
    """Test each stage of the generation pipeline individually."""

    def make_pipeline(self, **overrides) -> DataEnginePipeline:
        kwargs = dict(
            generator=MockQuestionGenerator(),
            answer_gen=MockAnswerGenerator(),
            topics=["sanctions", "ubo", "compliance"],
        )
        kwargs.update(overrides)
        return DataEnginePipeline(**kwargs)

    # ── Stage 1: Topics ──────────────────────────────────────────────────

    def test_get_topics_returns_all(self) -> None:
        pipeline = self.make_pipeline()
        topics = pipeline.get_topics()
        assert len(topics) == 3
        assert "sanctions" in topics

    def test_get_topics_capped(self) -> None:
        pipeline = self.make_pipeline()
        topics = pipeline.get_topics(n=2)
        assert len(topics) == 2

    # ── Stage 2: Question generation ─────────────────────────────────────

    @pytest.mark.asyncio
    async def test_generate_questions(self) -> None:
        pipeline = self.make_pipeline()
        questions = await pipeline.generate_questions(["sanctions", "ubo"], n_per_topic=2)
        assert len(questions) == 4  # 2 topics × 2 questions
        assert all(isinstance(q, GeneratedQuestion) for q in questions)
        assert all(q.topic in ("sanctions", "ubo") for q in questions)

    @pytest.mark.asyncio
    async def test_generate_questions_empty_topic_list(self) -> None:
        pipeline = self.make_pipeline()
        questions = await pipeline.generate_questions([], n_per_topic=5)
        assert len(questions) == 0

    # ── Stage 3: Intra-batch dedup ───────────────────────────────────────

    @pytest.mark.asyncio
    async def test_dedup_identical_questions(self) -> None:
        pipeline = self.make_pipeline()
        questions = [
            GeneratedQuestion(question="What is sanctions screening?", topic="sanctions"),
            GeneratedQuestion(question="What is sanctions screening?", topic="sanctions"),
            GeneratedQuestion(question="What is UBO?", topic="ubo"),
        ]
        deduped = await pipeline.dedup_questions(questions)
        assert len(deduped) == 2  # One duplicate removed

    @pytest.mark.asyncio
    async def test_dedup_single_question(self) -> None:
        pipeline = self.make_pipeline()
        questions = [GeneratedQuestion(question="What is sanctions?", topic="sanctions")]
        deduped = await pipeline.dedup_questions(questions)
        assert len(deduped) == 1

    @pytest.mark.asyncio
    async def test_dedup_all_unique(self) -> None:
        """Genuinely distinct questions should all survive dedup."""
        pipeline = self.make_pipeline()
        questions = [
            GeneratedQuestion(question="What is sanctions screening?", topic="sanctions"),
            GeneratedQuestion(question="How does UBO tracing work?", topic="ubo"),
            GeneratedQuestion(question="What triggers an export licence?", topic="export_control"),
            GeneratedQuestion(question="How is diversion risk assessed?", topic="diversion"),
            GeneratedQuestion(question="What is a procurement red flag?", topic="procurement"),
        ]
        deduped = await pipeline.dedup_questions(questions)
        assert len(deduped) == 5

    # ── Stage 4: Contamination check ─────────────────────────────────────

    @pytest.mark.asyncio
    async def test_contamination_check_passes_clean(self) -> None:
        pipeline = self.make_pipeline()
        questions = [
            GeneratedQuestion(question="What is the capital of France?", topic="geography"),
        ]
        clean = await pipeline.check_contamination(questions)
        assert len(clean) == 1

    # ── Stage 5: Answer generation ───────────────────────────────────────

    @pytest.mark.asyncio
    async def test_generate_answers(self) -> None:
        pipeline = self.make_pipeline()
        questions = [
            GeneratedQuestion(question="What is sanctions?", topic="sanctions"),
            GeneratedQuestion(question="What is UBO?", topic="ubo"),
        ]
        pairs = await pipeline.generate_answers(questions)
        assert len(pairs) == 2
        assert all(isinstance(p, GeneratedPair) for p in pairs)
        assert all(p.chosen_answer for p in pairs)
        assert all(p.topic in ("sanctions", "ubo") for p in pairs)

    @pytest.mark.asyncio
    async def test_generate_answers_handles_failure(self) -> None:
        pipeline = self.make_pipeline(
            answer_gen=MockAnswerGenerator(fail_on={"What is sanctions?"}),
        )
        questions = [
            GeneratedQuestion(question="What is sanctions?", topic="sanctions"),
            GeneratedQuestion(question="What is UBO?", topic="ubo"),
        ]
        pairs = await pipeline.generate_answers(questions)
        assert len(pairs) == 1  # One failed, one succeeded
        assert pairs[0].topic == "ubo"

    # ── Stage 6: Sanity check ────────────────────────────────────────────

    def test_sanity_check_passes_good_answers(self) -> None:
        pipeline = self.make_pipeline()
        pairs = [
            GeneratedPair(
                question="Q1", chosen_answer="Word " * 30,  # 30 words
            ),
            GeneratedPair(
                question="Q2", chosen_answer="Word " * 100,  # 100 words
            ),
        ]
        passed = pipeline.sanity_check(pairs)
        assert len(passed) == 2

    def test_sanity_check_rejects_short_answers(self) -> None:
        pipeline = self.make_pipeline(min_answer_words=20)
        pairs = [
            GeneratedPair(
                question="Q1", chosen_answer="Too short",
            ),
        ]
        passed = pipeline.sanity_check(pairs)
        assert len(passed) == 0

    def test_sanity_check_rejects_long_answers(self) -> None:
        pipeline = self.make_pipeline(max_answer_words=10)
        pairs = [
            GeneratedPair(
                question="Q1", chosen_answer="Word " * 20,
            ),
        ]
        passed = pipeline.sanity_check(pairs)
        assert len(passed) == 0

    # ── Stage 7: DPO pairing ─────────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_dpo_pairing_adds_rejected(self) -> None:
        pipeline = self.make_pipeline(v02_provider=MockV02Provider())
        pairs = [
            GeneratedPair(question="Q1", chosen_answer="Strong answer.", topic="sanctions"),
            GeneratedPair(question="Q2", chosen_answer="Strong answer.", topic="ubo"),
        ]
        result = await pipeline.add_dpo_rejected(pairs)
        assert all(p.rejected_answer for p in result)
        assert "v0.2" in result[0].rejected_answer

    @pytest.mark.asyncio
    async def test_dpo_pairing_no_provider(self) -> None:
        pipeline = self.make_pipeline(v02_provider=None)
        pairs = [
            GeneratedPair(question="Q1", chosen_answer="Strong answer.", topic="sanctions"),
        ]
        result = await pipeline.add_dpo_rejected(pairs)
        assert not result[0].rejected_answer

    # ── Stage 8: Volume control ──────────────────────────────────────────

    def test_cap_volume_no_cap(self) -> None:
        pipeline = self.make_pipeline()
        pairs = [GeneratedPair(question=f"Q{i}", chosen_answer="A") for i in range(10)]
        capped = pipeline.cap_volume(pairs, max_pairs=None)
        assert len(capped) == 10

    def test_cap_volume_with_cap(self) -> None:
        pipeline = self.make_pipeline()
        pairs = [GeneratedPair(question=f"Q{i}", chosen_answer="A") for i in range(10)]
        capped = pipeline.cap_volume(pairs, max_pairs=3)
        assert len(capped) == 3

    def test_cap_volume_below_cap(self) -> None:
        pipeline = self.make_pipeline()
        pairs = [GeneratedPair(question=f"Q{i}", chosen_answer="A") for i in range(3)]
        capped = pipeline.cap_volume(pairs, max_pairs=10)
        assert len(capped) == 3


# ════════════════════════════════════════════════════════════════════════════
# DataEnginePipeline — end-to-end
# ════════════════════════════════════════════════════════════════════════════

class TestDataEngineEndToEnd:
    """End-to-end tests of the full generation pipeline."""

    @pytest.mark.asyncio
    async def test_full_pipeline_sft_mode(self) -> None:
        pipeline = DataEnginePipeline(
            generator=MockQuestionGenerator(questions_per_topic=3),
            answer_gen=MockAnswerGenerator(),
            topics=["sanctions", "ubo"],
        )
        result = await pipeline.generate(n_per_topic=3, mode="sft")
        assert isinstance(result, GenerationResult)
        assert result.total_generated > 0
        assert result.total_pairs > 0
        assert result.topics_used == ["sanctions", "ubo"]
        assert result.duration_s > 0
        # All pairs should have chosen answers but no rejected
        for p in result.pairs:
            assert p.chosen_answer
            assert not p.rejected_answer  # SFT mode

    @pytest.mark.asyncio
    async def test_full_pipeline_dpo_mode(self) -> None:
        pipeline = DataEnginePipeline(
            generator=MockQuestionGenerator(questions_per_topic=2),
            answer_gen=MockAnswerGenerator(),
            v02_provider=MockV02Provider(),
            topics=["sanctions"],
        )
        result = await pipeline.generate(n_per_topic=2, mode="dpo")
        assert result.total_pairs > 0
        # At least some pairs should have rejected answers
        dpo_pairs = [p for p in result.pairs if p.rejected_answer]
        assert len(dpo_pairs) > 0

    @pytest.mark.asyncio
    async def test_full_pipeline_with_max_pairs(self) -> None:
        pipeline = DataEnginePipeline(
            generator=MockQuestionGenerator(questions_per_topic=10),
            answer_gen=MockAnswerGenerator(),
            topics=["sanctions", "ubo", "compliance"],
        )
        result = await pipeline.generate(n_per_topic=10, max_pairs=5)
        assert result.total_pairs <= 5

    @pytest.mark.asyncio
    async def test_full_pipeline_save(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            pipeline = DataEnginePipeline(
                generator=MockQuestionGenerator(questions_per_topic=2),
                answer_gen=MockAnswerGenerator(),
                topics=["sanctions"],
                output_dir=Path(tmpdir),
            )
            result = await pipeline.generate(n_per_topic=2, mode="sft")
            summary = await pipeline.save(result, label="test")
            assert summary["sft_written"] > 0
            assert Path(summary["sft_path"]).exists()
            # Verify the file has valid JSON lines
            with open(summary["sft_path"], encoding="utf-8") as f:
                for line in f:
                    record = json.loads(line)
                    assert "messages" in record
                    assert len(record["messages"]) == 2

    @pytest.mark.asyncio
    async def test_full_pipeline_no_questions(self) -> None:
        """Pipeline should handle the case where no questions are generated."""
        pipeline = DataEnginePipeline(
            generator=MockQuestionGenerator(questions_per_topic=0),
            answer_gen=MockAnswerGenerator(),
            topics=["sanctions"],
        )
        result = await pipeline.generate(n_per_topic=0)
        assert result.total_generated == 0
        assert result.total_pairs == 0
        assert len(result.errors) > 0


# ════════════════════════════════════════════════════════════════════════════
# PairBuilder
# ════════════════════════════════════════════════════════════════════════════

class TestPairBuilder:
    """Tests for the pair_builder assembly module."""

    def make_pairs(self, n: int = 5, with_rejected: bool = False) -> list[GeneratedPair]:
        pairs = []
        for i in range(n):
            p = GeneratedPair(
                question=f"Test question {i}?",
                chosen_answer=f"Strong answer {i}. " * 10,
                topic="sanctions" if i % 2 == 0 else "ubo",
                contamination_free=True,
                passed_sanity=True,
            )
            if with_rejected:
                p.rejected_answer = f"Weak answer {i}."
            pairs.append(p)
        return pairs

    @pytest.mark.asyncio
    async def test_build_sft(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            builder = PairBuilder(output_dir=Path(tmpdir))
            pairs = self.make_pairs(5)
            result = await builder.build(pairs, mode="sft")
            assert isinstance(result, BuildResult)
            assert result.sft_written == 5
            assert result.dpo_written == 0
            assert Path(result.sft_path).exists()
            assert "manifest" in result.__dict__

    @pytest.mark.asyncio
    async def test_build_dpo(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            builder = PairBuilder(output_dir=Path(tmpdir))
            pairs = self.make_pairs(5, with_rejected=True)
            result = await builder.build(pairs, mode="dpo")
            assert result.sft_written == 5
            assert result.dpo_written == 5
            assert Path(result.dpo_path).exists()

    @pytest.mark.asyncio
    async def test_build_excludes_contaminated(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            builder = PairBuilder(output_dir=Path(tmpdir))
            pairs = self.make_pairs(5)
            pairs[0].contamination_free = False  # Mark one as contaminated
            result = await builder.build(pairs, mode="sft")
            assert result.sft_written == 4  # Contaminated excluded
            assert not result.contamination_verified

    @pytest.mark.asyncio
    async def test_build_excludes_failed_sanity(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            builder = PairBuilder(output_dir=Path(tmpdir))
            pairs = self.make_pairs(5)
            pairs[0].passed_sanity = False
            result = await builder.build(pairs, mode="sft")
            assert result.sft_written == 4

    @pytest.mark.asyncio
    async def test_build_no_pairs(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            builder = PairBuilder(output_dir=Path(tmpdir))
            result = await builder.build([], mode="sft")
            assert result.sft_written == 0
            assert len(result.errors) > 0

    @pytest.mark.asyncio
    async def test_build_manifest_shape(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            builder = PairBuilder(output_dir=Path(tmpdir))
            pairs = self.make_pairs(3)
            result = await builder.build(pairs, mode="sft", label="test-run")
            m = result.manifest
            assert m["mode"] == "sft"
            assert m["label"] == "test-run"
            assert m["sft_count"] == 3
            assert "by_topic" in m
            assert "built_at" in m
            assert "sft_hashes" in m

    def test_verify_integrity_pass(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            sft_path = Path(tmpdir) / "test.sft.jsonl"
            with sft_path.open("w", encoding="utf-8") as f:
                for i in range(3):
                    f.write(json.dumps({"messages": [{"role": "user", "content": f"Q{i}"}]}) + "\n")
            manifest = {"sft_file": str(sft_path), "sft_count": 3}
            assert PairBuilder.verify_integrity(manifest)

    def test_verify_integrity_fail_count_mismatch(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            sft_path = Path(tmpdir) / "test.sft.jsonl"
            with sft_path.open("w", encoding="utf-8") as f:
                for i in range(3):
                    f.write(json.dumps({"messages": [{"role": "user", "content": f"Q{i}"}]}) + "\n")
            manifest = {"sft_file": str(sft_path), "sft_count": 5}  # Wrong count
            assert not PairBuilder.verify_integrity(manifest)

    def test_verify_integrity_fail_missing_file(self) -> None:
        manifest = {"sft_file": "/tmp/nonexistent.jsonl", "sft_count": 0}
        assert not PairBuilder.verify_integrity(manifest)


# ════════════════════════════════════════════════════════════════════════════
# GeneratedPair data classes
# ════════════════════════════════════════════════════════════════════════════

class TestGeneratedPair:
    """Tests for the GeneratedPair data class."""

    def test_to_sft_dict_shape(self) -> None:
        p = GeneratedPair(
            question="Test?",
            chosen_answer="Answer.",
            topic="sanctions",
            seed_id="seed_001",
        )
        d = p.to_sft_dict()
        assert "messages" in d
        assert len(d["messages"]) == 2
        assert d["messages"][0]["role"] == "user"
        assert d["messages"][1]["role"] == "assistant"
        assert d["metadata"]["topic"] == "sanctions"

    def test_to_dpo_dict_shape(self) -> None:
        p = GeneratedPair(
            question="Test?",
            chosen_answer="Strong answer.",
            rejected_answer="Weak answer.",
            topic="ubo",
        )
        d = p.to_dpo_dict()
        assert d["question"] == "Test?"
        assert d["chosen"] == "Strong answer."
        assert d["rejected"] == "Weak answer."

    def test_to_dpo_dict_empty_rejected(self) -> None:
        p = GeneratedPair(
            question="Test?",
            chosen_answer="Strong answer.",
        )
        d = p.to_dpo_dict()
        assert d["rejected"] == ""

    def test_generation_id_auto_generated(self) -> None:
        p = GeneratedPair(question="Test?", chosen_answer="Answer.")
        assert p.generation_id
        assert len(p.generation_id) == 16


class TestGeneratedQuestion:
    """Tests for the GeneratedQuestion data class."""

    def test_generation_id_auto_generated(self) -> None:
        q = GeneratedQuestion(question="What is sanctions?", topic="sanctions")
        assert q.generation_id
        assert len(q.generation_id) == 16

    def test_generation_id_deterministic(self) -> None:
        q1 = GeneratedQuestion(question="What is sanctions?", topic="sanctions")
        q2 = GeneratedQuestion(question="What is sanctions?", topic="sanctions")
        assert q1.generation_id == q2.generation_id
