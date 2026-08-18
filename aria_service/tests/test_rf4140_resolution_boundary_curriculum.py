"""R-F4140 guards deduplicated entity-resolution decision-state coverage."""
from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys

import pytest

from scripts.train.build_resolution_boundary_dpo import (
    MINIMUM_BRANCH_COUNTS,
    build_curriculum,
)
from scripts.train.preflight_training_recipe import validate_recipe


ROOT = Path(__file__).resolve().parents[2]
SOURCES = [
    ROOT / "data/training/aria_tooluse_novel_resolution_dpo_v1.jsonl",
    ROOT / "data/training/aria_tooluse_protected_positive_v1_resolution_dpo.jsonl",
    ROOT / "data/training/aria_tooluse_resolution_branch_expansion_dpo.jsonl",
]
EVAL = ROOT / "data/training/split_v1/eval.jsonl"
GOLDEN = ROOT / "data/eval_frozen/aria_eval_500q.jsonl"


def _rows(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()]


def test_real_builder_writes_unique_branch_covered_heldout_safe_asset(tmp_path: Path) -> None:
    """Capability: drive the CLI entry point with the genuine preference sources."""
    output = tmp_path / "curriculum.jsonl"
    manifest = tmp_path / "manifest.json"
    argv = [item for source in SOURCES for item in ("--input", str(source))]
    argv.extend([
        "--eval", str(EVAL), "--golden", str(GOLDEN),
        "--output", str(output), "--manifest", str(manifest),
    ])

    command = [
        sys.executable,
        str(ROOT / "scripts/train/build_resolution_boundary_dpo.py"),
        *argv,
    ]
    completed = subprocess.run(
        command, cwd=ROOT, capture_output=True, text=True, check=False,
    )
    assert completed.returncode == 0, completed.stderr

    rows = _rows(output)
    audit = json.loads(manifest.read_text(encoding="utf-8"))
    assert len(rows) == len({row["subject"].casefold() for row in rows}) == 32
    assert audit["source_rows"] == 40
    assert audit["duplicate_subject_rows_removed"] == 8
    assert audit["branch_counts"] == {
        "ambiguous_live": 10,
        "dissolved_only": 2,
        "no_match": 10,
        "unique_live": 10,
    }
    assert audit["minimum_branch_counts"] == MINIMUM_BRANCH_COUNTS
    assert audit["heldout_subjects_used_for_training"] is False


def test_builder_rejects_heldout_subject() -> None:
    row = _rows(SOURCES[0])[0]
    with pytest.raises(ValueError, match="held-out subject"):
        build_curriculum([row], forbidden_subjects={row["subject"].casefold()},
                         minimum_branch_counts={})


def test_builder_rejects_duplicate_subject_with_conflicting_target() -> None:
    row = _rows(SOURCES[0])[0]
    conflict = {**row, "chosen": row["chosen"] + " Conflicting target."}
    with pytest.raises(ValueError, match="conflicting chosen answers"):
        build_curriculum([row, conflict], forbidden_subjects=set(),
                         minimum_branch_counts={})


def test_builder_rejects_missing_decision_state() -> None:
    rows = _rows(SOURCES[2])
    with pytest.raises(ValueError, match="lacks decision-state coverage"):
        build_curriculum(rows, forbidden_subjects=set())


def test_boundary_recipe_and_launcher_pin_complete_epoch_and_artifacts() -> None:
    recipe = {
        "kind": "tooluse_dpo_boundary_accepted_continuation",
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
    }
    assert validate_recipe(recipe) == []
    launcher = (
        ROOT / "scripts/train/run_tooluse_resolution_boundary_dpo_v1.sh"
    ).read_text(encoding="utf-8")
    assert "EXPECTED_DPO_PAIRS=32" in launcher
    assert "DPO_GRAD_ACCUM=4 DPO_EXPECTED_UPDATES=4" in launcher
    assert "tooluse_dpo_boundary_accepted_continuation" in launcher
    assert "aria_tooluse_resolution_boundary_dpo_v1_manifest.json" in launcher
    assert "failed_candidate.tgz" in launcher
