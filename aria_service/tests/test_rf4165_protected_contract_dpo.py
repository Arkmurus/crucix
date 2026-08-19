"""R-F4165 capability tests for protected-contract DPO composition."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.train.build_protected_contract_dpo import build_curriculum, main
from scripts.train.build_mixed_tooluse_cycle import validate_protected_axis_evidence
from scripts.train.build_tooluse_corpus import _norm_subject
from scripts.train.eval_tooluse import score_one
from scripts.train.preflight_training_recipe import validate_recipe


ROOT = Path(__file__).resolve().parents[2]
RESOLUTION = ROOT / "data/training/aria_tooluse_resolution_boundary_dpo_v1.jsonl"
RETENTION = ROOT / "data/training/aria_tooluse_curve_v5_dpo.jsonl"
EVAL = ROOT / "data/training/split_v1/eval.jsonl"
GOLDEN = ROOT / "data/eval_frozen/aria_eval_500q.jsonl"


def _rows(path: Path) -> list[dict]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def test_real_builder_writes_valid_disjoint_protected_curriculum(tmp_path: Path) -> None:
    output, manifest = tmp_path / "curriculum.jsonl", tmp_path / "manifest.json"
    assert main([
        "--input", str(RESOLUTION), "--input", str(RETENTION),
        "--eval", str(EVAL), "--golden", str(GOLDEN),
        "--output", str(output), "--manifest", str(manifest),
    ]) == 0

    rows = _rows(output)
    audit = json.loads(manifest.read_text(encoding="utf-8"))
    forbidden = {
        _norm_subject(str(row.get("subject") or ""))
        for path in (EVAL, GOLDEN)
        for row in _rows(path)
    } - {""}
    assert audit["axis_counts"] == {
        "tooluse_challenge": 8,
        "tooluse_multihop": 7,
        "tooluse_resolution": 32,
    }
    assert audit["curriculum_rows"] == len(rows) == 47
    assert audit["heldout_subjects_used_for_training"] is False
    assert not {_norm_subject(row["subject"]) for row in rows} & forbidden
    counts = validate_protected_axis_evidence(
        rows, forbidden_subjects=forbidden,
        required_axes=frozenset({
            "tooluse_challenge", "tooluse_multihop", "tooluse_resolution",
        }),
    )
    assert sum(counts.values()) == 47
    for row in rows:
        chosen_trace = {
            "messages": [*row["prompt"], {"role": "assistant", "content": row["chosen"]}],
            "label": row["label"],
            "subject": row["subject"],
        }
        if row["label"] == "tooluse_challenge":
            chosen_trace["premise"] = "clean"
        rejected_trace = {
            **chosen_trace,
            "messages": [
                *row["prompt"], {"role": "assistant", "content": row["rejected"]},
            ],
        }
        assert score_one(chosen_trace, row["chosen"])["honest"] is True
        assert score_one(rejected_trace, row["rejected"])["honest"] is False


def test_builder_rejects_heldout_or_duplicate_preferences() -> None:
    row = _rows(RESOLUTION)[0]
    subject = _norm_subject(row["subject"])
    with pytest.raises(ValueError, match="held-out subject"):
        build_curriculum(
            [row], forbidden_subjects={subject},
            required_axis_counts={"tooluse_resolution": 1},
        )
    with pytest.raises(ValueError, match="duplicate protected preference"):
        build_curriculum(
            [row, row], forbidden_subjects=set(),
            required_axis_counts={"tooluse_resolution": 1},
        )


def test_frontier_recipe_is_bounded_to_one_complete_epoch() -> None:
    recipe = {
        "kind": "tooluse_dpo_protected_frontier_continuation",
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
    }
    assert validate_recipe(recipe) == []
    launcher = (
        ROOT / "scripts/train/run_tooluse_protected_contract_dpo_v1.sh"
    ).read_text(encoding="utf-8")
    assert "EXPECTED_DPO_PAIRS=47" in launcher
    assert "DPO_GRAD_ACCUM=4 DPO_EXPECTED_UPDATES=6" in launcher
    assert "tooluse_dpo_protected_frontier_continuation" in launcher
    assert "aria_tooluse_lora_interpolation_v1_alpha_025.tgz" in launcher
    assert "HELDOUT_BASELINE_LOCAL" in launcher
