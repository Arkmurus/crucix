"""R-F4096 guards for balanced resolution DPO with a fixed update budget."""
import json
from pathlib import Path
import tarfile

from scripts.train.balance_resolution_dpo import balance_pairs
from scripts.train.preflight_training_recipe import validate_recipe


ROOT = Path(__file__).resolve().parents[2]


def _rows(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()]


def test_balanced_artifact_uses_only_genuine_observed_pairs() -> None:
    source = _rows(ROOT / "data/training/aria_tooluse_resolution_branch_expansion_dpo.jsonl")
    written = _rows(ROOT / "data/training/aria_tooluse_resolution_balanced_dpo_v1.jsonl")
    assert written == balance_pairs(source)
    assert len(written) == 35
    assert sum(row["why"].startswith("did not ask") for row in written) == 17
    assert sum(row["why"].startswith("did not select") for row in written) == 18
    assert all(row in source for row in written)


def test_recipe_processes_all_pairs_in_four_optimizer_updates() -> None:
    recipe = {
        "kind": "tooluse_dpo_balanced_diagnostic_continuation",
        "runner": "scripts/train/pod_tooluse_dpo.sh",
        "base_model": "mistralai/Mistral-7B-Instruct-v0.3",
        "epochs": 1,
        "beta": 0.3,
        "learning_rate": 2e-6,
        "batch_size": 2,
        "gradient_accumulation_steps": 5,
        "expected_optimizer_steps": 4,
        "max_sequence_length": 4096,
        "max_gradient_norm": 0.3,
        "load_in_4bit": True,
        "parent_mode": "diagnostic_candidate",
    }
    assert validate_recipe(recipe) == []
    micro_batches = (35 + recipe["batch_size"] - 1) // recipe["batch_size"]
    assert micro_batches == 18
    assert micro_batches % recipe["gradient_accumulation_steps"] == 3


def test_launcher_is_non_promotable_and_pins_both_eval_gates() -> None:
    launcher = (
        ROOT / "scripts/train/run_tooluse_resolution_balanced_dpo_v1.sh"
    ).read_text(encoding="utf-8")
    assert "DPO_EXPECTED_UPDATES=4" in launcher
    assert "tooluse_dpo_balanced_diagnostic_continuation" in launcher
    assert "HELDOUT_BASELINE_LOCAL" in launcher
    assert "failed_candidate.tgz" in launcher
    assert "run_immutable_shell.sh scripts/train/run_tooluse_dpo.sh" in launcher


def test_diagnostic_failed_calibration_and_cannot_support_promotion() -> None:
    diagnostics = (
        ROOT / "data/eval_reports/aria_tooluse_resolution_balanced_dpo_v1_diagnostics.tgz"
    )
    with tarfile.open(diagnostics, "r:gz") as archive:
        verdict_member = archive.extractfile("aria_tooluse_curve_dpo_verdict.json")
        probe_member = archive.extractfile("aria_tooluse_curve_dpo_probe.json")
        assert verdict_member is not None and probe_member is not None
        verdict = json.load(verdict_member)
        probe = json.load(probe_member)
    resolution = next(
        row for row in probe["per_axis"] if row["label"] == "tooluse_resolution"
    )
    assert probe["honest"] == 26
    assert probe["total"] == 30
    assert resolution["honest"] == 0
    assert resolution["total"] == 3
    assert verdict == {
        "pass": False,
        "reason": "curve_gate_failed",
        "gain": -1,
        "protected_gain": -1,
        "regressions": [{"label": "tooluse_resolution", "before": 1, "after": 0}],
    }
