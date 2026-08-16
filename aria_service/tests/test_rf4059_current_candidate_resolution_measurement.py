"""R-F4059 guards for measuring current-candidate resolution breadth."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from scripts.train.build_tooluse_corpus import _norm_subject, validate_trace


ROOT = Path(__file__).resolve().parents[2]


def _rows(path: Path) -> list[dict]:
    return [
        json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def test_resolution_queue_is_valid_unique_and_eval_disjoint() -> None:
    queue = ROOT / "data/training/tooluse_novel_resolution_generation_queue.jsonl"
    rows = _rows(queue)
    held = {
        _norm_subject(str(row.get("subject") or ""))
        for row in _rows(ROOT / "data/training/split_v1/eval.jsonl")
    } - {""}
    subjects = {_norm_subject(row["subject"]) for row in rows}

    assert len(rows) == len(subjects) == 15
    assert {row["label"] for row in rows} == {"tooluse_resolution"}
    assert not subjects & held
    assert all(validate_trace(row) == [] for row in rows)


def test_launcher_pins_current_candidate_and_is_generation_only() -> None:
    queue = ROOT / "data/training/tooluse_novel_resolution_generation_queue.jsonl"
    launcher = (
        ROOT / "scripts/train/run_tooluse_protected_positive_v1_resolution_generation.sh"
    ).read_text(encoding="utf-8")

    assert hashlib.sha256(queue.read_bytes()).hexdigest() in launcher
    assert "223ba1dc99d0e65aafdfa3f5190d57e0e8dfdd4013f9fab5be3994af63384998" in launcher
    assert "ARIA_POD_CREATE_API=graphql" in launcher
    assert "ARIA_MAX_GPU_HOURLY_USD=1.60" in launcher
    assert "exec bash scripts/train/run_tooluse_generation.sh" in launcher
    assert "run_tooluse_dpo.sh" not in launcher
    assert "run_tooluse_sft" not in launcher
