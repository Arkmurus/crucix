"""R-F3962 capability guard binding paid recipes to their executable runner."""
from pathlib import Path

from scripts.train.preflight_training_recipe import validate_recipe


ROOT = Path(__file__).resolve().parents[2]


def _dpo_recipe(runner: str) -> dict:
    return {
        "kind": "tooluse_dpo_continuation",
        "runner": runner,
        "base_model": "mistralai/Mistral-7B-Instruct-v0.3",
        "epochs": 1,
        "beta": 0.3,
        "learning_rate": 2e-6,
        "batch_size": 2,
        "gradient_accumulation_steps": 1,
        "max_sequence_length": 4096,
        "max_gradient_norm": 0.3,
        "load_in_4bit": True,
        "parent_mode": "accepted_adapter",
    }


def test_dpo_recipe_accepts_only_its_reviewed_runner() -> None:
    recipe = _dpo_recipe("scripts/train/pod_tooluse_dpo.sh")
    assert validate_recipe(recipe) == []


def test_sft_runner_cannot_be_approved_as_dpo_recipe() -> None:
    recipe = _dpo_recipe("scripts/train/pod_tooluse_sft_continue.sh")
    assert validate_recipe(recipe) == [
        "runner: expected scripts/train/pod_tooluse_dpo.sh, "
        "got scripts/train/pod_tooluse_sft_continue.sh"
    ]


def test_host_passes_effective_runner_into_recipe_review() -> None:
    source = (ROOT / "scripts/train/run_tooluse_dpo.sh").read_text(encoding="utf-8")
    recipe = source.index("RECIPE_JSON=$(printf")
    review = source.index("preflight_training_recipe", recipe)
    create = source.index("scripts/train/_create_v04_pod.py", review)
    assert '"runner":"%s"' in source[recipe:review]
    assert '"$POD_RUNNER"' in source[recipe:review]
    assert recipe < review < create
