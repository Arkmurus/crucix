"""R-F4043 capability coverage for exact-pod resumable DPO recovery."""
from __future__ import annotations

import os
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


def test_real_driver_rejects_malformed_recovery_pod_before_api_access() -> None:
    """A malformed identifier must fail before credentials or network are consulted."""
    bash = _bash()
    if bash is None:
        pytest.skip("bash unavailable")

    env = os.environ.copy()
    env["EXISTING_POD_ID"] = "bad/pod-id"
    result = subprocess.run(
        [bash, str(DRIVER)], cwd=ROOT, env=env,
        capture_output=True, text=True, timeout=30,
    )

    output = result.stdout + result.stderr
    assert result.returncode == 3
    assert "existing pod id is malformed" in output
    assert "API key unavailable" not in output


def test_existing_pod_path_bypasses_creation_and_enters_resumable_upload() -> None:
    """The exact-pod branch starts that pod and retains the resumable upload path."""
    source = DRIVER.read_text(encoding="utf-8")
    recovery = source.index('if [ -n "$EXISTING_POD_ID" ]; then')
    start = source.index('curl.exe -fsS -X POST "$API/pods/$POD_ID/start"', recovery)
    creation = source.index('scripts/train/_create_v04_pod.py', recovery)
    upload = source.index("SFTP_UPLOAD=reput", creation)

    assert recovery < start < creation < upload
    assert 'POD_ID="$EXISTING_POD_ID"' in source[recovery:creation]
    assert 'scripts/train/_create_v04_pod.py' not in source[recovery:creation]
