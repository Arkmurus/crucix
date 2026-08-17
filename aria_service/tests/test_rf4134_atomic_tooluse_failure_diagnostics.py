"""R-F4134 — a remote failure sentinel carries its diagnosis atomically."""
from __future__ import annotations

import base64
import gzip
from pathlib import Path

import pytest

from scripts.train.capture_tooluse_cycle_status import main, parse_cycle_observation


ROOT = Path(__file__).resolve().parents[2]


def _observation(rc: int, evidence: str = "") -> str:
    if rc == 0:
        return "0\n"
    payload = base64.b64encode(gzip.compress(evidence.encode())).decode()
    return (
        f"{rc}\n__ARIA_FAILURE_BUNDLE_BEGIN__\n{payload}\n"
        "__ARIA_FAILURE_BUNDLE_END__\n"
    )


def test_nonzero_sentinel_persists_the_same_session_diagnosis(tmp_path: Path) -> None:
    observation = tmp_path / "observation.txt"
    diagnosis = tmp_path / "failure.txt"
    observation.write_text(_observation(1, "CUDA out of memory\n"), encoding="utf-8")

    assert main([
        "--input", str(observation), "--failure-out", str(diagnosis),
    ]) == 0
    assert diagnosis.read_text(encoding="utf-8") == "CUDA out of memory\n"


def test_nonzero_sentinel_without_diagnosis_is_rejected() -> None:
    with pytest.raises(ValueError, match="lacks one bounded evidence bundle"):
        parse_cycle_observation("1\n")


def test_compressed_diagnosis_cannot_expand_beyond_its_bound() -> None:
    oversized = "x" * 512_001
    with pytest.raises(ValueError, match="expands beyond its bound"):
        parse_cycle_observation(_observation(1, oversized))


def test_driver_reads_status_and_failure_bundle_in_one_ssh_session() -> None:
    driver = (ROOT / "scripts/train/run_tooluse_dpo.sh").read_text(encoding="utf-8")
    observe = driver.index("observe_cycle(){")
    sentinel = driver.index("_cycle_status", observe)
    bundle = driver.index("__ARIA_FAILURE_BUNDLE_BEGIN__", sentinel)
    parser = driver.index("scripts.train.capture_tooluse_cycle_status", bundle)
    decision = driver.index('if [ -n "$RC" ]; then break; fi', parser)

    assert observe < sentinel < bundle < parser < decision
    assert 'rm -f "$OBSERVATION_LOCAL" "$FAILURE_DIAGNOSTICS_LOCAL"' in driver
    assert "atomic_diagnostics=$ATOMIC_DIAG_SAVED" in driver
