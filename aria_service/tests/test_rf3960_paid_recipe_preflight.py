"""R-F3960 capability guard for paid tool-use recipe review."""
from pathlib import Path

from scripts.train.preflight_training_recipe import validate_recipe


ROOT = Path(__file__).resolve().parents[2]


def _approved_dpo() -> dict:
    return {
        "kind": "tooluse_dpo_continuation",
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


def test_approved_measured_dpo_recipe_passes() -> None:
    assert validate_recipe(_approved_dpo()) == []


def test_recipe_drift_fails_closed_with_exact_field() -> None:
    recipe = _approved_dpo()
    recipe["learning_rate"] = 5e-6
    assert validate_recipe(recipe) == [
        "learning_rate: expected 2e-06, got 5e-06"
    ]


def test_unknown_recipe_kind_is_not_silently_accepted() -> None:
    recipe = _approved_dpo()
    recipe["kind"] = "future_unreviewed_recipe"
    assert validate_recipe(recipe) == [
        "kind: no approved recipe named future_unreviewed_recipe"
    ]


def test_paid_launcher_reviews_recipe_before_pod_creation() -> None:
    source = (ROOT / "scripts/train/run_tooluse_dpo.sh").read_text(encoding="utf-8")
    review = source.index("PARENT_MODE=accepted_adapter")
    create = source.index("scripts/train/_create_v04_pod.py")
    assert review < create
    assert "preflight_training_recipe" in source[review:create]
    for field in (
        "tooluse_dpo_continuation",
        '"$FRESH_BASE"',
        '"$DPO_BETA"',
        '"$DPO_LR"',
    ):
        assert field in source[review:create]
    pod_environment = source[source.index('POD_ENV="'):]
    assert "DPO_BETA=$DPO_BETA" in pod_environment
    assert "DPO_LR=$DPO_LR" in pod_environment
