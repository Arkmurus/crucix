"""R-F2762 — the GRPO §24 dry-run must FAIL a recall-weighted cycle on keyword-less data.

The Cycle-5 goal is to lift keyword recall. Recall enters grounding_reward only via
expected_keywords; a prompt set with none (e.g. aria_grpo_prompts_v1.jsonl) makes the
recall term identically 0, so the cycle silently NO-OPs its stated objective. Before
R-F2762 dry_run() SKIPPED the recall assertion on such a set and printed PASS —
green-lighting a run that would claim a recall gain it never trained ("ARIA is 100%
what she says she is" violation, §24 "training must be REAL"). These tests drive the
real CLI and assert: keyword-less + recall-weighted → hard fail; keyworded → pass;
precision-only (recall weight 0) → exempt.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "scripts" / "train" / "grpo_train.py"
KEYWORDLESS = REPO / "data" / "training" / "aria_grpo_prompts_v1.jsonl"
KEYWORDED = REPO / "data" / "training" / "grpo_grounded_prompts_v2.jsonl"

pytestmark = pytest.mark.skipif(
    not (SCRIPT.exists() and KEYWORDLESS.exists() and KEYWORDED.exists()),
    reason="GRPO training assets not present in this checkout",
)


def _dry_run(dataset: Path, **env_over) -> subprocess.CompletedProcess:
    env = {**os.environ, **{k: str(v) for k, v in env_over.items()}}
    return subprocess.run(
        [sys.executable, str(SCRIPT), "--dry-run", "--dataset", str(dataset)],
        cwd=str(REPO), env=env, capture_output=True, text=True, timeout=180,
    )


def test_recall_weighted_on_keywordless_hard_fails():
    r = _dry_run(KEYWORDLESS, GROUNDING_PRECISION_WEIGHT="0.40")
    assert r.returncode != 0, "recall-weighted cycle on keyword-less data must FAIL the pre-flight"
    out = r.stdout + r.stderr
    assert "R-F2762" in out and "DEAD" in out, out[-400:]


def test_recall_weighted_on_keyworded_passes_and_validates_recall():
    r = _dry_run(KEYWORDED, GROUNDING_PRECISION_WEIGHT="0.40", GROUNDING_COVERAGE_WEIGHT="0.10")
    assert r.returncode == 0, (r.stdout + r.stderr)[-400:]
    out = r.stdout + r.stderr
    assert "[dry-run] RECALL:" in out, "keyworded set must actually exercise the recall assertion"
    assert "PASS" in out


def test_precision_only_is_exempt():
    # An intentional precision-only cycle (recall weight 0) must NOT be blocked.
    r = _dry_run(KEYWORDLESS, GROUNDING_PRECISION_WEIGHT="1.0")
    assert r.returncode == 0, (r.stdout + r.stderr)[-400:]
    assert "R-F2762" not in (r.stdout + r.stderr)
