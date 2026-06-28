"""R-F1310 — aria-forever: the self-healing process supervisor for the ARIA CLI.

The gap this closes (operator, 2026-06-04): when ARIA's CLI process crashed or
froze (e.g. the R-F1308 surrogate freeze), she stayed dead until a human noticed
— unacceptable for unattended/autonomous operation. This watchdog keeps her
alive:

  * launches `python -m aria_cli` as a child and watches it;
  * CRASH (non-zero exit) → relaunch with backoff;
  * STALL (the CLI's `busy` flag is set — a task is mid-flight — but the
    heartbeat file has not advanced for ARIA_SUPERVISOR_STALL_S seconds) →
    kill the whole process tree and relaunch;
  * CLEAN exit (operator typed /exit → exit code 0) → supervision ends;
  * restart-storm brake: more than ARIA_SUPERVISOR_MAX_RESTARTS restarts in a
    rolling hour → give up loudly (something is systematically broken —
    restarting harder won't fix it).

On every relaunch the child gets ARIA_RECOVERED=<reason> in its environment;
the REPL announces it and runs a recovery turn (re-read bridge, re-open task
list, verify any mid-flight write before redoing it) so ARIA RESUMES instead of
sitting at a blank prompt.

Liveness contract (written by cli.py):
  ~/.aria/sessions/heartbeat  — touched on REPL activity + every tool result /
                                 stream chunk while a turn runs (throttled);
  ~/.aria/sessions/busy       — exists while a turn is in flight.

Run:  python -m aria_cli.supervisor [aria args…]      (or aria-forever.cmd)
"""
from __future__ import annotations

import os
import subprocess
import sys
import time
from collections import deque
from pathlib import Path

SESSION_DIR = Path.home() / ".aria" / "sessions"

POLL_S = float(os.getenv("ARIA_SUPERVISOR_POLL_S", "10"))
STALL_S = float(os.getenv("ARIA_SUPERVISOR_STALL_S", "600"))
MAX_RESTARTS_PER_HOUR = int(os.getenv("ARIA_SUPERVISOR_MAX_RESTARTS", "6"))


def heartbeat_age(session_dir: Path = None) -> float:  # noqa: RUF013 — default resolved at call
    """Seconds since the CLI last touched its heartbeat; inf if never."""
    d = session_dir if session_dir is not None else SESSION_DIR
    try:
        return max(0.0, time.time() - (d / "heartbeat").stat().st_mtime)
    except Exception:  # noqa: BLE001 — missing file == no heartbeat
        return float("inf")


def is_busy(session_dir: Path = None) -> bool:
    """True while the CLI reports a turn in flight."""
    d = session_dir if session_dir is not None else SESSION_DIR
    return (d / "busy").exists()


def is_stalled(session_dir: Path = None, stall_s: float = None) -> bool:
    """Stalled = a task is mid-flight but the heartbeat stopped advancing.
    Idle-at-prompt (not busy) is NEVER a stall — waiting for the operator is
    legitimate."""
    s = stall_s if stall_s is not None else STALL_S
    return is_busy(session_dir) and heartbeat_age(session_dir) > s


def reset_liveness(session_dir: Path = None) -> None:
    """Clear a stale busy flag and freshen the heartbeat before (re)launch, so
    a crash that died mid-turn can't insta-kill the NEXT child."""
    d = session_dir if session_dir is not None else SESSION_DIR
    try:
        d.mkdir(parents=True, exist_ok=True)
        (d / "busy").unlink(missing_ok=True)
    except Exception:  # noqa: BLE001
        pass
    try:
        (d / "heartbeat").write_text(str(time.time()), encoding="utf-8")
    except Exception:  # noqa: BLE001
        pass


def _kill_tree(proc: "subprocess.Popen") -> None:
    """Kill the child AND its descendants (a stalled CLI may have live
    grandchildren — pytest, a deploy — that would otherwise linger)."""
    try:
        if sys.platform == "win32":
            # R-F2095 — 15s→5s so a wedged taskkill can't stall the supervisor's
            # stall-kill path as long (falls back to proc.kill() on timeout).
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                           capture_output=True, timeout=5)
        else:
            import os as _os
            import signal as _sig
            _os.killpg(_os.getpgid(proc.pid), _sig.SIGKILL)
    except Exception:  # noqa: BLE001 — last resort
        try:
            proc.kill()
        except Exception:
            pass


def run(argv: list[str] | None = None) -> int:
    argv = list(argv or [])
    restarts: deque = deque()
    reason = ""
    print("[aria-forever] supervising ARIA — clean /exit ends supervision; "
          "a crash or stall auto-restarts her.")
    while True:
        env = os.environ.copy()
        env["ARIA_SUPERVISED"] = "1"
        if reason:
            env["ARIA_RECOVERED"] = reason
        else:
            env.pop("ARIA_RECOVERED", None)
        reset_liveness()
        kwargs: dict = {}
        if sys.platform != "win32":
            kwargs["start_new_session"] = True
        child = subprocess.Popen([sys.executable, "-m", "aria_cli", *argv],
                                 env=env, **kwargs)

        stalled = False
        while True:
            try:
                child.wait(timeout=POLL_S)
                break  # child exited on its own
            except subprocess.TimeoutExpired:
                pass
            if is_stalled():
                print(f"[aria-forever] STALL — task in flight but heartbeat "
                      f"{heartbeat_age():.0f}s stale; killing the process tree.")
                _kill_tree(child)
                try:
                    child.wait(timeout=20)
                except Exception:  # noqa: BLE001
                    pass
                stalled = True
                break

        if not stalled and child.returncode == 0:
            print("[aria-forever] clean exit — supervision ends.")
            return 0

        reason = ("stall: heartbeat went stale while a task was running"
                  if stalled else f"crash: exit code {child.returncode}")

        now = time.time()
        restarts.append(now)
        while restarts and restarts[0] < now - 3600:
            restarts.popleft()
        if len(restarts) > MAX_RESTARTS_PER_HOUR:
            print(f"[aria-forever] {len(restarts)} restarts inside an hour — "
                  f"giving up. Something is systematically broken; investigate "
                  f"before restarting (last reason: {reason}).")
            return 1

        delay = min(5 * len(restarts), 60)
        print(f"[aria-forever] restarting in {delay:.0f}s ({reason})")
        time.sleep(delay)


def main() -> None:
    sys.exit(run(sys.argv[1:]))


if __name__ == "__main__":
    main()
