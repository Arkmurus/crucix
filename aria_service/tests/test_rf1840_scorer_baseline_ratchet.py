"""R-F1840 — dd_quality_scorer baseline-ratchet fix.

The old scorer ALWAYS overwrote the committed baseline with the current run's
score. So a regressed run lowered the bar to its own level and the SAME
regression never tripped again — --fail-on-regression was a no-op across runs
and the baseline silently ratcheted toward zero. These tests drive
score_dd_quality() (mocking the eval so the score is deterministic) and assert:
seed-if-absent, ratchet-UP on improvement, DON'T lower on regression (so the
drop stays detectable), and lower ONLY when explicitly re-baselined.
"""
from __future__ import annotations

import asyncio
import importlib

from unittest.mock import AsyncMock, patch

scorer = importlib.import_module("scripts.dd_quality_scorer")


def _entries_for(score: float, total: int = 10):
    passed = round(score * total)
    return {"entries": [{"passed": i < passed} for i in range(total)]}


def _run(score, *, update_baseline=False):
    with patch("aria_service.intel.eval_runner.run_eval",
               AsyncMock(return_value=_entries_for(score))):
        return asyncio.run(scorer.score_dd_quality(
            llm=None, label="test", update_baseline=update_baseline))


def _point_baseline_at(tmp_path):
    return patch.object(scorer, "_BASELINE_FILE", tmp_path / "baseline.json")


def test_first_run_seeds_baseline(tmp_path):
    with _point_baseline_at(tmp_path):
        r = _run(0.7)
        assert r["score"] == 0.7
        assert r["previous_score"] is None
        assert scorer._load_baseline() == 0.7


def test_improvement_ratchets_baseline_up(tmp_path):
    with _point_baseline_at(tmp_path):
        _run(0.6)
        r = _run(0.9)
        assert r["previous_score"] == 0.6
        assert scorer._load_baseline() == 0.9  # ratcheted up


def test_regression_does_not_lower_baseline(tmp_path):
    """The core bug: a regressed run must NOT move the bar down."""
    with _point_baseline_at(tmp_path):
        _run(0.9)                       # high-water mark
        r = _run(0.5)                   # regression
        assert r["regression"] is True
        assert scorer._load_baseline() == 0.9, "regression must not lower the baseline"
        # And it must STILL trip on the next equally-bad run (was the masked bug).
        r2 = _run(0.5)
        assert r2["regression"] is True
        assert scorer._load_baseline() == 0.9


def test_explicit_update_baseline_can_lower(tmp_path):
    with _point_baseline_at(tmp_path):
        _run(0.9)
        r = _run(0.6, update_baseline=True)   # deliberate re-baseline
        assert r["score"] == 0.6
        assert scorer._load_baseline() == 0.6


def test_equal_score_leaves_baseline(tmp_path):
    with _point_baseline_at(tmp_path):
        _run(0.8)
        _run(0.8)
        assert scorer._load_baseline() == 0.8
