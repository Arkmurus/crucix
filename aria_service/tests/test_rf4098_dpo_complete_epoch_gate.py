"""R-F4098 guards against silently truncated gradient-accumulation epochs."""
from pathlib import Path

import pytest

from scripts.train.balance_resolution_dpo import validate_accumulation_budget


ROOT = Path(__file__).resolve().parents[2]


def test_driver_blocks_incomplete_accumulation_window_before_pod_creation() -> None:
    source = (ROOT / "scripts/train/run_tooluse_dpo.sh").read_text(encoding="utf-8")
    gate = source.index("gradient accumulation truncates the epoch")
    create = source.index("scripts/train/_create_v04_pod.py")
    assert gate < create
    assert "micro_batches % accumulation" in source
    assert "updates != expected" in source


def test_failed_recipe_is_now_structurally_unlaunchable() -> None:
    launcher = (
        ROOT / "scripts/train/run_tooluse_resolution_balanced_dpo_v1.sh"
    ).read_text(encoding="utf-8")
    assert "EXPECTED_DPO_PAIRS=35" in launcher
    assert "DPO_GRAD_ACCUM=5" in launcher
    assert "DPO_EXPECTED_UPDATES=4" in launcher
    with pytest.raises(ValueError, match="truncates the epoch"):
        validate_accumulation_budget(
            35, batch_size=2, accumulation_steps=5, expected_updates=4,
        )


def test_complete_accumulation_budget_is_accepted() -> None:
    assert validate_accumulation_budget(
        40, batch_size=2, accumulation_steps=5, expected_updates=4,
    ) == 4
