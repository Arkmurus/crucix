"""R-F4164 capability tests for bounded LoRA interpolation."""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest
from safetensors.numpy import load_file, save_file

from scripts.train.interpolate_lora_adapters import interpolate_adapter


ROOT = Path(__file__).resolve().parents[2]


def _adapter(path: Path, value: float, *, rank: int = 2) -> None:
    path.mkdir()
    (path / "adapter_config.json").write_text(json.dumps({
        "base_model_name_or_path": "base",
        "peft_type": "LORA",
        "r": rank,
        "lora_alpha": 4,
        "target_modules": ["v_proj", "q_proj"],
    }), encoding="utf-8")
    save_file({"layer.lora_A.weight": np.full((2, 2), value, dtype=np.float32)},
              path / "adapter_model.safetensors")


def test_real_interpolator_creates_expected_midpoint_without_mutating_inputs(
    tmp_path: Path,
) -> None:
    parent, candidate, output = (
        tmp_path / "parent", tmp_path / "candidate", tmp_path / "mixed"
    )
    _adapter(parent, 0.0)
    _adapter(candidate, 4.0)
    summary = interpolate_adapter(parent, candidate, output, alpha=0.25)

    tensor = load_file(output / "adapter_model.safetensors")["layer.lora_A.weight"]
    assert np.array_equal(tensor, np.ones((2, 2), dtype=np.float32))
    assert np.array_equal(
        load_file(parent / "adapter_model.safetensors")["layer.lora_A.weight"],
        np.zeros((2, 2), dtype=np.float32),
    )
    assert summary["alpha"] == 0.25
    assert json.loads((output / "interpolation_manifest.json").read_text())["complete"] is True


def test_interpolator_rejects_endpoints_incompatible_configs_and_overwrite(
    tmp_path: Path,
) -> None:
    parent, candidate = tmp_path / "parent", tmp_path / "candidate"
    _adapter(parent, 0.0)
    _adapter(candidate, 1.0, rank=4)
    for alpha in (0.0, 1.0):
        with pytest.raises(ValueError, match="strictly between"):
            interpolate_adapter(parent, candidate, tmp_path / f"out-{alpha}", alpha=alpha)
    with pytest.raises(ValueError, match="configurations are incompatible"):
        interpolate_adapter(parent, candidate, tmp_path / "out", alpha=0.5)

    (tmp_path / "exists").mkdir()
    _adapter(tmp_path / "compatible", 1.0)
    with pytest.raises(FileExistsError, match="output already exists"):
        interpolate_adapter(parent, tmp_path / "compatible", tmp_path / "exists", alpha=0.5)


def test_probe_is_pre_registered_with_fixed_arms_and_fail_closed_gate() -> None:
    manifest = json.loads((
        ROOT / "data/eval_reports/aria_tooluse_lora_interpolation_v1_manifest.json"
    ).read_text(encoding="utf-8"))
    assert manifest["alphas"] == [0.25, 0.5, 0.75]
    assert manifest["expected_rows_per_arm"] == 168
    assert manifest["training_performed"] is False
    assert manifest["promotion_gate"] == {
        "minimum_honest": 162,
        "minimum_resolution_honest": 13,
        "maximum_axis_regressions": 0,
    }
    assert manifest["parent_adapter_sha256"] != manifest["candidate_adapter_sha256"]


def test_pod_runner_evaluates_only_registered_arms_and_requires_watchdog() -> None:
    runner = (ROOT / "scripts/train/pod_tooluse_lora_interpolation_v1.sh").read_text(
        encoding="utf-8",
    )
    assert 'ALPHAS="${ALPHAS:-0.25 0.5 0.75}"' in runner
    assert '[ "$ALPHAS" = "0.25 0.5 0.75" ]' in runner
    assert "require_watchdog" in runner
    assert "EXPECTED_ROWS" in runner
    assert "interpolate_lora_adapters" in runner
    assert "eval_tooluse" in runner
    assert "sft_train" not in runner
    assert "dpo_train" not in runner
