"""R-F4051 capability coverage for the paid positive-SFT recipe contract."""
from pathlib import Path

from scripts.train.preflight_training_recipe import validate_recipe


ROOT = Path(__file__).resolve().parents[2]


def _positive_recipe(runner: str = "scripts/train/pod_tooluse_sft_continue.sh") -> dict:
    return {
        "kind": "tooluse_positive_sft_continuation",
        "runner": runner,
        "base_model": "mistralai/Mistral-7B-Instruct-v0.3",
        "epochs": 1,
        "learning_rate": 1e-5,
        "batch_size": 2,
        "max_sequence_length": 4096,
        "lora_rank": 32,
        "lora_alpha": 64,
        "load_in_4bit": True,
        "completion_only_loss": True,
        "parent_mode": "accepted_adapter",
    }


def test_positive_sft_recipe_accepts_only_reviewed_runner_and_hyperparameters() -> None:
    assert validate_recipe(_positive_recipe()) == []
    assert validate_recipe(_positive_recipe("scripts/train/pod_tooluse_dpo.sh")) == [
        "runner: expected scripts/train/pod_tooluse_sft_continue.sh, "
        "got scripts/train/pod_tooluse_dpo.sh"
    ]
    changed = _positive_recipe()
    changed["learning_rate"] = 2e-5
    assert validate_recipe(changed) == ["learning_rate: expected 1e-05, got 2e-05"]


def test_real_driver_builds_explicit_positive_recipe_before_allocation() -> None:
    driver = (ROOT / "scripts/train/run_tooluse_dpo.sh").read_text(encoding="utf-8")
    recipe = driver.index("tooluse_positive_sft_continuation)")
    review = driver.index("preflight_training_recipe", recipe)
    allocation = driver.index("scripts/train/_create_v04_pod.py", review)
    branch = driver[recipe:review]
    assert '"kind":"tooluse_positive_sft_continuation"' in branch
    assert '"learning_rate":1e-5' in branch
    assert '"completion_only_loss":true' in branch
    assert recipe < review < allocation


def test_positive_launchers_declare_positive_recipe_kind() -> None:
    for relative in (
        "scripts/train/run_tooluse_protected_positive_v1.sh",
        "scripts/train/run_tooluse_citation_contract_v8.sh",
    ):
        source = (ROOT / relative).read_text(encoding="utf-8")
        assert "TRAINING_RECIPE_KIND=tooluse_positive_sft_continuation" in source
