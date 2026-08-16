"""R-F4033 capability coverage for bounded DPO allocation diagnostics."""
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


def test_paid_driver_surfaces_bounded_pod_create_failure(tmp_path: Path) -> None:
    """The real paid driver must preserve the allocator's failure category."""
    bash = _bash()
    if bash is None:
        pytest.skip("bash unavailable")

    python_proxy = tmp_path / "python-proxy.sh"
    python_proxy.write_text(
        "#!/usr/bin/env bash\n"
        "if [[ \"$1\" == \"scripts/train/_create_v04_pod.py\" ]]; then\n"
        "  echo '[pod-create] HTTP 500: secure cloud machine lacks resources' >&2\n"
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
            "FRESH_BASE": "0",
            "EXPECTED_DPO_PAIRS": "20",
            "PROTECTED_DPO_AXES": (
                "tooluse_adverse,tooluse_contradiction,tooluse_resolution,"
                "tooluse_news_impact"
            ),
            "ADAPTER_LOCAL": "data/training/checkpoints/aria_tooluse_citation_phoenix_v3_failed_candidate.tgz",
            "ADAPTER_SHA256": "9ad61c99ca0e0c735ff9346085d5d6491e6a21820a9219f71f15bb951a51a31a",
            "PROBE_LOCAL": "data/training/aria_tooluse_curve_v5_probe.jsonl",
            "PROBE_SHA256": "72b7eca2a90db4d1e3a6a4448a2d17f3b2a0dd165f82ab96fd975720d0227c5c",
            "BASELINE_LOCAL": "data/eval_reports/aria_tooluse_curve_v5_sft_rf4031_rescored.json",
            "BASELINE_SHA256": "679ce658e04282aea977b5d91c8f897f0aa9a296bba9aca4472703b679ccd49d",
            "HELDOUT_BASELINE_LOCAL": "data/eval_reports/aria_tooluse_curve_sft_v5_heldout_rf4031_rescored.json",
            "HELDOUT_BASELINE_SHA256": "0c132d6a19f587960072bd8e423c9c9170595ce999c97f58c6113a3c66a4ac63",
            "DPO_LOCAL": "data/training/aria_tooluse_protected_dpo_v1.jsonl",
            "DPO_SHA256": "c48d9130528fe375e258d28d5dc8ef3f58e543c26271529e4917fb099325459a",
            "EVAL_LOCAL": "data/training/split_v1/eval.jsonl",
            "TRAIN_PROOF": "data/training/tooluse_novel_resolution_generation_queue.jsonl",
        }
    )
    result = subprocess.run(
        [bash, str(DRIVER)], cwd=ROOT, env=env,
        capture_output=True, text=True, timeout=60,
    )

    output = result.stdout + result.stderr
    assert result.returncode == 2
    assert "create rejected 1/1" in output
    assert "HTTP 500: secure cloud machine lacks resources" in output
    assert "BLOCKED no GPU capacity" in output
