"""R-F3401 — the smoke-cycle driver's safety ORDER is load-bearing.

Each assertion below is a sequencing property where getting it backwards costs
real money or real data, and where nothing at runtime would complain:

  * pre-flight must run BEFORE the pod is created, or the gate is decoration
    and §24's "cancelled, not run" becomes "run, then discovered".
  * the self-stop watcher must be armed BEFORE the work is launched, or a
    driver that dies in the gap leaves a pod with no independent stop.
  * results must be pulled BEFORE the stop, because the cycle is volume-free
    (R-F1516) and container disk is ephemeral — after the stop there is nothing
    left to pull.
  * `trap stop_pod EXIT` must exist, so an error or a Ctrl-C still stops the pod.

These are checked as ORDER, not as wording, so the script can be rewritten
freely as long as the sequence holds.
"""
from __future__ import annotations

from pathlib import Path

import pytest

DRIVER = Path(__file__).resolve().parents[2] / "scripts" / "train" / "smoke_cycle.sh"
RUNNER = Path(__file__).resolve().parents[2] / "scripts" / "train" / "pod_smoke_sft.sh"


@pytest.fixture(scope="module")
def driver() -> list[str]:
    if not DRIVER.exists():
        pytest.skip("smoke_cycle.sh not present")
    return DRIVER.read_text(encoding="utf-8").splitlines()


def _first(lines: list[str], needle: str) -> int:
    for i, l in enumerate(lines):
        if needle in l:
            return i
    raise AssertionError(f"not found in driver: {needle!r}")


def test_preflight_runs_before_the_pod_is_created(driver):
    assert _first(driver, "preflight_cycle") < _first(driver, "_create_v04_pod.py")


def test_a_failed_preflight_aborts_rather_than_continuing(driver):
    i = _first(driver, "preflight_cycle")
    window = "\n".join(driver[i:i + 6])
    assert "exit" in window, "pre-flight failure must abort, not just print"


def test_watcher_is_armed_before_the_work_is_launched(driver):
    """ARM vs LAUNCH, not the scp push order — the push order is irrelevant."""
    arm = _first(driver, "setsid nohup bash /workspace/pod_selfstop_watch_v04.sh")
    launch = _first(driver, "setsid nohup bash /workspace/pod_smoke_sft.sh")
    assert arm < launch


def test_a_watcher_that_fails_to_arm_blocks_the_run(driver):
    i = _first(driver, "ARMED")
    assert "exit 1" in "\n".join(driver[i:i + 2]), (
        "refusing to run unattended without a watcher is the point")


def test_results_are_pulled_before_the_pod_is_stopped(driver):
    last_pull = max(i for i, l in enumerate(driver) if l.startswith("PULL "))
    final_stop = max(i for i, l in enumerate(driver) if l.strip() == "stop_pod")
    assert last_pull < final_stop, "container disk is ephemeral — pull first"


def test_an_exit_trap_stops_the_pod(driver):
    assert any("trap stop_pod EXIT" in l for l in driver)


def test_the_poll_loop_is_bounded(driver):
    assert any("POLL_CAP" in l and "seq 1" in l for l in driver), (
        "an unbounded driver poll is the R-F3400 defect in another place")


def test_the_cycle_is_volume_free(driver):
    """A networkVolumeId region-locks the pod to the DC that deleted pods."""
    code = [l for l in driver if not l.lstrip().startswith("#")]
    assert not any("networkVolumeId" in l for l in code), (
        "a volume pins the pod to one datacenter; the comment explaining that is fine")


def test_runner_always_writes_the_sentinel_the_watcher_waits_on():
    if not RUNNER.exists():
        pytest.skip("pod_smoke_sft.sh not present")
    src = RUNNER.read_text(encoding="utf-8")
    assert "trap finish EXIT" in src
    assert "_cycle_status" in src


def test_runner_refuses_to_claim_success_without_an_adapter():
    if not RUNNER.exists():
        pytest.skip("pod_smoke_sft.sh not present")
    src = RUNNER.read_text(encoding="utf-8")
    assert "adapter_config.json" in src and "FATAL" in src, (
        "a cycle that produced no LoRA is a failure, not a pass")


# --------------------------------------------------------------------------
# R-F3414 — a detached launch must not hold the SSH channel open
# --------------------------------------------------------------------------

DRIVERS = ["smoke_cycle.sh", "tooluse_cycle.sh"]


@pytest.mark.parametrize("name", DRIVERS)
def test_detached_launch_redirects_every_fd(name):
    """`cd X && ... &` backgrounds an AND-LIST, which bash runs in a SUBSHELL.

    Only the inner command carried redirects, so the subshell kept ssh's stdout
    open, ssh never saw EOF, and the launch hung for the full 75s TSSH timeout
    before being reported as a bare "FATAL launch". A pod was created, paid for
    and thrown away for it.

    The rule: the backgrounded command must carry its own >log 2>&1 </dev/null
    and must not be wrapped in a `cd ... &&` prefix. A runner that needs a
    working directory sets it itself.
    """
    p = Path(__file__).resolve().parents[2] / "scripts" / "train" / name
    if not p.exists():
        pytest.skip(f"{name} not present")
    for line in p.read_text(encoding="utf-8").splitlines():
        if "setsid nohup bash" not in line:
            continue
        assert ">/workspace/logs/" in line, f"{name}: detached launch without stdout redirect"
        assert "2>&1" in line, f"{name}: detached launch without stderr redirect"
        assert "</dev/null" in line, f"{name}: detached launch without stdin redirect"
        assert "cd " not in line.split("setsid")[0], (
            f"{name}: `cd ... &&` before a backgrounded launch creates a subshell "
            f"that holds the ssh channel open")


@pytest.mark.parametrize("name", DRIVERS)
def test_a_failed_launch_says_why(name):
    """A bare "FATAL launch" cost a diagnosis cycle."""
    p = Path(__file__).resolve().parents[2] / "scripts" / "train" / name
    if not p.exists():
        pytest.skip(f"{name} not present")
    src = p.read_text(encoding="utf-8")
    if "FATAL launch" in src:
        assert "FATAL launch -" in src or "FATAL launch —" in src, (
            f"{name}: launch failure must name the likely cause")


@pytest.mark.parametrize("name", DRIVERS)
def test_the_run_log_is_pulled_before_any_result_file(name):
    """R-F3415 — a failure must leave evidence behind.

    Container disk is ephemeral, so the stop destroys the only copy of the run
    log. A driver that pulls only success artefacts turns every failure into a
    paid-for pod that produced nothing to diagnose from.
    """
    p = Path(__file__).resolve().parents[2] / "scripts" / "train" / name
    if not p.exists():
        pytest.skip(f"{name} not present")
    lines = p.read_text(encoding="utf-8").splitlines()
    pulls = [i for i, l in enumerate(lines) if l.startswith("PULL ")]
    if not pulls:
        pytest.skip(f"{name} has no PULL block")
    logs = [i for i in pulls if "logs/" in lines[i] and ".log" in lines[i]]
    assert logs, f"{name}: no log is ever pulled — a failure leaves no evidence"
    assert min(logs) == min(pulls), (
        f"{name}: a result file is pulled before the run log; the log is what a "
        f"FAILED cycle leaves behind")
