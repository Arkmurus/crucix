"""R-F4078 capability guards for balanced resolution positive replay."""
from __future__ import annotations

import hashlib
import json
from collections import Counter
from pathlib import Path

from scripts.train.build_positive_replay_curriculum import validate_reference_contract
from scripts.train.preflight_training_recipe import validate_recipe


ROOT = Path(__file__).resolve().parents[2]


def _rows(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()]


def test_curriculum_replays_every_axis_and_balances_resolution_branches() -> None:
    rows = _rows(ROOT / "data/training/aria_tooluse_resolution_positive_replay_v1.jsonl")
    manifest = json.loads((
        ROOT / "data/eval_reports/aria_tooluse_resolution_positive_replay_v1_manifest.json"
    ).read_text(encoding="utf-8"))
    resolution = [row for row in rows if row["label"] == "tooluse_resolution"]
    branches = Counter(
        "clarify" if "cannot safely say which company" in row["messages"][-1]["content"].lower()
        else "select" for row in resolution
    )

    assert manifest["complete"] is True
    assert manifest["parent_rows"] == 230
    assert manifest["delta_rows"] == 15
    assert manifest["total_rows"] == len(rows) == 254
    assert set(manifest["total_axis_counts"]) == {
        "tooluse_adverse", "tooluse_challenge", "tooluse_challenge_unavailable",
        "tooluse_contradiction", "tooluse_multihop", "tooluse_news_impact",
        "tooluse_person", "tooluse_resolution", "tooluse_trace",
        "tooluse_trace_unavailable",
    }
    assert len(resolution) == 30
    assert branches == {"select": 18, "clarify": 12}
    assert len({row["subject"] for row in resolution}) == 21
    for row in rows:
        validate_reference_contract(row)


def test_diagnostic_parent_recipe_is_explicit_and_non_promotable() -> None:
    recipe = {
        "kind": "tooluse_positive_sft_diagnostic_continuation",
        "runner": "scripts/train/pod_tooluse_sft_continue.sh",
        "base_model": "mistralai/Mistral-7B-Instruct-v0.3",
        "epochs": 1,
        "learning_rate": 1e-5,
        "batch_size": 2,
        "max_sequence_length": 4096,
        "lora_rank": 32,
        "lora_alpha": 64,
        "load_in_4bit": True,
        "completion_only_loss": True,
        "parent_mode": "diagnostic_candidate",
    }
    launcher = (
        ROOT / "scripts/train/run_tooluse_resolution_positive_replay_v1.sh"
    ).read_text(encoding="utf-8")

    assert validate_recipe(recipe) == []
    assert "TRAINING_RECIPE_KIND=tooluse_positive_sft_diagnostic_continuation" in launcher
    assert "aria_tooluse_protected_positive_v1_failed_candidate.tgz" in launcher
    assert "HELDOUT_BASELINE_LOCAL" not in launcher
    assert "failed_candidate.tgz" in launcher


def test_launcher_pins_actual_post_checkout_bytes() -> None:
    launcher = (
        ROOT / "scripts/train/run_tooluse_resolution_positive_replay_v1.sh"
    ).read_text(encoding="utf-8")
    for relative in (
        "data/training/aria_tooluse_resolution_positive_replay_v1.jsonl",
        "data/training/checkpoints/aria_tooluse_protected_positive_v1_failed_candidate.tgz",
        "data/training/aria_tooluse_curve_v5_probe.jsonl",
        "data/eval_reports/rf4054_recovery_baseline/aria_tooluse_sft_child_probe.json",
        "data/training/aria_tooluse_protected_dpo_v1.jsonl",
        "data/training/split_v1/eval.jsonl",
    ):
        digest = hashlib.sha256((ROOT / relative).read_bytes()).hexdigest()
        assert digest in launcher, f"launcher does not pin current bytes for {relative}"
