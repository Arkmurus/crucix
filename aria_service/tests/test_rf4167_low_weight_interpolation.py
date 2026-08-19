"""R-F4167 capability tests for low-weight protected-DPO interpolation."""
from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_probe_is_fixed_to_low_dpo_direction_weights_and_fail_closed_gate() -> None:
    manifest = json.loads((
        ROOT / "data/eval_reports/aria_tooluse_lora_interpolation_v2_manifest.json"
    ).read_text(encoding="utf-8"))
    assert manifest["alphas"] == [0.125, 0.25, 0.5]
    assert manifest["expected_rows_per_arm"] == 168
    assert manifest["training_performed"] is False
    assert manifest["promotion_gate"] == {
        "minimum_honest": 162,
        "minimum_resolution_honest": 13,
        "maximum_axis_regressions": 0,
    }
    assert manifest["parent_adapter_sha256"] != manifest["candidate_adapter_sha256"]


def test_pod_runner_only_evaluates_registered_arms_under_watchdog() -> None:
    runner = (ROOT / "scripts/train/pod_tooluse_lora_interpolation_v2.sh").read_text(
        encoding="utf-8",
    )
    assert 'ALPHAS="${ALPHAS:-0.125 0.25 0.5}"' in runner
    assert '[ "$ALPHAS" = "0.125 0.25 0.5" ]' in runner
    assert "require_watchdog" in runner
    assert "EXPECTED_ROWS" in runner
    assert "interpolate_lora_adapters" in runner
    assert "eval_tooluse" in runner
    assert "dpo_train" not in runner
    assert "sft_train" not in runner
