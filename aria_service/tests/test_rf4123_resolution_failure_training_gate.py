"""R-F4123 — the measured correction launches only from the accepted parent."""
from __future__ import annotations

import hashlib
from pathlib import Path

from scripts.train.preflight_training_recipe import validate_recipe


ROOT = Path(__file__).resolve().parents[2]
LAUNCHER = ROOT / "scripts/train/run_tooluse_resolution_failure_correction_v1.sh"


def test_scaled_accepted_parent_recipe_is_exact() -> None:
    recipe = {
        "kind": "tooluse_positive_sft_scaled_continuation",
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
        "parent_mode": "accepted_adapter",
    }
    assert validate_recipe(recipe) == []
    assert validate_recipe(dict(recipe, learning_rate=1e-5)) == [
        "learning_rate: expected 1e-06, got 1e-05"
    ]
    assert validate_recipe(dict(recipe, parent_mode="diagnostic_candidate")) == [
        "parent_mode: expected accepted_adapter, got diagnostic_candidate"
    ]


def test_launcher_pins_every_training_and_promotion_input() -> None:
    launcher = LAUNCHER.read_text(encoding="utf-8")
    paths = (
        "data/training/aria_tooluse_resolution_failure_correction_v1.jsonl",
        "data/training/checkpoints/aria_tooluse_curve_sft_v5.tgz",
        "data/training/aria_tooluse_curve_v5_probe.jsonl",
        "data/eval_reports/aria_tooluse_curve_v5_sft_rf4031_rescored.json",
        "data/eval_reports/aria_tooluse_curve_sft_v5_heldout_rf4031_rescored.json",
        "data/training/aria_tooluse_protected_dpo_v1.jsonl",
        "data/training/split_v1/eval.jsonl",
    )
    for relative in paths:
        digest = hashlib.sha256((ROOT / relative).read_bytes()).hexdigest()
        assert digest in launcher
    assert "SFT_LR=1e-6" in launcher
    assert "tooluse_positive_sft_scaled_continuation" in launcher
    assert "HELDOUT_BASELINE_LOCAL" in launcher
    assert "aria_tooluse_resolution_positive_replay_v2_failed_candidate.tgz" not in launcher


def test_recipe_is_wired_through_real_driver_and_runner() -> None:
    driver = (ROOT / "scripts/train/run_tooluse_dpo.sh").read_text(encoding="utf-8")
    runner = (ROOT / "scripts/train/pod_tooluse_sft_continue.sh").read_text(encoding="utf-8")
    assert "tooluse_positive_sft_scaled_continuation)" in driver
    assert '"$SFT_LR" "$PARENT_MODE"' in driver
    assert "SFT_LR=$SFT_LR" in driver
    assert '--lr "$SFT_LR"' in runner
