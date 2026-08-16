"""R-F4054 capability coverage for evaluation-only positive recovery."""
from pathlib import Path

from scripts.train.preflight_training_recipe import validate_recipe


ROOT = Path(__file__).resolve().parents[2]


def _recipe(runner: str = "scripts/train/pod_tooluse_calibration_recovery.sh") -> dict:
    return {
        "kind": "tooluse_adapter_evaluation_recovery",
        "runner": runner,
        "base_model": "mistralai/Mistral-7B-Instruct-v0.3",
        "load_in_4bit": True,
        "calibration_gate": True,
        "heldout_rows": 168,
        "parent_mode": "accepted_adapter",
    }


def test_evaluation_recovery_recipe_rejects_training_runner() -> None:
    assert validate_recipe(_recipe()) == []
    assert validate_recipe(_recipe("scripts/train/pod_tooluse_sft_continue.sh")) == [
        "runner: expected scripts/train/pod_tooluse_calibration_recovery.sh, "
        "got scripts/train/pod_tooluse_sft_continue.sh"
    ]


def test_recovery_wrapper_uses_saved_child_without_training_path() -> None:
    wrapper = (
        ROOT / "scripts/train/run_tooluse_protected_positive_v1_recovery.sh"
    ).read_text(encoding="utf-8")
    remote = (
        ROOT / "scripts/train/pod_tooluse_calibration_recovery.sh"
    ).read_text(encoding="utf-8")
    assert "aria_tooluse_protected_positive_v1_failed_candidate.tgz" in wrapper
    assert "TRAINING_RECIPE_KIND=tooluse_adapter_evaluation_recovery" in wrapper
    assert "POD_RUNNER=scripts/train/pod_tooluse_calibration_recovery.sh" in wrapper
    assert "sft_train.py" not in remote
    assert "dpo_train.py" not in remote
    assert "require_watchdog" in remote
    assert "python -m scripts.train.learning_curve_gate" in remote
    assert "--eval-file \"$EVAL\"" in remote
