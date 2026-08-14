"""R-F4008 capability tests for protected-axis failure collection."""
from __future__ import annotations

import hashlib
import json
from collections import Counter
from pathlib import Path

from scripts.train.build_tooluse_corpus import _norm_subject


ROOT = Path(__file__).resolve().parents[2]
QUEUE = ROOT / "data/training/tooluse_protected_axis_recovery_queue.jsonl"
AXES = {
    "tooluse_contradiction", "tooluse_news_impact", "tooluse_resolution",
}


def _jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()]


def test_real_recovery_queue_is_target_only_novel_and_held_out_disjoint() -> None:
    rows = _jsonl(QUEUE)
    assert Counter(str(row["label"]) for row in rows) == {axis: 32 for axis in AXES}

    held_paths = [
        ROOT / "data/training/split_v1/eval.jsonl",
        ROOT / "data/training/split_v2/eval.jsonl",
        ROOT / "data/eval_frozen/aria_eval_500q.jsonl",
        ROOT / "data/training/aria_tooluse_curve_v5_probe.jsonl",
    ]
    held = {
        _norm_subject(str(row.get("subject") or ""))
        for path in held_paths for row in _jsonl(path)
    } - {""}
    assert not {
        _norm_subject(str(row.get("subject") or "")) for row in rows
    } & held

    prior = {
        (str(row.get("label") or ""), _norm_subject(str(row.get("subject") or "")))
        for path in (ROOT / "data/training").glob("aria_tooluse*dpo*.jsonl")
        for row in _jsonl(path)
    }
    assert not {
        (str(row["label"]), _norm_subject(str(row["subject"]))) for row in rows
    } & prior


def test_launcher_pins_queue_and_failed_adapter_for_generation_only() -> None:
    launcher = (ROOT / "scripts/train/run_tooluse_protected_axis_recovery_generation.sh"
                ).read_text(encoding="utf-8")
    assert hashlib.sha256(QUEUE.read_bytes()).hexdigest() in launcher
    assert "aria_tooluse_citation_phoenix_v3_failed_candidate.tgz" in launcher
    assert "exec bash scripts/train/run_tooluse_generation.sh" in launcher
    assert "run_tooluse_dpo.sh" not in launcher
