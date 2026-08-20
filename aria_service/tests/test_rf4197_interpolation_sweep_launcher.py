"""R-F4197 capability tests for the preserved R-F4167 harvest path."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.train import run_tooluse_lora_interpolation_v2 as launcher


def _complete_report() -> str:
    return json.dumps({"complete": True, "total": 168, "rows": [{}] * 168})


def test_state_rejects_any_other_pod_or_phase(tmp_path: Path) -> None:
    state = tmp_path / "state"
    state.write_text(
        "POD_ID=some-new-pod\nPHASE=harvest_capacity_blocked\nPROVIDER_STATUS=EXITED\n",
        encoding="utf-8",
    )
    with pytest.raises(RuntimeError, match="unexpected pod"):
        launcher.read_state(state)
    state.write_text(
        f"POD_ID={launcher.EXPECTED_POD_ID}\nPHASE=running\nPROVIDER_STATUS=EXITED\n",
        encoding="utf-8",
    )
    with pytest.raises(RuntimeError, match="harvest boundary"):
        launcher.read_state(state)


def test_incomplete_report_is_rejected(tmp_path: Path) -> None:
    report = tmp_path / "arm.json"
    report.write_text('{"complete": true, "total": 168, "rows": []}', encoding="utf-8")
    with pytest.raises(RuntimeError, match="incomplete harvested report"):
        launcher._assert_complete_report(report)


def test_harvest_resumes_exact_pod_pulls_diagnostics_first_and_stops(
    monkeypatch, tmp_path: Path,
) -> None:
    state = {
        "POD_ID": launcher.EXPECTED_POD_ID,
        "PHASE": launcher.RESUMABLE_PHASE,
        "PROVIDER_STATUS": "EXITED",
    }
    calls: list[tuple[str, str]] = []
    pulls: list[str] = []
    monkeypatch.setattr(launcher, "read_state", lambda: state)
    key_file = tmp_path / "runpod_aria"
    key_file.write_text("test", encoding="utf-8")
    monkeypatch.setattr(launcher, "SSH_KEY", key_file)
    monkeypatch.setattr(launcher.runpod, "_key", lambda: "secret")
    statuses = iter(["EXITED", "EXITED"])

    def request(method, path, key, body=None):
        calls.append((method, path))
        if path.endswith("/start") or path.endswith("/stop"):
            return 200, {}
        return 200, {"desiredStatus": next(statuses)}

    def pull(host, port, remote, local):
        pulls.append(remote)
        content = _complete_report() if remote.endswith(".json") else "diagnostic"
        local.write_text(content, encoding="utf-8")
        return True

    monkeypatch.setattr(launcher.runpod, "_req", request)
    monkeypatch.setattr(launcher, "_wait_for_ssh", lambda key, pod_id: ("host", "22"))
    monkeypatch.setattr(launcher, "_pull", pull)
    monkeypatch.setattr(launcher, "DESTINATION", tmp_path)
    launcher.harvest()
    assert calls[0] == ("GET", f"/pods/{launcher.EXPECTED_POD_ID}")
    assert calls[1] == ("POST", f"/pods/{launcher.EXPECTED_POD_ID}/start")
    assert ("POST", f"/pods/{launcher.EXPECTED_POD_ID}/stop") in calls
    assert pulls[: len(launcher.DIAGNOSTICS)] == [remote for remote, _local in launcher.DIAGNOSTICS]
    assert all((tmp_path / name).is_file() for name in launcher.REPORT_NAMES)


def test_failed_harvest_still_stops_but_never_deletes_or_creates(
    monkeypatch, tmp_path: Path,
) -> None:
    state = {
        "POD_ID": launcher.EXPECTED_POD_ID,
        "PHASE": launcher.RESUMABLE_PHASE,
        "PROVIDER_STATUS": "EXITED",
    }
    monkeypatch.setattr(launcher, "read_state", lambda: state)
    key_file = tmp_path / "runpod_aria"
    key_file.write_text("test", encoding="utf-8")
    monkeypatch.setattr(launcher, "SSH_KEY", key_file)
    monkeypatch.setattr(launcher.runpod, "_key", lambda: "secret")
    calls: list[tuple[str, str]] = []

    def request(method, path, key, body=None):
        calls.append((method, path))
        if path.endswith("/start") or path.endswith("/stop"):
            return 200, {}
        return 200, {"desiredStatus": "EXITED"}

    monkeypatch.setattr(launcher.runpod, "_req", request)
    def fail_ssh(key, pod_id):
        raise RuntimeError("ssh failed")

    monkeypatch.setattr(launcher, "_wait_for_ssh", fail_ssh)
    with pytest.raises(RuntimeError, match="ssh failed"):
        launcher.harvest()
    assert ("POST", f"/pods/{launcher.EXPECTED_POD_ID}/stop") in calls
    assert not any(method == "DELETE" or path == "/pods" for method, path in calls)


def test_capacity_block_does_not_issue_stop_or_mutate_the_preserved_pod(
    monkeypatch, tmp_path: Path,
) -> None:
    state = {
        "POD_ID": launcher.EXPECTED_POD_ID,
        "PHASE": launcher.RESUMABLE_PHASE,
        "PROVIDER_STATUS": "EXITED",
    }
    monkeypatch.setattr(launcher, "read_state", lambda: state)
    key_file = tmp_path / "runpod_aria"
    key_file.write_text("test", encoding="utf-8")
    monkeypatch.setattr(launcher, "SSH_KEY", key_file)
    monkeypatch.setattr(launcher.runpod, "_key", lambda: "secret")
    calls: list[tuple[str, str]] = []

    def request(method, path, key, body=None):
        calls.append((method, path))
        if path.endswith("/start"):
            return 500, {"error": "not enough free GPUs on host"}
        return 200, {"desiredStatus": "EXITED"}

    monkeypatch.setattr(launcher.runpod, "_req", request)
    with pytest.raises(RuntimeError, match="not enough free GPUs"):
        launcher.harvest()
    assert calls == [("GET", f"/pods/{launcher.EXPECTED_POD_ID}"), ("POST", f"/pods/{launcher.EXPECTED_POD_ID}/start")]


def test_stop_readback_failure_is_never_hidden(monkeypatch, tmp_path: Path) -> None:
    state = {
        "POD_ID": launcher.EXPECTED_POD_ID,
        "PHASE": launcher.RESUMABLE_PHASE,
        "PROVIDER_STATUS": "EXITED",
    }
    key_file = tmp_path / "runpod_aria"
    key_file.write_text("test", encoding="utf-8")
    monkeypatch.setattr(launcher, "read_state", lambda: state)
    monkeypatch.setattr(launcher, "SSH_KEY", key_file)
    monkeypatch.setattr(launcher.runpod, "_key", lambda: "secret")

    def request(method, path, key, body=None):
        if method == "GET":
            return 200, {"desiredStatus": "EXITED"}
        return 200, {}

    monkeypatch.setattr(launcher.runpod, "_req", request)
    monkeypatch.setattr(launcher, "_wait_for_ssh", lambda key, pod_id: ("host", "22"))
    monkeypatch.setattr(launcher, "_pull", lambda host, port, remote, local: False)
    monkeypatch.setattr(launcher, "_stop_and_confirm", lambda key, pod_id: (_ for _ in ()).throw(RuntimeError("unconfirmed")))
    monkeypatch.setattr(launcher, "DESTINATION", tmp_path)

    with pytest.raises(RuntimeError, match="safety stop failed") as failure:
        launcher.harvest()
    assert isinstance(failure.value.__cause__, RuntimeError)
