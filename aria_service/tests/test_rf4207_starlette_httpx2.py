"""R-F4207 capability gates for Starlette's HTTPX2 TestClient migration."""

from pathlib import Path
import subprocess
import sys


REPO = Path(__file__).resolve().parents[2]
DEV_REQUIREMENTS = REPO / "requirements-dev.txt"


def test_httpx2_is_a_pinned_dev_only_dependency():
    """The maintained TestClient transport belongs in the dev manifest only."""
    dev = DEV_REQUIREMENTS.read_text(encoding="utf-8")
    runtime = (REPO / "aria_service" / "requirements.txt").read_text(encoding="utf-8")

    assert "httpx2==2.12.0" in dev
    assert "httpx2" not in runtime.lower()


def test_fastapi_testclient_import_has_no_starlette_deprecation_warning():
    """Drive a fresh interpreter and make the observed fallback warning fatal."""
    proc = subprocess.run(
        [
            sys.executable,
            "-W",
            "error::starlette.exceptions.StarletteDeprecationWarning",
            "-c",
            "from fastapi.testclient import TestClient; print(TestClient.__module__)",
        ],
        cwd=REPO,
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "starlette.testclient" in proc.stdout
