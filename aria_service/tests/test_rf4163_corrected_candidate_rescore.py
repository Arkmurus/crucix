"""R-F4163 preserves corrected rescoring and strict candidate gating."""
from __future__ import annotations

import json
from pathlib import Path

from scripts.train.compound_tooluse_cycle import promotion_verdict


ROOT = Path(__file__).resolve().parents[2]
REPORTS = ROOT / "data/eval_reports"


def _load(name: str) -> dict:
    return json.loads((REPORTS / name).read_text(encoding="utf-8"))


def test_best_preserved_candidate_gains_overall_but_cannot_regress_resolution() -> None:
    incumbent = _load("aria_tooluse_incumbent_rf4160_rescored.json")
    candidate = _load(
        "aria_tooluse_resolution_failure_correction_v1_rf4163_rescored.json"
    )
    verdict = promotion_verdict(incumbent, candidate)

    assert incumbent["honest"] == 161
    assert candidate["honest"] == 162
    assert verdict == {
        "promote": False,
        "overall_gain": 1,
        "missing_axes": [],
        "regressions": [{
            "label": "tooluse_resolution",
            "before": 13,
            "after": 12,
            "lost": 1,
            "n": 16,
        }],
        "reason": "retention_gate_failed",
    }


def test_all_preserved_candidates_are_complete_current_scorer_reports() -> None:
    names = (
        "aria_tooluse_resolution_failure_correction_v1_rf4163_rescored.json",
        "aria_tooluse_resolution_positive_replay_v2_rf4163_rescored.json",
        "aria_tooluse_resolution_boundary_dpo_v1_rf4163_rescored.json",
        "aria_tooluse_resolution_balanced_dpo_v4_rf4163_rescored.json",
    )
    expected = [162, 158, 157, 159]
    reports = [_load(name) for name in names]
    assert [report["honest"] for report in reports] == expected
    assert all(report["complete"] is True for report in reports)
    assert all(report["total"] == 168 for report in reports)
    assert all(
        report["scorer_version"] == "R-F4160-evidence-aligned-clean-v4"
        for report in reports
    )


def test_persisted_verdict_blocks_direct_promotion() -> None:
    verdict = _load("aria_tooluse_rf4163_preserved_candidate_verdict.json")
    assert verdict["promote"] is False
    assert verdict["overall_gain"] == 1
    assert verdict["training_output_written"] is False
    assert verdict["regressions"][0]["label"] == "tooluse_resolution"
