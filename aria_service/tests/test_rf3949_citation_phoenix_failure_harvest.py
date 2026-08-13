"""Capability tests for the train-only citation-phoenix failure harvest."""
from __future__ import annotations

import hashlib
import json
from collections import Counter
from pathlib import Path

from scripts.train.build_tooluse_corpus import _norm_subject


ROOT = Path(__file__).resolve().parents[2]
QUEUE = ROOT / "data/training/tooluse_citation_phoenix_generation_queue.jsonl"
EVAL = ROOT / "data/training/split_v1/eval.jsonl"
LAUNCHER = ROOT / "scripts/train/run_tooluse_citation_phoenix_generation.sh"


def _rows(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()]


def test_rf3949_queue_is_complete_train_only_failure_evidence() -> None:
    """The paid pass covers all axes without using any held-out entity."""
    queue = _rows(QUEUE)
    held = {_norm_subject(str(row["subject"])) for row in _rows(EVAL)}
    counts = Counter(str(row["label"]) for row in queue)

    assert len(queue) == 100
    assert len(counts) == 10
    assert counts["tooluse_adverse"] == 6
    assert counts["tooluse_news_impact"] == 6
    assert counts["tooluse_person"] == 16
    assert not ({_norm_subject(str(row["subject"])) for row in queue} & held)


def test_rf3949_launcher_pins_accepted_parent_and_queue_bytes() -> None:
    """The rejected v10 child can never become this harvest's serving parent."""
    code = LAUNCHER.read_text(encoding="utf-8")
    queue_hash = hashlib.sha256(QUEUE.read_bytes()).hexdigest()

    assert queue_hash in code
    assert "aria_tooluse_curve_sft_v5.tgz" in code
    assert "citation_contract_v10_calibration_child" not in code
    assert "exec bash scripts/train/run_tooluse_generation.sh" in code
