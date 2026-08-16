"""R-F4036 capability tests for immutable paid-training driver execution."""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
RUNNER = ROOT / "scripts/train/run_immutable_shell.sh"
LAUNCHER = ROOT / "scripts/train/run_tooluse_protected_dpo_v1.sh"


def _bash() -> str | None:
    for candidate in (r"C:\Program Files\Git\bin\bash.exe", "/bin/bash", "/usr/bin/bash"):
        if Path(candidate).exists():
            return candidate
    return None


def test_inflight_driver_survives_source_mutation(tmp_path: Path) -> None:
    """The real immutable runner must isolate a live process from later edits."""
    bash = _bash()
    if bash is None:
        pytest.skip("bash unavailable")
    source = tmp_path / "mutable-driver.sh"
    source.write_text(
        "#!/usr/bin/env bash\n"
        "echo driver-started\n"
        "sleep 1\n"
        "echo original-driver-finished\n",
        encoding="utf-8",
    )
    source_arg = source.relative_to(ROOT).as_posix()
    process = subprocess.Popen(
        [bash, str(RUNNER), source_arg], cwd=ROOT,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )
    assert process.stdout is not None
    assert process.stdout.readline().strip() == "driver-started"
    source.write_text(") syntax-corrupted-after-launch\n", encoding="utf-8")
    stdout, stderr = process.communicate(timeout=10)

    assert process.returncode == 0, stderr
    assert "original-driver-finished" in stdout


def test_protected_paid_recipe_uses_immutable_driver_boundary() -> None:
    launcher = LAUNCHER.read_text(encoding="utf-8")
    assert "run_immutable_shell.sh scripts/train/run_tooluse_dpo.sh" in launcher
    assert "exec bash scripts/train/run_tooluse_dpo.sh" not in launcher
