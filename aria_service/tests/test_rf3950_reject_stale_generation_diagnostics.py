"""Capability tests for truthful generation diagnostic harvesting."""
from pathlib import Path


RUNNER = (Path(__file__).resolve().parents[2] / "scripts" / "train" /
          "run_tooluse_generation.sh")


def test_rf3950_stale_diagnostics_are_removed_before_paid_work() -> None:
    """An old local log must never be presented as evidence from a new pod."""
    code = RUNNER.read_text(encoding="utf-8")

    cleared = code.index('rm -f "$GENERATION_LOG_LOCAL" "$SHIM_LOG_LOCAL"')
    created = code.index('POD_ID=$("$PYBIN" scripts/train/_create_v04_pod.py')
    assert cleared < created


def test_rf3950_diagnostics_are_persisted_atomically_or_reported_missing() -> None:
    """A failed SCP cannot leave a pre-existing file looking freshly harvested."""
    code = RUNNER.read_text(encoding="utf-8")
    harvest = code[code.index("harvest_diagnostics(){"):code.index('[ "$RC" = 0 ]')]

    assert 'download="${destination}.download"' in harvest
    assert 'mv "$download" "$destination"' in harvest
    assert 'return "$failed"' in harvest
    assert 'diagnostics_harvested=$DIAGNOSTICS_HARVESTED' in code
