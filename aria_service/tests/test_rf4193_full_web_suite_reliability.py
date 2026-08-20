"""R-F4193 capability coverage for the repaired web-suite sentinels."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
NODE = shutil.which("node")
SENTINELS = (
    "test/aria-app-server-fetch-deadline-rf4187.test.mjs",
    "test/aria-app-signin-cutover-rf4184.test.mjs",
    "test/aria-brain-top-truth-rf2988.test.mjs",
    "test/aria-brain-zero-evidence-honesty-rf3470.test.mjs",
    "test/brain-banner-403-honest-rf2876.test.mjs",
    "test/precommit-gate-performance-rf3556.test.mjs",
)


def test_rf4193_sentinel_manifest_names_real_files() -> None:
    """The cross-runner capability bridge must never silently exercise zero tests."""
    missing = [relative for relative in SENTINELS if not (ROOT / relative).is_file()]
    assert missing == []


@pytest.mark.skipif(NODE is None, reason="Node.js is required for the web capability suite")
def test_rf4193_repaired_web_failure_cluster_passes_end_to_end() -> None:
    """Drive every Node sentinel that failed in the full-suite regression."""
    result = subprocess.run(
        [NODE, "--test", *SENTINELS],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr

