"""Capability tests for verified safety controls on paid generation pods."""
from pathlib import Path


RUNNER = (Path(__file__).resolve().parents[2] / "scripts" / "train" /
          "run_tooluse_generation.sh")


def test_rf3948_generation_watchdog_is_proven_alive() -> None:
    """The paid generation runner must read back the watchdog PID as live."""
    code = RUNNER.read_text(encoding="utf-8")

    assert "arm_watchdog(){" in code
    assert "kill -0" in code
    assert "watchdog arm verified" in code
    assert "watchdog arm not live" in code


def test_rf3948_generation_cleanup_proves_terminal_pod_state() -> None:
    """A stop request is not cleanup until RunPod reports a terminal state."""
    code = RUNNER.read_text(encoding="utf-8")

    release = code[code.index("release(){"):code.index("trap release EXIT")]
    assert "for attempt in 1 2 3" in release
    assert '"$(pod_state)" = NOT_RUNNING' in release
    assert "verified pod $POD_ID stopped" in release
    assert "stop unverified after 3 attempts" in release
