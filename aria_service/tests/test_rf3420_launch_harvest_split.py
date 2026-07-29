"""R-F3420 — a killed local driver must not strand, waste, or lose a pod.

WHAT HAPPENED. The full cycle ran ~35 minutes and the local driver was KILLED
externally — the same behaviour this repo already records for background pytest
on this machine. Its `trap stop_pod EXIT` never ran, and the pod billed until it
was stopped by hand: about $0.87.

The "three independent stops" were not independent. The local trap needs the
driver to exit cleanly; the bounded poll lives inside that same driver; and the
on-pod watchdog waits for the completion sentinel or a 6-HOUR deadline, so with
the cycle still legitimately running neither fired. Every layer ultimately
depended on the driver surviving or the cycle finishing. A driver killed
mid-run was covered by none of them.

THE FIX IS TO REMOVE THE DEPENDENCY, not to lengthen a timeout. Launch is short
(create, push, arm, start, record, exit) and harvest is a separate short command
(pull, stop). Neither needs a process to live for forty minutes, so a kill costs
nothing: the pod finishes its work and stops itself, and harvest runs whenever.

THE DEADLINE IS NOW THE BACKSTOP, so it has to be tight. Launch deliberately
does NOT stop the pod on exit — that is the entire point — which means an
unharvested pod is bounded only by the watchdog. Six hours of a $1.49/hr GPU is
$9 for a run nobody collected; one cycle envelope plus margin is the honest
bound.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

TRAIN = Path(__file__).resolve().parents[2] / "scripts" / "train"
LAUNCH = TRAIN / "tooluse_launch.sh"
HARVEST = TRAIN / "tooluse_harvest.sh"


def _src(p: Path) -> str:
    if not p.exists():
        pytest.skip(f"{p.name} not present")
    return p.read_text(encoding="utf-8")


def _lines(p: Path) -> list[str]:
    return _src(p).splitlines()


def _first(lines: list[str], needle: str) -> int:
    for i, l in enumerate(lines):
        if needle in l:
            return i
    raise AssertionError(f"not found: {needle!r}")


# --------------------------------------------------------------------------
# launch: short-lived, and deliberately does NOT stop the pod
# --------------------------------------------------------------------------

def test_launch_does_not_stop_the_pod_on_exit():
    """The whole point: the work must outlive the launcher.

    Comment lines are excluded — the header explains WHY there is no exit trap,
    and matching that text would fail against a correct script.
    """
    code = [l for l in _lines(LAUNCH) if not l.lstrip().startswith("#")]
    assert not any("trap stop_pod EXIT" in l for l in code), (
        "launch must NOT stop the pod on exit — the pod is meant to keep working")


def test_launch_still_releases_a_pod_it_failed_to_set_up():
    """A pod that never received the work is a leak, not a running job."""
    src = _src(LAUNCH)
    assert "/stop" in src, "a half-set-up pod must be released"
    assert re.search(r"(scp|push|arm|launch).{0,400}?/stop", src, re.S | re.I), (
        "the release path must be reachable from a setup failure")


def test_launch_has_no_long_poll_loop():
    """A loop that waits for the cycle reintroduces the process that gets killed."""
    src = _src(LAUNCH)
    assert "POLL_CAP" not in src
    assert "sleep 120" not in src, "launch must not sit and wait for the cycle"


def test_launch_records_the_handoff_state():
    """Harvest cannot find the pod without it, and the driver may not survive."""
    src = _src(LAUNCH)
    for field in ("POD_ID", "HOST", "PORT"):
        assert field in src
    assert "STATE_FILE" in src, "launch must persist the handoff to disk"


def test_launch_verifies_the_work_actually_started():
    """Exiting on a pod that is doing nothing burns until the deadline."""
    src = _src(LAUNCH)
    assert "STARTED" in src, "launch must confirm the runner started"


# --------------------------------------------------------------------------
# the deadline is now the only backstop, so it must be tight
# --------------------------------------------------------------------------

def test_the_watchdog_deadline_bounds_one_cycle_not_a_day():
    """Six hours of an unharvested $1.49/hr GPU is $9 nobody collected."""
    src = _src(LAUNCH)
    m = re.search(r"DEADLINE=\$\{DEADLINE:-(\d+)\}", src)
    assert m, "launch must set an explicit watchdog deadline"
    seconds = int(m.group(1))
    # R-F3445 - both bounds are now measured rather than assumed. The old 7200
    # ceiling was itself a guess; the observed envelope is ~110 min (base eval 16
    # + SFT 12 + trained eval 45 + generation 37), so a 2h cap would truncate a
    # healthy cycle - which is exactly what happened, at a cost of $4.07.
    assert seconds <= 10800, f"deadline {seconds}s is too loose to be a backstop"
    assert seconds >= 6600, (
        f"deadline {seconds}s is below the measured ~110min envelope and would "
        f"truncate a healthy cycle")


def test_the_deadline_default_shows_its_arithmetic():
    """A bound set by feel is what cost a run; the derivation must be visible."""
    src = _src(LAUNCH)
    i = src.index("DEADLINE=${DEADLINE:-")
    window = src[max(0, i - 900):i]
    assert "min" in window and ("110" in window or "envelope" in window), (
        "the deadline must be derived from measured stage timings, not chosen")


def test_the_collection_window_is_passed_to_the_watchdog():
    """R-F3445 - the watchdog cannot honour a window it was never told about."""
    # Anchored on the ARMING construct, not the filename: the first occurrence
    # of the watcher's name is its scp push. Matching that instead of the launch
    # is a mistake this suite has now made three times.
    lines = _lines(LAUNCH)
    arm = next(l for l in lines
               if "setsid nohup bash /workspace/pod_selfstop_watch_v04.sh" in l)
    assert "COLLECT_GRACE=" in arm, (
        "the arming line must pass COLLECT_GRACE through - the watchdog cannot "
        "honour a window it was never told about")


def test_the_watchdog_is_armed_before_the_work_starts():
    """ARM vs START, not the scp push order.

    The push order is irrelevant and comparing it is a mistake already made once
    in the R-F3401 suite; both files are copied before either runs.
    """
    lines = _lines(LAUNCH)
    arm = _first(lines, "setsid nohup bash /workspace/pod_selfstop_watch_v04.sh")
    start = _first(lines, "setsid nohup bash /workspace/pod_tooluse_cycle.sh")
    assert arm < start


def test_a_watchdog_that_fails_to_arm_aborts_the_launch():
    """Without it, an unharvested pod has NO bound at all.

    `die` counts: it releases the pod and exits, which is the stronger abort.
    """
    lines = _lines(LAUNCH)
    i = _first(lines, "grep -q ARMED")
    window = " ".join(lines[i:i + 2])
    assert "die " in window or "exit " in window, (
        "launch must refuse to start work it cannot bound")
    assert any("die()" in l or "die(){" in l for l in lines), (
        "die() must release the pod, not just exit")


# --------------------------------------------------------------------------
# harvest: short, repeatable, and it always stops the pod
# --------------------------------------------------------------------------

def test_harvest_pulls_the_run_log_before_any_result_file():
    """R-F3415 — the log is what a FAILED cycle leaves behind."""
    lines = _lines(HARVEST)
    pulls = [i for i, l in enumerate(lines) if l.strip().startswith("PULL ")]
    assert pulls, "harvest must pull something"
    logs = [i for i in pulls if ".log" in lines[i]]
    assert logs and min(logs) == min(pulls), (
        "the run log must be pulled before the result files")


def test_harvest_stops_the_pod():
    src = _src(HARVEST)
    assert "/stop" in src


def test_harvest_stops_the_pod_even_when_the_cycle_is_unfinished():
    """Called early, it must still release the pod rather than leave it running."""
    src = _src(HARVEST)
    assert "--leave-running" in src or "KEEP" in src, (
        "there must be an explicit way to inspect without stopping, so the "
        "DEFAULT can safely be to stop")


def test_harvest_reads_the_state_file_and_accepts_an_override():
    src = _src(HARVEST)
    assert "STATE_FILE" in src
    assert "POD_ID" in src


def test_harvest_never_reports_success_without_a_result():
    src = _src(HARVEST)
    assert "NO RESULT" in src or "not pulled" in src


def test_neither_script_depends_on_a_forty_minute_process():
    """The property the whole R-number exists for."""
    for p in (LAUNCH, HARVEST):
        src = _src(p)
        for m in re.finditer(r"seq 1 (\d+)", src):
            assert int(m.group(1)) <= 60, (
                f"{p.name}: a {m.group(1)}-tick loop is the long-lived process again")
