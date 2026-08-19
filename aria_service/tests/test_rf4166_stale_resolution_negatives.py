"""R-F4166 capability test for stale resolution preference repair."""
from __future__ import annotations

import json
from pathlib import Path

from scripts.train.eval_tooluse import score_one


ROOT = Path(__file__).resolve().parents[2]


def test_shipped_resolution_curriculum_has_no_current_scoring_false_negative() -> None:
    path = ROOT / "data/training/aria_tooluse_resolution_boundary_dpo_v1.jsonl"
    rows = [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert len(rows) == 32
    for row in rows:
        trace = {
            "messages": [
                *row["prompt"], {"role": "assistant", "content": row["rejected"]},
            ],
            "label": row["label"],
            "subject": row["subject"],
        }
        assert score_one(trace, row["rejected"])["honest"] is False
