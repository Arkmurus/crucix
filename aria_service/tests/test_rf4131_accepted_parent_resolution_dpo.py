"""R-F4131 — balanced resolution DPO is complete and starts at the incumbent."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from scripts.train.balance_resolution_dpo import validate_accumulation_budget
from scripts.train.preflight_training_recipe import validate_recipe


ROOT = Path(__file__).resolve().parents[2]
LAUNCHER = ROOT / "scripts/train/run_tooluse_resolution_balanced_dpo_v4.sh"


def _rows(path: Path) -> list[dict]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def test_accepted_parent_recipe_processes_the_complete_epoch() -> None:
    recipe = {
        "kind": "tooluse_dpo_balanced_accepted_continuation",
        "runner": "scripts/train/pod_tooluse_dpo.sh",
        "base_model": "mistralai/Mistral-7B-Instruct-v0.3",
        "epochs": 1,
        "beta": 0.3,
        "learning_rate": 2e-6,
        "batch_size": 2,
        "gradient_accumulation_steps": 3,
        "expected_optimizer_steps": 6,
        "max_sequence_length": 4096,
        "max_gradient_norm": 0.3,
        "load_in_4bit": True,
        "parent_mode": "accepted_adapter",
    }

    assert validate_recipe(recipe) == []
    assert validate_accumulation_budget(
        35, batch_size=2, accumulation_steps=3, expected_updates=6,
    ) == 6


def test_launcher_pins_incumbent_evidence_and_both_evaluation_gates() -> None:
    launcher = LAUNCHER.read_text(encoding="utf-8")
    paths = (
        "data/training/checkpoints/aria_tooluse_curve_sft_v5.tgz",
        "data/training/aria_tooluse_curve_v5_probe.jsonl",
        "data/eval_reports/aria_tooluse_curve_v5_sft_rf4031_rescored.json",
        "data/eval_reports/aria_tooluse_curve_sft_v5_heldout_rf4031_rescored.json",
        "data/training/aria_tooluse_resolution_balanced_dpo_v1.jsonl",
        "data/training/split_v1/eval.jsonl",
    )
    for relative in paths:
        digest = hashlib.sha256((ROOT / relative).read_bytes()).hexdigest()
        assert digest in launcher
    assert "DPO_GRAD_ACCUM=3 DPO_EXPECTED_UPDATES=6" in launcher
    assert "PROTECTED_DPO_AXES=tooluse_resolution" in launcher
    assert "HELDOUT_BASELINE_LOCAL" in launcher
    assert "failed_candidate.tgz" in launcher


def test_launcher_uses_only_disjoint_genuine_resolution_preferences() -> None:
    pairs = _rows(ROOT / "data/training/aria_tooluse_resolution_balanced_dpo_v1.jsonl")
    assert len(pairs) == 35
    assert {row["label"] for row in pairs} == {"tooluse_resolution"}
    assert all(row["chosen"] != row["rejected"] for row in pairs)
    driver = (ROOT / "scripts/train/run_tooluse_dpo.sh").read_text(encoding="utf-8")
    assert "tooluse_dpo_balanced_accepted_continuation" in driver
