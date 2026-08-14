"""R-F3400 — the pod self-stop watcher must stop the pod even when nothing tells it to.

Both watchers waited on a sentinel with an UNBOUNDED loop:

    while [ ! -f "$STATUS" ]; do sleep 30; done

The sentinel is written by the cycle script's EXIT trap. An EXIT trap does not
run if the process is SIGKILLed — OOM-killer on a 7B in 4-bit, a container disk
that fills mid-checkpoint, a kernel panic — and it never runs at all if the
script dies before installing it. In every one of those cases the watcher loops
forever and the GPU bills until a human notices. CLAUDE.md §24 calls a pod the
operator discovers still burning the worst outcome; this is the code path that
produces it.

The watcher is the LAST line of defence: it exists precisely for the case where
the local driver died. A last line of defence that depends on the thing it is
defending against having exited cleanly is not one.

These tests run the real script with a stubbed `curl` on PATH, so they assert
the actual stop call, not a proxy for it.
"""
from __future__ import annotations

import os
import subprocess
import textwrap
from pathlib import Path

import pytest

WATCHERS = ["pod_selfstop_watch.sh", "pod_selfstop_watch_v04.sh"]


def _posix(p: Path) -> str:
    """Windows path -> the /c/... mount form bash needs.

    `C:/tmp/bin` inside a bash PATH SPLITS AT THE COLON into `C` and `/tmp/bin`,
    so the entry silently never resolves. That produced a red test against a
    correct script — the worst kind.
    """
    s = p.as_posix()
    if len(s) > 1 and s[1] == ":":
        s = "/" + s[0].lower() + s[2:]
    return s


def _bash() -> str | None:
    for c in (r"C:\Program Files\Git\bin\bash.exe", "/bin/bash", "/usr/bin/bash"):
        if Path(c).exists():
            return c
    return None


@pytest.fixture
def rig(tmp_path):
    """A fake pod: stubbed curl that records the URL it was called with."""
    bash = _bash()
    if bash is None:
        pytest.skip("bash unavailable")

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    calls = tmp_path / "curl_calls.txt"
    (bin_dir / "curl").write_text(
        textwrap.dedent(f"""\
        #!/usr/bin/env bash
        echo "$@" >> "{_posix(calls)}"
        exit 0
        """),
        encoding="utf-8", newline="\n",
    )
    (bin_dir / "curl").chmod(0o755)
    for d in ("workspace/eval", "workspace/logs"):
        (tmp_path / d).mkdir(parents=True, exist_ok=True)
    return {"bash": bash, "tmp": tmp_path, "bin": bin_dir, "calls": calls}


def _run(rig, watcher: str, env_extra: dict, timeout: int = 90):
    root = Path(__file__).resolve().parents[2]
    src = (root / "scripts" / "train" / watcher).read_text(encoding="utf-8")
    # Redirect the pod's absolute /workspace paths into the sandbox.
    src = src.replace("/workspace", _posix(rig["tmp"] / "workspace"))
    script = rig["tmp"] / watcher
    script.write_text(src, encoding="utf-8", newline="\n")

    env = dict(os.environ)
    env.update({"POD_ID": "pod-under-test", "RP_KEY": "k"})
    env.update(env_extra)
    # Let BASH own its PATH. Setting a POSIX directory into a Windows PATH (with
    # ';' separators) is silently mangled by the Git-Bash conversion, and the
    # stub curl is then never found — the test would fail while the script was
    # correct, which is the worst kind of red.
    cmd = f'export PATH="{_posix(rig["bin"])}:$PATH"; exec bash "{_posix(script)}"'
    return subprocess.run([rig["bash"], "-c", cmd],
                          env=env, capture_output=True, text=True, timeout=timeout)


@pytest.mark.parametrize("watcher", WATCHERS)
def test_pod_is_stopped_when_the_sentinel_never_arrives(rig, watcher):
    """The defect: a SIGKILLed cycle writes no sentinel and the pod bills forever."""
    _run(rig, watcher, {"DEADLINE": "3", "GRACE": "0", "POLL": "1"}, timeout=90)
    calls = rig["calls"].read_text(encoding="utf-8") if rig["calls"].exists() else ""
    assert "/stop" in calls, "watcher never issued a stop — the pod would bill indefinitely"
    assert "pod-under-test" in calls


@pytest.mark.parametrize("watcher", WATCHERS)
def test_the_deadline_stop_is_recorded_as_a_deadline_not_a_clean_finish(rig, watcher):
    """A timed-out run must be distinguishable from a completed one in the log."""
    _run(rig, watcher, {"DEADLINE": "3", "GRACE": "0", "POLL": "1"}, timeout=90)
    log = (rig["tmp"] / "workspace" / "logs")
    text = "\n".join(p.read_text(encoding="utf-8", errors="replace")
                     for p in log.glob("*.log"))
    assert "DEADLINE" in text.upper(), "a deadline stop must say so, not look like success"


