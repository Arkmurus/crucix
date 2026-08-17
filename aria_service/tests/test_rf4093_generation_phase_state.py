"""R-F4093 guards for phase-observable, immutable generation drivers."""
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_generation_driver_records_each_watchdog_epoch() -> None:
    source = (ROOT / "scripts/train/run_tooluse_generation.sh").read_text(
        encoding="utf-8",
    )
    assert "PHASE=$phase" in source
    assert "PHASE_AT=$(date -u +%s)" in source
    assert source.index("write_state upload") < source.index("write_state generation")
    assert source.index("write_state generation") < source.index("write_state complete")
    assert "write_state stopped" in source


def test_resolution_measurement_uses_immutable_driver_snapshot() -> None:
    launcher = (
        ROOT / "scripts/train/run_tooluse_resolution_branch_expansion_generation.sh"
    ).read_text(encoding="utf-8")
    assert (
        "exec bash scripts/train/run_immutable_shell.sh "
        "scripts/train/run_tooluse_generation.sh"
    ) in launcher
