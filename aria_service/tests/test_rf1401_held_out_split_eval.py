"""R-F1401/R-F1462 — Unit tests for held-out 80/20 split in eval_runner.

Tests:
  1. _split_held_out returns correct split sizes (~80/20)
  2. Split is deterministic (same seed = same split)
  3. Different seed = different split
  4. Entries without IDs are included in both splits (backward compat)
  5. set_eval_split/get_eval_split round-trip
  6. Invalid split raises ValueError
  7. run_eval with split filters correctly
"""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def _run(coro):
    return asyncio.run(coro)


@pytest.fixture(autouse=True)
def _no_framework_eval():
    """R-F3307 — stub the R-F1068 deep-analysis pass.

    The three run_eval tests below feed a 100-item synthetic golden set and patch
    get_golden_set, _aria_chat_session, _save_run, wire_success and judge_enabled.
    They did NOT patch the framework evaluator, which run_eval invokes over every
    item and which loads a local scoring model. The result was a test that blew a
    120s per-test timeout, and it is the second blocker of this kind found in the
    Python suite (see R-F3298, and R-F2812 before it): a unit test doing real work
    it never meant to.

    These tests assert split FILTERING only, the item count and eval_split, so the
    framework's output is not part of any assertion here.

    Patched at the SOURCE module, not on eval_runner. run_eval does
    `from .llm_eval_framework import evaluate as _framework_evaluate` INSIDE the
    function body, so the name is looked up on llm_eval_framework at call time and
    patching eval_runner._framework_evaluate would bind nothing and silently leave
    the real evaluator running.
    """
    with patch("aria_service.intel.llm_eval_framework.evaluate",
               new=AsyncMock(return_value=None)):
        yield


# ════════════════════════════════════════════════════════════════════════════
# _split_held_out
# ════════════════════════════════════════════════════════════════════════════

class TestSplitHeldOut:
    """Unit tests for _split_held_out()."""

    def _make_items(self, count: int = 100) -> list[dict]:
        return [
            {"id": f"gold_test_{i:04d}", "question": f"Q{i}", "expected_answer": f"A{i}"}
            for i in range(count)
        ]

    def test_split_returns_correct_sizes(self) -> None:
        """The eval split should contain ~20% of items."""
        from aria_service.intel.eval_runner import _split_held_out
        items = self._make_items(1000)
        eval_items = _split_held_out(items, "eval")
        train_items = _split_held_out(items, "train")
        total = len(eval_items) + len(train_items)
        assert total == 1000, f"Total {total} != 1000"
        eval_pct = len(eval_items) / total
        assert 0.15 <= eval_pct <= 0.25, f"Eval ratio {eval_pct:.3f} outside 0.15-0.25"

    def test_split_is_deterministic(self) -> None:
        """Same seed should produce the same split."""
        from aria_service.intel.eval_runner import _split_held_out
        items = self._make_items(100)
        eval1 = _split_held_out(items, "eval")
        eval2 = _split_held_out(items, "eval")
        ids1 = {e["id"] for e in eval1}
        ids2 = {e["id"] for e in eval2}
        assert ids1 == ids2, "Same seed produced different splits"

    def test_no_overlap_between_splits(self) -> None:
        """Train and eval splits should have zero overlap."""
        from aria_service.intel.eval_runner import _split_held_out
        items = self._make_items(100)
        eval_items = _split_held_out(items, "eval")
        train_items = _split_held_out(items, "train")
        eval_ids = {e["id"] for e in eval_items}
        train_ids = {e["id"] for e in train_items}
        overlap = eval_ids & train_ids
        assert len(overlap) == 0, f"Overlap: {overlap}"

    def test_items_without_id_included_in_both(self) -> None:
        """Items without an 'id' field should be included in both splits."""
        from aria_service.intel.eval_runner import _split_held_out
        items = [
            {"question": "Q1", "expected_answer": "A1"},
            {"question": "Q2", "expected_answer": "A2"},
        ]
        eval_items = _split_held_out(items, "eval")
        train_items = _split_held_out(items, "train")
        # Both should include all items (no ID = include in both)
        assert len(eval_items) == 2
        assert len(train_items) == 2

    def test_seed_id_fallback(self) -> None:
        """Items with seed_id but no id should use seed_id for splitting."""
        from aria_service.intel.eval_runner import _split_held_out
        items = [
            {"seed_id": "seed_001", "question": "Q1", "expected_answer": "A1"},
            {"seed_id": "seed_002", "question": "Q2", "expected_answer": "A2"},
        ]
        eval_items = _split_held_out(items, "eval")
        train_items = _split_held_out(items, "train")
        assert len(eval_items) + len(train_items) == 2


# ════════════════════════════════════════════════════════════════════════════
# set_eval_split / get_eval_split
# ════════════════════════════════════════════════════════════════════════════

