"""R-F4034 capability coverage for the DPO driver's shell-safe allocation gate."""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
DRIVER = ROOT / "scripts/train/run_tooluse_dpo.sh"


def _bash() -> str | None:
    for candidate in (r"C:\Program Files\Git\bin\bash.exe", "/bin/bash", "/usr/bin/bash"):
        if Path(candidate).exists():
            return candidate
    return None


def test_real_driver_parses_without_the_hook_rejected_host_port_chain() -> None:
    """The shipped driver must parse and express its allocation gate explicitly."""
    bash = _bash()
    if bash is None:
        pytest.skip("bash unavailable")

    result = subprocess.run(
        [bash, "-n", str(DRIVER)], cwd=ROOT,
        capture_output=True, text=True, timeout=30,
    )
    source = DRIVER.read_text(encoding="utf-8")

    assert result.returncode == 0, result.stdout + result.stderr
    assert '[ -n "$HOST" ] && [ -n "$PORT" ] && break' not in source
    assert 'if [ -n "$HOST" ]; then' in source
    assert 'if [ -n "$PORT" ]; then break; fi' in source
