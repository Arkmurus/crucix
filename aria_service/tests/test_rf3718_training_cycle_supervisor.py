"""R-F3718 — paid cycles must be versioned, diagnosable, and fail closed."""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TRAIN = ROOT / "scripts" / "train"
DRIVER = TRAIN / "run_cycle_unattended.sh"


def _read(name: str) -> str:
    return (TRAIN / name).read_text(encoding="utf-8")


def test_all_tooluse_entrypoints_resolve_the_checkout_not_a_dead_clone() -> None:
    for name in ("smoke_cycle.sh", "tooluse_cycle.sh", "tooluse_launch.sh", "tooluse_harvest.sh"):
        source = _read(name)
        assert "/c/code/crucix" not in source
        assert "BASH_SOURCE[0]" in source
        assert 'cd "$REPO" ||' in source


def test_paid_recipe_preflights_train_serve_and_eval_runtime() -> None:
    source = _read("pod_tooluse_cycle.sh")
    preflight = source.index('log "verifying train -> serve -> eval runtime')
    model_load = source.index('log "verifying base architecture')
    assert preflight < model_load
    for dependency in ("bitsandbytes", "fastapi", "httpx", "peft", "torch", "trl", "uvicorn"):
        assert f'"{dependency}"' in source[preflight:model_load]
    assert "torch.cuda.is_available()" in source[preflight:model_load]


def test_unattended_driver_is_checked_in_and_uses_tristate_status() -> None:
    source = DRIVER.read_text(encoding="utf-8")
    for state in ("RUNNING", "NOT_RUNNING", "UNREADABLE"):
        assert state in source
    assert "desiredStatus') or '').upper()" in source
    assert "--max-time 20" in source
    assert "not assuming RUNNING" in source


def test_unreadable_control_plane_is_not_reported_as_running() -> None:
    source = DRIVER.read_text(encoding="utf-8")
    function = source[source.index("pod_state(){"):source.index('log "launching paid cycle"')]
    bash = shutil.which("bash") or r"C:\Program Files\Git\bin\bash.exe"
    script = f"curl(){{ return 22; }}; POD_ID=p KEY=k PYBIN=python; {function.rstrip()}\npod_state"
    result = subprocess.run([bash, "-c", script], capture_output=True, text=True, timeout=10)
    assert result.returncode == 0
    assert result.stdout.strip() == "UNREADABLE"


def test_driver_collects_on_the_completion_sentinel_before_api_state() -> None:
    source = DRIVER.read_text(encoding="utf-8")
    loop = source[source.index('for i in $(seq 1 "$MAX_POLLS")'):]
    assert loop.index("rc=$(sentinel)") < loop.index("state=$(pod_state)")
    assert 'bash "$SCRIPT_DIR/tooluse_harvest.sh"' in loop


def test_driver_collects_inside_the_watchdog_grace_before_exit() -> None:
    """Capability: deadline output is copied while pod-local disk is reachable."""
    source = DRIVER.read_text(encoding="utf-8")
    assert 'elapsed=$(( $(date -u +%s) - LAUNCHED_AT ))' in source
    deadline = source.index('if [ "$elapsed" -ge "$DEADLINE" ]')
    not_running = source.index("NOT_RUNNING)", deadline)
    window = source[deadline:not_running]
    assert "--leave-running" in window
    assert "collection attempt" in window
