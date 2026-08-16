"""R-F4037 capability coverage for repository-root snapshot execution."""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
LAUNCHER = ROOT / "scripts/train/run_tooluse_protected_dpo_v1.sh"


def _bash() -> str | None:
    for candidate in (r"C:\Program Files\Git\bin\bash.exe", "/bin/bash", "/usr/bin/bash"):
        if Path(candidate).exists():
            return candidate
    return None


def test_real_protected_launcher_preserves_repo_root_through_snapshot(tmp_path: Path) -> None:
    """The protected launcher must reach allocation from its immutable copy."""
    bash = _bash()
    if bash is None:
        pytest.skip("bash unavailable")
    python_proxy = tmp_path / "python-proxy.sh"
    python_proxy.write_text(
        "#!/usr/bin/env bash\n"
        "if [[ \"$1\" == \"scripts/train/_create_v04_pod.py\" ]]; then\n"
        "  echo '[pod-create] controlled secure-capacity rejection' >&2\n"
        "  exit 1\n"
        "fi\n"
        f"exec '{Path(os.sys.executable).as_posix()}' \"$@\"\n",
        encoding="utf-8",
    )
    python_proxy.chmod(0o755)
    env = dict(os.environ)
    env.update(
        {
            "PYBIN": str(python_proxy),
            "MAX_CREATE_TRIES": "1",
            "CREATE_RETRY_SECS": "0",
        }
    )

    result = subprocess.run(
        [bash, str(LAUNCHER)], cwd=ROOT, env=env,
        capture_output=True, text=True, timeout=60,
    )
    output = result.stdout + result.stderr

    assert result.returncode == 2
    assert "FATAL API key unavailable" not in output
    assert "training recipe approved" in output
    assert "controlled secure-capacity rejection" in output
    assert "BLOCKED no GPU capacity" in output
