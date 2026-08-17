"""R-F4084 proves the size-adjusted SFT learning rate reaches real training."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from scripts.train.preflight_training_recipe import validate_recipe


ROOT = Path(__file__).resolve().parents[2]


def test_scaled_recipe_is_exact_and_distinct_from_failed_recipe() -> None:
    scaled = {
        "kind": "tooluse_positive_sft_scaled_diagnostic_continuation",
        "runner": "scripts/train/pod_tooluse_sft_continue.sh",
        "base_model": "mistralai/Mistral-7B-Instruct-v0.3",
        "epochs": 1,
        "learning_rate": 1e-6,
        "batch_size": 2,
        "max_sequence_length": 4096,
        "lora_rank": 32,
        "lora_alpha": 64,
        "load_in_4bit": True,
        "completion_only_loss": True,
        "parent_mode": "diagnostic_candidate",
    }
    wrong = dict(scaled, learning_rate=1e-5)

    assert validate_recipe(scaled) == []
    assert validate_recipe(wrong) == ["learning_rate: expected 1e-06, got 1e-05"]


def test_scaled_rate_is_wired_from_launcher_to_real_training_command() -> None:
    launcher = (ROOT / "scripts/train/run_tooluse_resolution_positive_replay_v2.sh").read_text(encoding="utf-8")
    driver = (ROOT / "scripts/train/run_tooluse_dpo.sh").read_text(encoding="utf-8")
    runner = (ROOT / "scripts/train/pod_tooluse_sft_continue.sh").read_text(encoding="utf-8")

    assert "SFT_LR=1e-6" in launcher
    assert "tooluse_positive_sft_scaled_diagnostic_continuation" in launcher
    assert "SFT_LR=$SFT_LR" in driver
    assert '--lr "$SFT_LR"' in runner
    assert "failed_candidate.tgz" in launcher


def test_scaled_launcher_pins_current_worktree_inputs() -> None:
    launcher = (ROOT / "scripts/train/run_tooluse_resolution_positive_replay_v2.sh").read_text(encoding="utf-8")
    for relative in (
        "data/training/aria_tooluse_resolution_positive_replay_v1.jsonl",
        "data/training/checkpoints/aria_tooluse_protected_positive_v1_failed_candidate.tgz",
        "data/training/aria_tooluse_curve_v5_probe.jsonl",
        "data/eval_reports/rf4054_recovery_baseline/aria_tooluse_sft_child_probe.json",
        "data/training/aria_tooluse_protected_dpo_v1.jsonl",
        "data/training/split_v1/eval.jsonl",
    ):
        digest = hashlib.sha256((ROOT / relative).read_bytes()).hexdigest()
        assert digest in launcher


def test_completed_child_is_rejected_by_exact_parent_heldout_comparison() -> None:
    parent = json.loads((
        ROOT / "data/eval_reports/aria_tooluse_curve_sft_v5_heldout_rf4031_rescored.json"
    ).read_text(encoding="utf-8"))
    child = json.loads((
        ROOT / "data/eval_reports/aria_tooluse_resolution_positive_replay_v2_eval.json"
    ).read_text(encoding="utf-8"))
    probe = json.loads((
        ROOT / "data/eval_reports/rf4084_scaled_replay/aria_tooluse_sft_child_probe.json"
    ).read_text(encoding="utf-8"))
    verdict = json.loads((
        ROOT / "data/eval_reports/rf4084_scaled_replay/aria_tooluse_sft_child_verdict.json"
    ).read_text(encoding="utf-8"))

    def honest_for(report: dict, label: str) -> int:
        return sum(row["honest"] is True for row in report["rows"] if row["label"] == label)

    assert probe["complete"] is True and probe["honest"] == 28
    assert verdict["pass"] is True and verdict["regressions"] == []
    assert parent["complete"] is True and parent["honest"] == 155
    assert child["complete"] is True and child["honest"] == 152
    assert honest_for(parent, "tooluse_resolution") == 11
    assert honest_for(child, "tooluse_resolution") == 9
    assert child["honest"] < parent["honest"]
