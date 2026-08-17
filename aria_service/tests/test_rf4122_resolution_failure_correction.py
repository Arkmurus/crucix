"""R-F4122 — measured resolution failures produce a disjoint correction gate."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.train.build_resolution_failure_correction import (
    build_correction,
    main,
    measured_resolution_contracts,
)
from scripts.train.build_tooluse_corpus import _norm_subject, validate_trace


ROOT = Path(__file__).resolve().parents[2]


def _rows(path: Path) -> list[dict]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def test_incomplete_or_one_sided_measurement_is_rejected() -> None:
    with pytest.raises(ValueError, match="incomplete"):
        measured_resolution_contracts({"complete": False, "total": 0, "rows": []})
    with pytest.raises(ValueError, match="ask_for_clarification"):
        measured_resolution_contracts({
            "complete": True,
            "total": 1,
            "rows": [{
                "label": "tooluse_resolution",
                "honest": False,
                "errors": ["did not select the resolved company ACME (1)"],
            }],
        })


def test_parent_missing_retention_axes_is_rejected() -> None:
    with pytest.raises(ValueError, match="does not retain all axes"):
        build_correction([], [], forbidden_subjects=set())


def test_real_builder_composes_disjoint_validator_passing_replay(tmp_path: Path) -> None:
    output = tmp_path / "correction.jsonl"
    manifest = tmp_path / "manifest.json"
    eval_path = ROOT / "data/training/split_v1/eval.jsonl"
    golden_path = ROOT / "data/eval_frozen/aria_eval_500q.jsonl"

    assert main([
        "--parent-sft", str(ROOT / "data/training/aria_tooluse_resolution_positive_replay_v1.jsonl"),
        "--preferences", str(ROOT / "data/training/aria_tooluse_resolution_branch_expansion_dpo.jsonl"),
        "--failed-report", str(ROOT / "data/eval_reports/aria_tooluse_resolution_positive_replay_v2_eval.json"),
        "--eval", str(eval_path),
        "--golden", str(golden_path),
        "--output", str(output),
        "--manifest", str(manifest),
    ]) == 0

    rows = _rows(output)
    audit = json.loads(manifest.read_text(encoding="utf-8"))
    parent_count = len(_rows(ROOT / "data/training/aria_tooluse_resolution_positive_replay_v1.jsonl"))
    forbidden = {
        _norm_subject(str(row.get("subject") or ""))
        for row in [*_rows(eval_path), *_rows(golden_path)]
    } - {""}
    correction = rows[parent_count:]

    assert audit["complete"] is True
    assert audit["parent_rows"] == parent_count == 254
    assert audit["correction_rows"] == len(correction) == 23
    assert audit["total_rows"] == len(rows) == 277
    assert audit["measured_failure_contracts"] == {
        "ask_for_clarification": 2,
        "select_resolved_company": 5,
    }
    assert audit["heldout_subjects_used_for_training"] is False
    assert not {_norm_subject(row["subject"]) for row in correction} & forbidden
    assert all(validate_trace(row) == [] for row in correction)
    assert output.read_bytes() == (
        ROOT / "data/training/aria_tooluse_resolution_failure_correction_v1.jsonl"
    ).read_bytes()
    assert manifest.read_bytes() == (
        ROOT / "data/eval_reports/aria_tooluse_resolution_failure_correction_v1_manifest.json"
    ).read_bytes()
