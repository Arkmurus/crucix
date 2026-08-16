"""R-F4027 capability proof for live, contamination-safe resolution evidence."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from scripts.train.preflight_cycle import _golden_subjects, check_contamination


ROOT = Path(__file__).resolve().parents[2]


def test_live_resolution_evidence_reaches_only_the_generation_path() -> None:
    raw = ROOT / "data/training/aria_tooluse_resolution_novel_v1.jsonl"
    queue = ROOT / "data/training/tooluse_novel_resolution_generation_queue.jsonl"
    raw_rows = [json.loads(line) for line in raw.read_text(encoding="utf-8").splitlines()
                if line.strip()]
    queue_rows = [json.loads(line) for line in queue.read_text(encoding="utf-8").splitlines()
                  if line.strip()]

    assert len(raw_rows) == 16
    assert {row["label"] for row in raw_rows} == {"tooluse_resolution"}
    assert {row["source"] for row in raw_rows} == {"replayed_real_tool_execution"}
    contamination = check_contamination(
        queue_rows,
        _golden_subjects(ROOT / "data/eval_frozen/aria_eval_500q.jsonl"),
    )
    assert len(queue_rows) == 15
    assert contamination.status == "PASS", contamination.detail

    launcher = (
        ROOT / "scripts/train/run_tooluse_novel_resolution_generation.sh"
    ).read_text(encoding="utf-8")
    assert hashlib.sha256(queue.read_bytes()).hexdigest() in launcher
    assert "exec bash scripts/train/run_tooluse_generation.sh" in launcher
    assert "run_tooluse_dpo.sh" not in launcher