@pytest.mark.parametrize("watcher", WATCHERS)
def test_normal_completion_still_stops_the_pod(rig, watcher):
    """The existing behaviour must survive: sentinel present -> verdict -> stop."""
    status = {"pod_selfstop_watch.sh": "_v0_2_status",
              "pod_selfstop_watch_v04.sh": "_cycle_status"}[watcher]
    (rig["tmp"] / "workspace" / "eval" / status).write_text("0", encoding="utf-8")
    _run(rig, watcher, {"DEADLINE": "600", "GRACE": "0", "POLL": "1"}, timeout=90)
    calls = rig["calls"].read_text(encoding="utf-8") if rig["calls"].exists() else ""
    assert "/stop" in calls


@pytest.mark.parametrize("watcher", WATCHERS)
def test_deadline_has_a_bounded_default(rig, watcher):
    """An unset DEADLINE must not mean 'wait forever' — that is the defect."""
    root = Path(__file__).resolve().parents[2]
    src = (root / "scripts" / "train" / watcher).read_text(encoding="utf-8")
    assert "DEADLINE" in src, f"{watcher} has no deadline at all"
    assert "DEADLINE:-" in src, f"{watcher}'s deadline has no default"
    # and the unbounded wait must be gone
    assert "while [ ! -f \"$STATUS\" ]; do sleep 30; done" not in src, (
        f"{watcher} still has the unbounded wait")


# --------------------------------------------------------------------------
# R-F3445 — the deadline must not destroy artefacts that already exist
# --------------------------------------------------------------------------

@pytest.mark.parametrize("watcher", WATCHERS)
def test_deadline_gives_a_collection_window_when_reports_exist(rig, watcher):
    """The nine-second loss.

    A trained eval report was written at 21:36:50; this watcher stopped the pod
    at 21:36:59 on its deadline; container disk is ephemeral, so the report was
    gone before it could be pulled. R-F3400's comment said "nothing is coming:
    stop NOW" - correct for a HUNG cycle, wrong for a merely SLOW one that has
    already produced collectible output.

    With artefacts present the deadline must allow a bounded collection window
    instead of stopping instantly.
    """
    (rig["tmp"] / "workspace" / "eval" / "tooluse_eval_trained.json").write_text(
        '{"total": 168}', encoding="utf-8")
    r = _run(rig, watcher, {"DEADLINE": "2", "GRACE": "0", "POLL": "1",
                            "COLLECT_GRACE": "6"}, timeout=90)
    log = "\n".join(p.read_text(encoding="utf-8", errors="replace")
                    for p in (rig["tmp"] / "workspace" / "logs").glob("*.log"))
    assert "COLLECT" in log.upper() or "collection" in log, (
        "the deadline path must announce a collection window when output exists")


@pytest.mark.parametrize("watcher", WATCHERS)
def test_deadline_still_stops_immediately_when_there_is_nothing_to_save(rig, watcher):
    """The anti-hang property must survive: no artefacts, no waiting."""
    import time as _t

    t0 = _t.time()
    _run(rig, watcher, {"DEADLINE": "2", "GRACE": "0", "POLL": "1",
                        "COLLECT_GRACE": "600"}, timeout=90)
    elapsed = _t.time() - t0
    calls = rig["calls"].read_text(encoding="utf-8") if rig["calls"].exists() else ""
    assert "/stop" in calls, "a hung cycle with no output must still be stopped"
    assert elapsed < 60, (
        f"waited {elapsed:.0f}s with nothing to collect - the collection window "
        f"must not apply when there are no artefacts")


def test_v04_deadline_preserves_a_staged_dpo_adapter(rig):
    """R-F3981: the real DPO output is a tgz, not an eval JSON.

    The DPO cycle archives its trained adapter before held-out evaluation.  The
    watchdog must treat that paid artifact as collectible and leave the pod up
    for the configured harvest window instead of destroying ephemeral storage.
    """
    adapter = rig["tmp"] / "workspace" / "eval" / "aria_tooluse_dpo_adapter.tgz"
    adapter.write_bytes(b"staged adapter")

    import time as _t

    t0 = _t.monotonic()
    _run(
        rig,
        "pod_selfstop_watch_v04.sh",
        {"DEADLINE": "2", "GRACE": "0", "POLL": "1", "COLLECT_GRACE": "3"},
        timeout=90,
    )
    elapsed = _t.monotonic() - t0
    calls = rig["calls"].read_text(encoding="utf-8") if rig["calls"].exists() else ""
    log = (rig["tmp"] / "workspace" / "logs" / "_selfstop_v04.log").read_text(
        encoding="utf-8"
    )

    assert elapsed >= 4, "watchdog skipped the adapter collection window"
    assert "COLLECTION window 3s" in log
    assert "/stop" in calls, "bounded collection must still end by stopping the pod"


@pytest.mark.parametrize("watcher", WATCHERS)
def test_the_collection_window_is_itself_bounded(rig, watcher):
    """A window that never closes is the unbounded pod again."""
    src = (Path(__file__).resolve().parents[2] / "scripts" / "train"
           / watcher).read_text(encoding="utf-8")
    assert "COLLECT_GRACE" in src
    assert "COLLECT_GRACE:-" in src, "the collection window needs a bounded default"
