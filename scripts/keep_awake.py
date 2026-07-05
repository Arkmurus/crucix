"""Keep this Windows machine awake while a long unattended job runs (operator
asked to keep the computer awake so the autonomous code-sovereign prep finishes).

Holds a system wake-lock via SetThreadExecutionState for as long as THIS process
is alive — non-destructive: it changes NO persistent power settings, and normal
sleep behaviour returns the instant this process exits. Re-asserts each minute
(belt-and-suspenders) and self-expires after --hours so it can never keep the
machine awake forever.

Run detached:
  nohup python scripts/keep_awake.py --hours 16 > data/eval_reports/_keep_awake.log 2>&1 &
"""
from __future__ import annotations

import argparse
import ctypes
import time

ES_CONTINUOUS = 0x80000000
ES_SYSTEM_REQUIRED = 0x00000001
ES_DISPLAY_REQUIRED = 0x00000002


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--hours", type=float, default=16.0)
    args = ap.parse_args()
    flags = ES_CONTINUOUS | ES_SYSTEM_REQUIRED | ES_DISPLAY_REQUIRED
    deadline = time.time() + args.hours * 3600
    try:
        set_state = ctypes.windll.kernel32.SetThreadExecutionState  # type: ignore[attr-defined]
    except Exception as exc:  # not Windows / no kernel32
        print(f"keep_awake: cannot set execution state ({exc}); exiting")
        return
    print(f"keep_awake: holding wake-lock for up to {args.hours}h")
    while time.time() < deadline:
        set_state(flags)
        time.sleep(60)
    set_state(ES_CONTINUOUS)  # release the lock
    print("keep_awake: released wake-lock (deadline reached)")


if __name__ == "__main__":
    main()
