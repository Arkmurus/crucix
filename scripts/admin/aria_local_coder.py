"""ARIA LOCAL CODER — run the autonomous coder ON THE COMPUTER.

Why this exists: ARIA's brain runs on fly, but the gold/fix pipeline must run
where the toolchain + repo live — pytest is NOT in the fly image, so the coder
CANNOT run reproduce tests / produce gold there. This runner executes
self_coder.fix_gap LOCALLY (repo + venv + pytest present) so ARIA can keep
building her own infrastructure: gap -> reproduce -> DeepSeek fix -> test ->
stage for review (and capture gold when a reproducible bug is fixed).

It bakes in every local-runtime fix discovered while landing first gold:
  - repo root on sys.path (so `aria_service` imports)
  - sys.executable for test subprocesses (R-F1928; literal "python" = system py)
  - state_store.connect() (opens the conn AND the write-queue worker)
  - a unique fresh state DB per run (no "database is locked" from prior runs)
  - os._exit at the end (state_store bg tasks keep the loop alive otherwise)

Modes:
  --list                 pull + show the brain's current coder gaps
  --gap-id <id>          pull that gap from the brain, run fix_gap locally, stage
  --canary               run the controlled coder_canary code task (deterministic
                         positive proof: produces a staged fix + gold)

Env: ARIA_INTERNAL_TOKEN (DeepSeek coder/llm + brain auth), ARIA_SERVICE_URL
(default https://aria-intel.fly.dev). Run with the VENV python so test
subprocesses inherit pytest + pytest-timeout.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

os.environ.setdefault("ARIA_REPO_PATH", str(REPO))
os.environ.setdefault("ARIA_CODER_TESTS_ENABLED", "1")
os.environ.setdefault("ARIA_STATE_BACKEND", "sqlite")
os.environ.setdefault("ARIA_SERVICE_URL", "https://aria-intel.fly.dev")


def _brain_gaps() -> list[dict]:
    import httpx
    tok = os.environ.get("ARIA_INTERNAL_TOKEN", "")
    url = os.environ["ARIA_SERVICE_URL"].rstrip("/") + "/api/aria/coder/gaps"
    r = httpx.get(url, headers={"Authorization": f"Bearer {tok}"}, timeout=40)
    r.raise_for_status()
    d = r.json()
    return d.get("gaps", d) if isinstance(d, dict) else d


def _rebuild_gap(d: dict):
    """Reconstruct a Gap object from the brain's serialized dict."""
    from aria_service.autonomous.gap_detector import Gap, GapType, GapSeverity
    # gap_type is a plain str on the dataclass; severity is a GapSeverity enum.
    sev = d.get("severity")
    try:
        severity = GapSeverity(int(sev)) if str(sev).isdigit() else GapSeverity[str(sev).upper()]
    except Exception:
        severity = GapSeverity.MEDIUM if hasattr(GapSeverity, "MEDIUM") else list(GapSeverity)[0]
    ev = d.get("evidence")
    if isinstance(ev, str):
        try:
            ev = json.loads(ev.replace("'", '"'))
        except Exception:
            ev = {"raw": ev}
    rf = d.get("related_files")
    if isinstance(rf, str):
        try:
            rf = json.loads(rf)
        except Exception:
            rf = []
    return Gap(
        gap_id=d.get("gap_id", "local_run"),
        gap_type=str(d.get("gap_type", "module_bug")),
        severity=severity,
        title=d.get("title", ""),
        description=d.get("description", ""),
        module=d.get("module", ""),
        related_files=rf or [],
        error_trace=(d.get("error_trace") if d.get("error_trace") not in ("None", None) else None),
        evidence=ev or {},
    )


_TMP_MOD = REPO / "aria_service" / "intel" / "_coder_canary_run.py"
_TMP_TEST = REPO / "aria_service" / "tests" / "test__coder_canary_run.py"

_TMP_MOD_BUGGY = '''"""Throwaway canary for the local-coder positive proof (auto-generated, removed
after the run). Carries one controlled bug for the coder to fix end-to-end."""
def add_bonus(base: int, bonus: int) -> int:
    """Return base + bonus. (Canary bug: subtracts instead of adds.)"""
    return base - bonus  # BUG: should be base + bonus
'''

_TMP_TEST_SRC = '''from aria_service.intel._coder_canary_run import add_bonus
def test_add_bonus():
    assert add_bonus(10, 5) == 15
def test_add_bonus_zero():
    assert add_bonus(7, 0) == 7
'''


def _make_temp_canary():
    """Write a fresh throwaway buggy module + reproduce test into the repo so the
    coder can fix it end-to-end. Returns (gap, cleanup). Cleanup removes both."""
    from aria_service.autonomous.gap_detector import Gap, GapType, GapSeverity
    _TMP_MOD.write_text(_TMP_MOD_BUGGY, encoding="utf-8")
    _TMP_TEST.write_text(_TMP_TEST_SRC, encoding="utf-8")

    def cleanup():
        for p in (_TMP_MOD, _TMP_TEST):
            try:
                p.unlink(missing_ok=True)
            except Exception:
                pass

    gap = Gap(
        gap_id="local_canary_proof",
        gap_type=GapType.MODULE_BUG if isinstance(GapType.MODULE_BUG, str) else "module_bug",
        severity=GapSeverity.HIGH,
        title="Failing test: test__coder_canary_run.py (add_bonus subtracts)",
        description=(
            "_coder_canary_run.add_bonus returns base - bonus instead of base + bonus. "
            "test__coder_canary_run.py reproduces this (FAILS on the bug)."
        ),
        module="aria_service/intel/_coder_canary_run.py",
        evidence={"test_file": "aria_service/tests/test__coder_canary_run.py",
                  "first_failing_test": "test_add_bonus"},
    )
    return gap, cleanup


