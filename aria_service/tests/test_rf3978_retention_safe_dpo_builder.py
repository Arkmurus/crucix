"""R-F3978 / C-67 — deterministic retention-safe Phoenix curriculum."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.train.build_retention_safe_dpo import build_curriculum
from scripts.train.build_tooluse_dpo import _norm, build_pairs


ROOT = Path(__file__).resolve().parents[2]


def _jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()]


def test_real_phoenix_merge_retains_history_and_four_current_failures() -> None:
    retention = _jsonl(ROOT / "data/training/aria_tooluse_curve_v5_dpo.jsonl")
    phoenix = _jsonl(
        ROOT / "data/training/tooluse_citation_phoenix_v2_generation_queue.jsonl"
    )
    raw = json.loads((
        ROOT / "data/eval_reports/aria_tooluse_citation_phoenix_v2_generations.json"
    ).read_text(encoding="utf-8"))
    corpus = _jsonl(ROOT / "data/training/split_v1/train.jsonl")
    corpus += _jsonl(ROOT / "data/training/split_v2/train.jsonl")
    held = _jsonl(ROOT / "data/training/split_v1/eval.jsonl")
    held += _jsonl(ROOT / "data/training/split_v2/eval.jsonl")
    held_subjects = {_norm(str(row.get("subject") or "")) for row in held} - {""}
    delta = build_pairs(raw, phoenix, eval_entities=held_subjects,
                        validate_chosen=True)

    pairs, manifest = build_curriculum([retention, delta], corpus, held)

    assert len(pairs) == 57
    assert manifest["chosen_valid"] == manifest["nondegenerate"] == 57
    assert manifest["held_out_overlap"] == 0
    assert {pair["subject"] for pair in pairs[-4:]} == {
        "Hanwha Aerospace", "L3Harris Technologies", "SOCAR", "Uzbekneftegaz",
    }


def test_builder_refuses_held_out_and_missing_canonical_trace() -> None:
    pair = {"subject": "Acme", "label": "x", "prompt": [],
            "chosen": "good", "rejected": "bad"}
    with pytest.raises(ValueError, match="held-out"):
        build_curriculum([[pair]], [], [{"subject": "Acme"}])
    with pytest.raises(ValueError, match="canonical trace"):
        build_curriculum([[pair]], [], [])


def test_launcher_binds_the_audited_curriculum_and_accepted_parent() -> None:
    launch = (ROOT / "scripts/train/run_tooluse_citation_phoenix_v3.sh").read_text(
        encoding="utf-8"
    )
    assert "EXPECTED_DPO_PAIRS=57" in launch
    assert "32f15517b0a26b716c91c5f1d2003d8e3f01c47188fa873dbbae7f09a639d234" in launch
    assert "99030c720f6db869f1fb4829d3389ee98f49cb67fea7b5169ca2f1b90417dac8" in launch
    assert "POD_RUNNER=scripts/train/pod_tooluse_dpo.sh" in launch
    assert "split_v1/eval.jsonl" in launch