class TestEvalSplitConfig:
    """Unit tests for set_eval_split/get_eval_split."""

    def test_default_is_none(self) -> None:
        """Default split should be None (evaluate all)."""
        from aria_service.intel.eval_runner import get_eval_split
        assert get_eval_split() is None

    def test_set_and_get_round_trip(self) -> None:
        """set_eval_split and get_eval_split should round-trip."""
        from aria_service.intel.eval_runner import set_eval_split, get_eval_split
        set_eval_split("eval")
        assert get_eval_split() == "eval"
        set_eval_split("train")
        assert get_eval_split() == "train"
        set_eval_split(None)
        assert get_eval_split() is None

    def test_invalid_split_raises(self) -> None:
        """Invalid split values should raise ValueError."""
        from aria_service.intel.eval_runner import set_eval_split
        with pytest.raises(ValueError):
            set_eval_split("invalid")
        with pytest.raises(ValueError):
            set_eval_split("test")


# ════════════════════════════════════════════════════════════════════════════
# run_eval with split
# ════════════════════════════════════════════════════════════════════════════

class TestRunEvalWithSplit:
    """Capability tests: run_eval with held-out split."""

    def test_run_eval_with_eval_split_filters(self) -> None:
        """run_eval with eval_split='eval' should only evaluate ~20% of items."""
        from aria_service.intel.eval_runner import (
            run_eval, set_eval_split, get_eval_split, GOLDEN_SET_KEY,
        )

        # Save original split
        orig_split = get_eval_split()

        try:
            # Set eval split
            set_eval_split("eval")

            # Mock the golden set with 100 items
            items = [
                {"id": f"gold_test_{i:04d}", "question": f"Q{i}", "expected_answer": f"A{i}"}
                for i in range(100)
            ]

            with patch("aria_service.intel.eval_runner.get_golden_set",
                       new=AsyncMock(return_value=items)), \
                 patch("aria_service.routes.aria._aria_chat_session",
                       new=AsyncMock(return_value="Test answer.")), \
                 patch("aria_service.intel.eval_runner._save_run",
                       new=AsyncMock()), \
                 patch("aria_service.intel.eval_runner.wire_success"), \
                 patch("aria_service.intel.eval_runner.eval_judge.judge_enabled",
                       new=MagicMock(return_value=False)):

                result = _run(run_eval(MagicMock(), label="test-split"))

            assert "summary" in result
            total = result["summary"]["total"]
            # Should be ~20 items (15-25 range)
            assert 10 <= total <= 30, f"Expected ~20 items, got {total}"
            assert result.get("eval_split") == "eval"

        finally:
            # Restore original split
            set_eval_split(orig_split)

    def test_run_eval_with_train_split_filters(self) -> None:
        """run_eval with eval_split='train' should only evaluate ~80% of items."""
        from aria_service.intel.eval_runner import (
            run_eval, set_eval_split, get_eval_split,
        )

        orig_split = get_eval_split()

        try:
            set_eval_split("train")

            items = [
                {"id": f"gold_test_{i:04d}", "question": f"Q{i}", "expected_answer": f"A{i}"}
                for i in range(100)
            ]

            with patch("aria_service.intel.eval_runner.get_golden_set",
                       new=AsyncMock(return_value=items)), \
                 patch("aria_service.routes.aria._aria_chat_session",
                       new=AsyncMock(return_value="Test answer.")), \
                 patch("aria_service.intel.eval_runner._save_run",
                       new=AsyncMock()), \
                 patch("aria_service.intel.eval_runner.wire_success"), \
                 patch("aria_service.intel.eval_runner.eval_judge.judge_enabled",
                       new=MagicMock(return_value=False)):

                result = _run(run_eval(MagicMock(), label="test-train-split"))

            assert "summary" in result
            total = result["summary"]["total"]
            # Should be ~80 items (70-90 range)
            assert 70 <= total <= 90, f"Expected ~80 items, got {total}"
            assert result.get("eval_split") == "train"

        finally:
            set_eval_split(orig_split)

    def test_run_eval_no_split_includes_all(self) -> None:
        """Without a split, run_eval should evaluate all items."""
        from aria_service.intel.eval_runner import (
            run_eval, set_eval_split, get_eval_split,
        )

        orig_split = get_eval_split()

        try:
            set_eval_split(None)

            items = [
                {"id": f"gold_test_{i:04d}", "question": f"Q{i}", "expected_answer": f"A{i}"}
                for i in range(50)
            ]

            with patch("aria_service.intel.eval_runner.get_golden_set",
                       new=AsyncMock(return_value=items)), \
                 patch("aria_service.routes.aria._aria_chat_session",
                       new=AsyncMock(return_value="Test answer.")), \
                 patch("aria_service.intel.eval_runner._save_run",
                       new=AsyncMock()), \
                 patch("aria_service.intel.eval_runner.wire_success"), \
                 patch("aria_service.intel.eval_runner.eval_judge.judge_enabled",
                       new=MagicMock(return_value=False)):

                result = _run(run_eval(MagicMock(), label="test-no-split"))

            assert "summary" in result
            assert result["summary"]["total"] == 50
            assert result.get("eval_split") is None

        finally:
            set_eval_split(orig_split)
