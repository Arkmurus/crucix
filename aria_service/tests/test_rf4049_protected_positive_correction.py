"""R-F4049 tests for genuine chosen-only protected-axis correction assets."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.train.build_protected_positive_correction import build_positive_rows, main


AXES = frozenset({"tooluse_adverse", "tooluse_contradiction"})
DPO_PATH = Path("data/training/aria_tooluse_protected_dpo_v1.jsonl")


def _shipped_rows() -> list[dict]:
    return [json.loads(line) for line in DPO_PATH.read_text(encoding="utf-8").splitlines()]


def test_build_positive_rows_uses_chosen_only_and_preserves_prompt() -> None:
    rows = [
        next(row for row in _shipped_rows() if row["label"] == label)
        for label in sorted(AXES)
    ]

    result = build_positive_rows(rows, forbidden_subjects=set(), required_axes=AXES)

    assert [row["messages"][:-1] for row in result] == [row["prompt"] for row in rows]
    assert [row["messages"][-1]["content"] for row in result] == [
        row["chosen"] for row in rows
    ]
    assert all(row["rejected"] not in json.dumps(result) for row in rows)


def test_build_positive_rows_fails_on_heldout_overlap() -> None:
    rows = [next(row for row in _shipped_rows() if row["label"] == "tooluse_adverse")]
    with pytest.raises(ValueError, match="overlaps held-out"):
        build_positive_rows(
            rows,
            forbidden_subjects={str(rows[0]["subject"]).lower()},
            required_axes=frozenset({"tooluse_adverse"}),
        )


def test_real_builder_emits_complete_manifest_from_shipped_evidence(tmp_path: Path) -> None:
    output = tmp_path / "positive.jsonl"
    manifest = tmp_path / "manifest.json"
    rc = main([
        "--dpo", "data/training/aria_tooluse_protected_dpo_v1.jsonl",
        "--eval", "data/training/split_v1/eval.jsonl",
        "--golden", "data/eval_frozen/aria_eval_500q.jsonl",
        "--axes", "tooluse_adverse,tooluse_contradiction,tooluse_resolution,tooluse_news_impact",
        "--output", str(output),
        "--manifest", str(manifest),
    ])

    built = [json.loads(line) for line in output.read_text(encoding="utf-8").splitlines()]
    proof = json.loads(manifest.read_text(encoding="utf-8"))
    assert rc == 0
    assert len(built) == proof["rows"] == 20
    assert proof["complete"] is True
    assert proof["axis_counts"] == {
        "tooluse_adverse": 4,
        "tooluse_contradiction": 4,
        "tooluse_news_impact": 3,
        "tooluse_resolution": 9,
    }
    assert all(row["messages"][-1]["role"] == "assistant" for row in built)


def test_launch_uses_accepted_parent_and_positive_sft_gate() -> None:
    launcher = Path(
        "scripts/train/run_tooluse_protected_positive_v1.sh"
    ).read_text(encoding="utf-8")
    assert "PARENT=data/training/checkpoints/aria_tooluse_curve_sft_v5.tgz" in launcher
    assert "phoenix_v3_failed_candidate" not in launcher
    assert "POD_RUNNER=scripts/train/pod_tooluse_sft_continue.sh" in launcher
    assert "SFT_LOCAL=\"$SFT\"" in launcher
    assert "HELDOUT_BASELINE_LOCAL=\"$HELDOUT_BASELINE\"" in launcher
    assert "ARIA_MAX_GPU_HOURLY_USD=1.60" in launcher
    assert 'REPO="$ROOT"' in launcher
    assert "run_immutable_shell.sh scripts/train/run_tooluse_dpo.sh" in launcher
