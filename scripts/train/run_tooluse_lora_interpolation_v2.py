"""R-F4197 — resume and harvest the preserved R-F4167 RunPod sweep.

This recovery tool never creates or deletes a pod. It resumes only the pod in
the durable handoff, pulls diagnostics before results, validates each complete
168-row arm into a temporary file, publishes the reports atomically, and stops
the pod after every successful resume attempt.
"""
from __future__ import annotations

import json
import subprocess
import time
from pathlib import Path

from scripts.train import runpod_derisk as runpod

ROOT = Path(__file__).resolve().parents[2]
STATE_FILE = ROOT / "data/eval_reports/.tooluse_lora_interpolation_v2_pod_state"
DESTINATION = ROOT / "data/eval_reports"
EXPECTED_POD_ID = "ydfgy06ik7fzca"
RESUMABLE_PHASE = "harvest_capacity_blocked"
REPORT_NAMES = (
    "aria_tooluse_lora_interpolation_v2_alpha_0125.json",
    "aria_tooluse_lora_interpolation_v2_alpha_025.json",
    "aria_tooluse_lora_interpolation_v2_alpha_05.json",
)
DIAGNOSTICS = (
    ("/workspace/eval/_cycle_status", "aria_rf4167_cycle_status"),
    ("/workspace/logs/_cycle_watch.log", "aria_rf4167_cycle_watch.log"),
    ("/workspace/logs/interpolation_v2_0125_eval.log", "aria_rf4167_alpha_0125_eval.log"),
    ("/workspace/logs/interpolation_v2_025_eval.log", "aria_rf4167_alpha_025_eval.log"),
    ("/workspace/logs/interpolation_v2_05_eval.log", "aria_rf4167_alpha_05_eval.log"),
)
SSH_KEY = Path.home() / ".ssh/runpod_aria"
SSH_OPTIONS = (
    "-o", "StrictHostKeyChecking=no", "-o", "UserKnownHostsFile=/dev/null",
    "-o", "LogLevel=ERROR", "-o", "ConnectTimeout=15",
)
SSH_READY_DEADLINE_SECONDS = 15 * 60


def read_state(path: Path = STATE_FILE) -> dict[str, str]:
    """Read and validate the exact durable R-F4167 recovery handoff."""
    if not path.is_file():
        raise RuntimeError(f"recovery state missing: {path}")
    state: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        if not raw_line or raw_line.lstrip().startswith("#"):
            continue
        key, separator, value = raw_line.partition("=")
        if not separator or not key or not value:
            raise RuntimeError(f"malformed recovery state line: {raw_line!r}")
        state[key] = value
    if state.get("POD_ID") != EXPECTED_POD_ID:
        raise RuntimeError("recovery state identifies an unexpected pod")
    if state.get("PHASE") != RESUMABLE_PHASE:
        raise RuntimeError("recovery state is not paused at the harvest boundary")
    if state.get("PROVIDER_STATUS") != "EXITED":
        raise RuntimeError("recovery state does not record a safely exited pod")
    return state


def _ssh(host: str, port: str, command: str, *, timeout: int) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["ssh", "-i", str(SSH_KEY), *SSH_OPTIONS, "-p", port, f"root@{host}", command],
        capture_output=True, text=True, timeout=timeout, check=False,
    )


def _pull(host: str, port: str, remote: str, local: Path) -> bool:
    completed = subprocess.run(
        ["scp", "-i", str(SSH_KEY), *SSH_OPTIONS, "-P", port, f"root@{host}:{remote}", str(local)],
        capture_output=True, text=True, timeout=10 * 60, check=False,
    )
    return completed.returncode == 0


def _pod_status(pod: dict) -> str:
    return str(pod.get("desiredStatus") or pod.get("status") or "").upper()


def _wait_for_ssh(key: str, pod_id: str) -> tuple[str, str]:
    started_at = time.monotonic()
    while time.monotonic() - started_at < SSH_READY_DEADLINE_SECONDS:
        code, pod = runpod._req("GET", f"/pods/{pod_id}", key)
        if code != 200:
            raise RuntimeError(f"pod readback failed with HTTP {code}")
        host = str(pod.get("publicIp") or "")
        port = str((pod.get("portMappings") or {}).get("22") or "")
        if host and port:
            probe = _ssh(host, port, "printf ready", timeout=20)
            if probe.returncode == 0 and probe.stdout == "ready":
                return host, port
        if _pod_status(pod) not in {"RUNNING", "CREATED"}:
            raise RuntimeError(f"pod left resumable state: {_pod_status(pod) or 'UNKNOWN'}")
        time.sleep(10)
    raise RuntimeError("resumed pod never exposed working SSH")


def _assert_complete_report(path: Path) -> None:
    data = json.loads(path.read_text(encoding="utf-8"))
    rows = data.get("rows")
    if data.get("complete") is not True or data.get("total") != 168:
        raise RuntimeError(f"incomplete harvested report: {path.name}")
    if not isinstance(rows, list) or len(rows) != 168:
        raise RuntimeError(f"incomplete harvested report: {path.name}")


def _stop_and_confirm(key: str, pod_id: str) -> None:
    code, response = runpod._req("POST", f"/pods/{pod_id}/stop", key)
    if code not in {200, 201, 204}:
        raise RuntimeError(f"pod stop failed with HTTP {code}: {json.dumps(response)[:200]}")
    for _ in range(12):
        code, pod = runpod._req("GET", f"/pods/{pod_id}", key)
        if code == 200 and _pod_status(pod) == "EXITED":
            return
        time.sleep(5)
    raise RuntimeError("pod stop was not confirmed by provider readback")


def harvest() -> None:
    """Resume the preserved pod once, harvest verified outputs, and stop it."""
    state = read_state()
    if not SSH_KEY.is_file():
        raise RuntimeError("RunPod SSH private key is unavailable")
    key = runpod._key()
    pod_id = state["POD_ID"]
    code, pod = runpod._req("GET", f"/pods/{pod_id}", key)
    if code != 200 or _pod_status(pod) != "EXITED":
        raise RuntimeError("provider readback does not confirm the recorded pod is EXITED")
    code, response = runpod._req("POST", f"/pods/{pod_id}/start", key)
    if code not in {200, 201, 202}:
        detail = str(response.get("error") or response.get("message") or response)[:300]
        raise RuntimeError(f"preserved pod resume failed with HTTP {code}: {detail}")

    harvest_error: Exception | None = None
    stop_error: Exception | None = None
    try:
        host, port = _wait_for_ssh(key, pod_id)
        DESTINATION.mkdir(parents=True, exist_ok=True)
        for remote, local_name in DIAGNOSTICS:
            partial = DESTINATION / f"{local_name}.download"
            if _pull(host, port, remote, partial):
                partial.replace(DESTINATION / local_name)
        staged: list[tuple[Path, Path]] = []
        for name in REPORT_NAMES:
            partial = DESTINATION / f"{name}.download"
            if not _pull(host, port, f"/workspace/eval/{name}", partial):
                raise RuntimeError(f"required report unavailable: {name}")
            _assert_complete_report(partial)
            staged.append((partial, DESTINATION / name))
        for partial, final in staged:
            partial.replace(final)
    except Exception as exc:
        harvest_error = exc
    finally:
        try:
            _stop_and_confirm(key, pod_id)
        except Exception as exc:
            stop_error = exc
    if stop_error is not None:
        raise RuntimeError(f"pod safety stop failed after harvest attempt: {stop_error}") from harvest_error
    if harvest_error is not None:
        raise harvest_error


def main() -> int:
    harvest()
    print("R-F4167 preserved reports harvested and pod stop confirmed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
