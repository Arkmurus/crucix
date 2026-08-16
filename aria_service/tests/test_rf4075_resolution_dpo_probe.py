"""R-F4075 guards for a non-promotable resolution-only DPO probe."""
from __future__ import annotations

import json
from pathlib import Path

from scripts.train.build_mixed_tooluse_cycle import validate_protected_axis_evidence
from scripts.train.build_tooluse_corpus import _norm_subject
from scripts.train.preflight_training_recipe import validate_recipe


ROOT = Path(__file__).resolve().parents[2]


def _rows(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()]


def test_eight_resolution_pairs_are_genuine_disjoint_evidence() -> None:
    dpo = _rows(ROOT / "data/training/aria_tooluse_protected_positive_v1_resolution_dpo.jsonl")
    forbidden = {
        _norm_subject(str(row.get("subject") or ""))
        for path in (
            ROOT / "data/training/split_v1/eval.jsonl",
            ROOT / "data/eval_frozen/aria_eval_500q.jsonl",
            ROOT / "data/training/aria_tooluse_curve_v5_probe.jsonl",
        ) for row in _rows(path)
    } - {""}

    counts = validate_protected_axis_evidence(
        dpo, forbidden_subjects=forbidden,
        required_axes=frozenset({"tooluse_resolution"}),
    )
    assert counts["tooluse_resolution"] == 8
    assert all(pair["chosen"] != pair["rejected"] for pair in dpo)


def test_probe_uses_reviewed_recipe_and_cannot_claim_promotion() -> None:
    launcher = (
        ROOT / "scripts/train/run_tooluse_resolution_dpo_probe_v1.sh"
    ).read_text(encoding="utf-8")
    recipe = {
        "kind": "tooluse_dpo_continuation",
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
    }

    assert validate_recipe(recipe) == []
    assert "EXPECTED_DPO_PAIRS=8" in launcher
    assert "PROTECTED_DPO_AXES=tooluse_resolution" in launcher
    assert "aria_tooluse_protected_positive_v1_failed_candidate.tgz" in launcher
    assert "aria_tooluse_sft_child_probe.json" in launcher
    assert "HELDOUT_BASELINE_LOCAL" not in launcher
    assert "failed_candidate.tgz" in launcher
    assert "OUTPUT_LOCAL=data/training/checkpoints/aria_tooluse_resolution_dpo_probe_v1.tgz" not in launcher
