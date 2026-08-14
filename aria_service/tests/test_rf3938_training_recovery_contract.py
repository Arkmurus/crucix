"""R-F3938 capability tests for crash-safe tool-use recovery controls."""
from __future__ import annotations

import os
import json
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


def test_driver_rejects_dpo_calibration_subject_contamination(tmp_path: Path) -> None:
    """R-F3992: the real paid driver must reject a trained-on probe subject."""
    bash = _bash()
    if bash is None:
        pytest.skip("bash unavailable")
    dpo = tmp_path / "dpo.jsonl"
    probe = tmp_path / "probe.jsonl"
    dpo.write_text(json.dumps({"subject": "Chemring Group plc"}) + "\n", encoding="utf-8")
    probe.write_text(json.dumps({"subject": "Chemring"}) + "\n", encoding="utf-8")
    env = dict(os.environ)
    env.update({
        "FRESH_BASE": "1",
        "DPO_LOCAL": str(dpo),
        "PROBE_LOCAL": str(probe),
        "EVAL_LOCAL": "data/training/split_v1/eval.jsonl",
        "TRAIN_PROOF": "data/training/tooluse_citation_phoenix_v2_generation_queue.jsonl",
        "EXPECTED_DPO_PAIRS": "1",
    })

    result = subprocess.run(
        [bash, str(DRIVER)], cwd=ROOT, env=env,
        capture_output=True, text=True, timeout=30,
    )

    output = result.stdout + result.stderr
    assert result.returncode == 3
    assert "DPO and calibration overlap on 1 subject" in output
    assert "training recipe approved" not in output


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
    assert 'if [ -n "$INTERMEDIATE_LOCAL" ]; then' in driver
    assert "if persist_intermediate; then INTERMEDIATE_SAVED=1; fi" in driver
    assert 'persist_report /workspace/eval/aria_tooluse_dpo_eval.json "${REPORT_LOCAL}.failed"' in driver
    assert "REPORT_SAVED=0" in driver
    assert "recovered intermediate=$INTERMEDIATE_SAVED report=$REPORT_SAVED diagnostics=$DIAGNOSTICS_SAVED logs=$LOGS_SAVED" in driver


def test_failed_adapter_downloads_resume_and_phoenix_retains_them() -> None:
    """R-F3987: a failed gate must retain paid training without promoting it."""
    driver = DRIVER.read_text(encoding="utf-8")
    wrapper = (ROOT / "scripts/train/run_tooluse_citation_phoenix_v3.sh").read_text(
        encoding="utf-8"
    )

    assert "printf 'reget %s %s\\n' \"$remote\" \"$download\"" in driver
    assert 'remote_sha=$(TSSH -p "$PORT"' in driver
    assert '[ "$local_sha" = "$remote_sha" ] || return 1' in driver
    assert 'download="${2}.download"' in driver
    assert 'tar -tzf "$download"' in driver
    assert 'INTERMEDIATE_SAVED=0' in driver
    assert 'INTERMEDIATE_LOCAL=data/training/checkpoints/aria_tooluse_citation_phoenix_v3_failed_candidate.tgz' in wrapper
    assert 'INTERMEDIATE_REMOTE=/workspace/eval/aria_tooluse_dpo_adapter.tgz' in wrapper
    output_line = next(line for line in wrapper.splitlines() if "OUTPUT_LOCAL=" in line)
    assert "failed_candidate.tgz" not in output_line


def test_driver_wires_verified_parent_heldout_baseline_to_pod() -> None:
    """R-F3993: completeness alone cannot promote a paid candidate."""
    driver = DRIVER.read_text(encoding="utf-8")
    assert 'REQUIRED_FILES+=("$HELDOUT_BASELINE_LOCAL")' in driver
    assert "immutable held-out baseline hash mismatch" in driver
    assert "verified parent held-out baseline" in driver
    assert "held-out baseline surface does not match evaluation" in driver
    assert 'RSCP "$HELDOUT_BASELINE_LOCAL" /workspace/eval/aria_tooluse_parent_heldout.json' in driver
    assert 'remote held-out baseline hash mismatch' in driver
    assert 'POD_ENV="$POD_ENV HELDOUT_BASELINE=/workspace/eval/aria_tooluse_parent_heldout.json"' in driver


def test_unreadable_upload_continues_only_with_fresh_recorded_pod_liveness() -> None:
    """R-F3994: intermittent control visibility must not discard resumable bytes."""
    driver = DRIVER.read_text(encoding="utf-8")
    unreadable = driver.index('if [ "$STATE" = UNREADABLE ]; then')
    live_probe = driver.index("'echo upload-pod-alive'", unreadable)
    promote = driver.index("STATE=RUNNING", live_probe)
    stop = driver.index('[ "$STATE" = RUNNING ] || break', promote)
    assert unreadable < live_probe < promote < stop
    assert "recorded pod SSH liveness unverified" in driver


def test_host_deadline_covers_pod_before_remote_watchdog_is_reachable() -> None:
    """R-F4000: a wedged Windows SSH child must not leave a paid pod exposed."""
    driver = DRIVER.read_text(encoding="utf-8")
    state = driver.index('echo "POD_ID=$POD_ID"')
    host_arm = driver.index('log "host pre-arm watchdog armed', state)
    ssh_gate = driver.index("FATAL SSH unstable", host_arm)
    remote_arm = driver.index('log "watchdog arm verified"', host_arm)
    assert state < host_arm < ssh_gate
    assert state < host_arm < remote_arm
    assert 'PREARM_DEADLINE="${PREARM_DEADLINE:-900}"' in driver
    assert 'curl.exe -s -X POST "$API/pods/$POD_ID/stop"' in driver
    assert driver.count("disarm_prearm_watchdog") >= 4


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


def test_ephemeral_pod_transports_isolate_reused_host_keys() -> None:
    """R-F3983: a recycled RunPod endpoint must not break cycle observability."""
    driver = DRIVER.read_text(encoding="utf-8")
    contract = 'SSH_HOST_KEYS="-o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null"'

    assert contract in driver
    assert 'SSH="ssh -i $KEYF $SSH_HOST_KEYS ' in driver
    assert 'scp -i "$KEYF" $SSH_HOST_KEYS ' in driver
    assert 'sftp -b - -i "$KEYF" $SSH_HOST_KEYS ' in driver
    assert "scp -i \"$KEYF\" -o StrictHostKeyChecking=no" not in driver
    assert "sftp -b - -i \"$KEYF\" -o StrictHostKeyChecking=no" not in driver