async def _run(gap) -> int:
    from aria_service.autonomous.self_coder import ARIACoder
    from aria_service.intel import redis_store as rs
    from aria_service.intel import state_store as ss

    db = os.environ.get("ARIA_STATE_DB_PATH") or str(
        Path(os.environ.get("TEMP", "/tmp")) / f"_aria_local_coder_{int(time.time())}.db")
    os.environ["ARIA_STATE_DB_PATH"] = db
    Path(db).unlink(missing_ok=True)
    ok = await ss.connect()
    print(f"[local-coder] state_store connected: {ok} (db={db})")
    if not ok:
        print("BLOCKED: state_store could not connect (db locked?). Try a fresh shell.")
        return 2

    coder = ARIACoder(redis_client=rs, aria_service_url=os.environ["ARIA_SERVICE_URL"])
    print(f"[local-coder] running fix_gap on {gap.gap_id} "
          f"(type={gap.gap_type}, module={gap.module!r}) — stage-only, no deploy")
    res = await coder.fix_gap(gap, operator_initiated=False, force_stage_only=True)
    print(f"[local-coder] RESULT: success={res.success} r_number={res.r_number} "
          f"reason={res.failure_reason!r}")
    # Gold is written to the gold corpus by build_coder_reward_record — read the
    # authoritative row (training_pair on the result isn't always populated).
    gp = os.environ.get("ARIA_CODER_GOLD_PATH") or str(
        REPO / "data" / "aria_training" / "coder_verifiable_gold.jsonl")
    try:
        if Path(gp).exists():
            rows = [json.loads(l) for l in Path(gp).read_text(encoding="utf-8").splitlines() if l.strip()]
            if rows:
                last = rows[-1]
                rw = last.get("reward") or {}
                print(f"[local-coder] latest gold row: gold={last.get('gold')} "
                      f"reproduce_fail_to_pass={rw.get('reproduce_fail_to_pass')} "
                      f"tests={rw.get('tests_passed')}p/{rw.get('tests_failed')}f "
                      f"(corpus={gp})")
    except Exception as e:
        print(f"[local-coder] (could not read gold corpus: {e})")
    return 0 if res.success else 1


def main() -> int:
    ap = argparse.ArgumentParser(description="Run ARIA's coder locally on the computer")
    ap.add_argument("--list", action="store_true", help="show the brain's coder gaps")
    ap.add_argument("--gap-id", help="pull this gap from the brain and run it locally")
    ap.add_argument("--canary", action="store_true", help="run the controlled canary code task")
    ap.add_argument("--scan", action="store_true",
                    help="generate high-signal code-gap FUEL (failing-tests + reliability, RAG-grounded)")
    ap.add_argument("--source", choices=["both", "test", "reliability"], default="both",
                    help="--scan fuel source (default both)")
    ap.add_argument("--limit", type=int, default=10, help="--scan max gaps")
    ap.add_argument("--fix-top", action="store_true",
                    help="with --scan: run fix_gap locally on the top fuel gap")
    a = ap.parse_args()

    if a.scan:
        from code_gap_fuel import gather
        gaps = asyncio.run(gather(a.source, a.limit, enrich=True))
        print(f"[local-coder] {len(gaps)} code-gap fuel item(s) (source={a.source}):")
        for g in gaps:
            gold = "GOLD-ABLE" if (g.evidence or {}).get("gold_able") else "stage"
            rc = "RAG" if (g.evidence or {}).get("rag_context") else "—"
            print(f"  [{g.gap_type:11}] {gold:9} {rc} {g.module:42} {str(g.title)[:56]}")
        if a.fix_top and gaps:
            if not os.environ.get("ARIA_INTERNAL_TOKEN"):
                print("BLOCKED: ARIA_INTERNAL_TOKEN unset (needed for DeepSeek).")
                return 2
            print(f"[local-coder] --fix-top: running fix_gap on {gaps[0].gap_id} ...")
            return asyncio.run(_run(gaps[0]))
        return 0

    if not os.environ.get("ARIA_INTERNAL_TOKEN") and (a.list or a.gap_id):
        print("BLOCKED: ARIA_INTERNAL_TOKEN unset (needed for brain + DeepSeek).")
        return 2

    if a.list:
        gaps = _brain_gaps()
        from collections import Counter
        print(f"[local-coder] {len(gaps)} gaps; by type: {dict(Counter(g.get('gap_type','?') for g in gaps))}")
        for g in gaps[:25]:
            print(f"  {g.get('gap_id','?')[:16]}  {g.get('gap_type','?'):18} mod={str(g.get('module'))[:24]:24} {str(g.get('title'))[:50]}")
        return 0

    if a.canary:
        gap, cleanup = _make_temp_canary()
        try:
            return asyncio.run(_run(gap))
        finally:
            cleanup()
            print("[local-coder] temp canary files removed")

    if a.gap_id:
        gaps = _brain_gaps()
        match = next((g for g in gaps if str(g.get("gap_id", "")).startswith(a.gap_id)), None)
        if not match:
            print(f"BLOCKED: gap id {a.gap_id!r} not found among {len(gaps)} brain gaps.")
            return 2
        return asyncio.run(_run(_rebuild_gap(match)))

    ap.print_help()
    return 0


if __name__ == "__main__":
    _code = main()
    sys.stdout.flush()
    sys.stderr.flush()
    os._exit(int(_code or 0))
