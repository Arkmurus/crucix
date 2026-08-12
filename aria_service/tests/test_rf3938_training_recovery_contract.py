"""R-F3938 capability tests for crash-safe tool-use recovery controls."""
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


def test_driver_rejects_an_under_budget_cycle_before_paid_api_access() -> None:
    """The real driver must reject the crashed run's 1800s/7200s contract."""
    bash = _bash()
    if bash is None:
        pytest.skip("bash unavailable")

    env = dict(os.environ)
    env.update({"CYCLE_DEADLINE": "1800", "MIN_CYCLE_DEADLINE": "7200"})
    result = subprocess.run(
        [bash, str(DRIVER)],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )

    output = result.stdout + result.stderr
    assert result.returncode == 3
    assert "below required workload envelope" in output
    assert "API key unavailable" not in output


def test_driver_rejects_a_malformed_deadline_before_paid_api_access() -> None:
    bash = _bash()
    if bash is None:
        pytest.skip("bash unavailable")

    env = dict(os.environ)
    env.update({"CYCLE_DEADLINE": "7200:0", "MIN_CYCLE_DEADLINE": "7200"})
    result = subprocess.run(
        [bash, str(DRIVER)],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )

    output = result.stdout + result.stderr
    assert result.returncode == 3
    assert "must use integer seconds" in output
    assert "API key unavailable" not in output


def test_v10_recovery_declares_the_measured_eval_envelope() -> None:
    recovery = (ROOT / "scripts/train/run_tooluse_citation_contract_v10_recovery.sh").read_text(
        encoding="utf-8"
    )
    assert "MIN_CYCLE_DEADLINE=7200" in recovery
    assert "CYCLE_DEADLINE=14400" in recovery
    assert "CYCLE_DEADLINE=1800" not in recovery


def test_failed_cycle_reports_only_artifacts_that_were_recovered() -> None:
    driver = DRIVER.read_text(encoding="utf-8")
    assert "diagnostics harvested" not in driver
    assert "DIAGNOSTICS_SAVED=0" in driver
    assert "if persist_diagnostics; then DIAGNOSTICS_SAVED=1; fi" in driver
    assert "recovered intermediate=$INTERMEDIATE_SAVED diagnostics=$DIAGNOSTICS_SAVED logs=$LOGS_SAVED" in driver


def test_watchdog_arm_and_pod_stop_require_live_readback() -> None:
    """R-F3939: tokens and POST responses alone must never claim safety."""
    driver = DRIVER.read_text(encoding="utf-8")
    assert 'kill -0 "$(cat /workspace/eval/_watchdog_pid)"' in driver
    assert "curl.exe -s -X POST" in driver
    assert 'kill \\$(cat /workspace/eval/_watchdog_pid)' in driver
    assert 'log "watchdog arm verified"' in driver
    assert 'if [ "$(pod_state)" = NOT_RUNNING ]; then' in driver
    assert 'log "verified pod $POD_ID stopped"' in driver
    assert "stop unverified after 3 attempts" in driver
