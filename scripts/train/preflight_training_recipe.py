"""Fail closed unless a paid training recipe matches a reviewed configuration."""
from __future__ import annotations

import argparse
import json
from typing import Any


APPROVED_RECIPES: dict[str, dict[str, Any]] = {
    "tooluse_dpo_continuation": {
        "runner": "scripts/train/pod_tooluse_dpo.sh",
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
    },
    "tooluse_dpo_balanced_diagnostic_continuation": {
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
    },
    "tooluse_positive_sft_continuation": {
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
        "parent_mode": "accepted_adapter",
    },
    "tooluse_positive_sft_diagnostic_continuation": {
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
    },
    "tooluse_positive_sft_scaled_diagnostic_continuation": {
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
    },
    "tooluse_dpo_balanced_accepted_continuation": {
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
    },
    "tooluse_dpo_boundary_accepted_continuation": {
        "runner": "scripts/train/pod_tooluse_dpo.sh",
        "base_model": "mistralai/Mistral-7B-Instruct-v0.3",
        "epochs": 1,
        "beta": 0.3,
        "learning_rate": 2e-6,
        "batch_size": 2,
        "gradient_accumulation_steps": 4,
        "expected_optimizer_steps": 4,
        "max_sequence_length": 4096,
        "max_gradient_norm": 0.3,
        "load_in_4bit": True,
        "parent_mode": "accepted_adapter",
    },
    "tooluse_dpo_protected_frontier_continuation": {
        "runner": "scripts/train/pod_tooluse_dpo.sh",
        "base_model": "mistralai/Mistral-7B-Instruct-v0.3",
        "epochs": 1,
        "beta": 0.3,
        "learning_rate": 2e-6,
        "batch_size": 2,
        "gradient_accumulation_steps": 4,
        "expected_optimizer_steps": 6,
        "max_sequence_length": 4096,
        "max_gradient_norm": 0.3,
        "load_in_4bit": True,
        "parent_mode": "diagnostic_candidate",
    },
    "tooluse_positive_sft_scaled_continuation": {
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
    },
    "tooluse_adapter_evaluation_recovery": {
        "runner": "scripts/train/pod_tooluse_calibration_recovery.sh",
        "base_model": "mistralai/Mistral-7B-Instruct-v0.3",
        "load_in_4bit": True,
        "calibration_gate": True,
        "heldout_rows": 168,
        "parent_mode": "accepted_adapter",
    },
}


def validate_recipe(recipe: dict[str, Any]) -> list[str]:
    """Return exact deviations from the reviewed recipe, or an empty list."""
    kind = recipe.get("kind")
    expected = APPROVED_RECIPES.get(str(kind))
    if expected is None:
        return [f"kind: no approved recipe named {kind}"]
    errors = []
    for field, expected_value in expected.items():
        actual = recipe.get(field)
        if actual != expected_value:
            errors.append(f"{field}: expected {expected_value}, got {actual}")
    return errors


def main(argv: list[str] | None = None) -> int:
    """Validate one explicit recipe supplied as JSON."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--recipe-json", required=True)
    args = parser.parse_args(argv)
    try:
        recipe = json.loads(args.recipe_json)
    except json.JSONDecodeError as exc:
        print(f"recipe JSON invalid: {exc}")
        return 3
    if not isinstance(recipe, dict):
        print("recipe must be a JSON object")
        return 3
    errors = validate_recipe(recipe)
    if errors:
        print("training recipe REFUSED")
        for error in errors:
            print(f"- {error}")
        return 3
    print(f"training recipe approved: {recipe['kind']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
