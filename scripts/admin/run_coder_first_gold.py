"""R-F1926 — drive ARIA's autonomous coder over the controlled canary bug to
produce its FIRST verifiable gold row, end-to-end, locally.

Why local: the coder's gold gate runs the reproduce test via pytest, and pytest
is NOT in the fly prod image — so the live coder can't produce gold there. But
gold is OFFLINE SFT fuel ("trains on gold=True only"), and CodebaseReader reads
ARIA_REPO_PATH while TestRunner runs pytest in a sandboxed repo COPY — so a local
run faithfully exercises the real pipeline (gap -> reproduce FAIL -> DeepSeek fix
-> PASS -> gold) without touching the working tree.

Usage: python scripts/admin/run_coder_first_gold.py
Env (set by this script if absent): ARIA_REPO_PATH, ARIA_CODER_TESTS_ENABLED=1,
ARIA_CODER_GOLD_PATH (scratch), ARIA_SERVICE_URL. Requires ARIA_INTERNAL_TOKEN
(for the DeepSeek-backed /api/aria/coder/llm) — read from the environment/.env.
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))
os.environ.setdefault("ARIA_REPO_PATH", str(REPO))
os.environ.setdefault("ARIA_CODER_TESTS_ENABLED", "1")
os.environ.setdefault("ARIA_STATE_BACKEND", "sqlite")
os.environ.setdefault("ARIA_SERVICE_URL", "https://aria-intel.fly.dev")
_GOLD = os.environ.setdefault(
    "ARIA_CODER_GOLD_PATH",
    str(REPO / "data" / "aria_training" / "_first_gold_scratch.jsonl"),
)

CANARY_MODULE = "aria_service/intel/coder_canary.py"


async def main() -> int:
    if not os.environ.get("ARIA_INTERNAL_TOKEN"):
        print("BLOCKED: ARIA_INTERNAL_TOKEN unset — the DeepSeek coder/llm call will refuse.")
        return 2

    from aria_service.autonomous.self_coder import ARIACoder
    from aria_service.autonomous.gap_detector import Gap, GapType, GapSeverity
    from aria_service.intel import redis_store as rs
    from aria_service.intel import state_store as ss

    # Standalone harness: lifespan never ran, so open the sqlite state_store
    # connection ourselves (else the staging write fails "no connection").
    _connected = await ss.connect()
    print(f"[harness] state_store connected: {_connected} (db={os.environ.get('ARIA_STATE_DB_PATH', 'default')})")

    gap = Gap(
        gap_id="rf1926_canary_first_gold",
        gap_type=GapType.MODULE_BUG,
        severity=GapSeverity.HIGH,
        title="Failing test: test_coder_canary_rf1926.py (clamp_percentage upper bound)",
        description=(
            "coder_canary.clamp_percentage returns 0.0 for values > 100 instead of "
            "clamping to 100.0. The documented contract clamps over-range values to "
            "100.0. test_coder_canary_rf1926.py reproduces this (FAILS on the bug)."
        ),
        module=CANARY_MODULE,
        evidence={
            "test_file": "aria_service/tests/test_coder_canary_rf1926.py",
            "first_failing_test": "test_clamp_percentage_upper_bound",
            "source": "rf1926_controlled_canary",
        },
    )

    coder = ARIACoder(
        redis_client=rs,
        aria_service_url=os.environ["ARIA_SERVICE_URL"],
    )

    print(f"[harness] gold path: {_GOLD}")
    print(f"[harness] driving fix_gap on {gap.gap_id} (module={CANARY_MODULE}) ...")
    result = await coder.fix_gap(gap, operator_initiated=False, force_stage_only=True)
    print(f"[harness] FixResult: success={result.success} "
          f"r_number={result.r_number} reason={result.failure_reason!r}")

    # Inspect the gold corpus for a real gold row from this run.
    gp = Path(_GOLD)
    if not gp.exists():
        print(f"[harness] NO gold file written at {_GOLD} — no gold this run.")
        return 1
    rows = [json.loads(l) for l in gp.read_text(encoding="utf-8").splitlines() if l.strip()]
    # gold-gate fields live in the nested `reward` block (build_coder_reward_record).
    gold_rows = [
        r for r in rows
        if r.get("gold") is True and (r.get("reward") or {}).get("reproduce_fail_to_pass") is True
    ]
    print(f"[harness] gold corpus rows={len(rows)} | REAL gold (gold+reproduce_fail_to_pass)={len(gold_rows)}")
    if gold_rows:
        g = gold_rows[-1]
        print("[harness] ✅ FIRST GOLD captured:")
        print(json.dumps({k: g.get(k) for k in
              ("gap_id", "gold", "tests_ran", "reproduce_fail_to_pass",
               "all_green", "stage_ok", "r_number", "instruction")}, indent=2, default=str)[:1200])
        return 0
    print("[harness] rows present but none are REAL gold — inspecting last row:")
    if rows:
        print(json.dumps(rows[-1], indent=2, default=str)[:1200])
    return 1


if __name__ == "__main__":
    _code = asyncio.run(main())
    # state_store + coder background tasks keep the event loop alive after
    # fix_gap returns, so a plain exit hangs (then gets timeout-killed, leaving
    # a sqlite lock). Force immediate termination once we have the result.
    sys.stdout.flush()
    sys.stderr.flush()
    os._exit(int(_code or 0))
