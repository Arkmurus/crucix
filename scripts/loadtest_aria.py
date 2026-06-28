#!/usr/bin/env python3
"""R-F2100 — ARIA load-test harness (Phase 0.5 of the scaling readiness plan).

Find the REAL concurrency knee instead of guessing it. Ramps concurrent requests
at increasing levels and reports latency percentiles + success rate + throughput,
so you know where the single-worker brain actually saturates BEFORE building any
multi-worker/multi-machine tier (docs/aria_scaling_readiness_plan_2026_06_28.md).

SAFETY (read this):
  - --dry-run (DEFAULT) hits ONLY /health/live — zero LLM cost, zero state writes,
    safe against live prod. Use this first to baseline event-loop responsiveness.
  - --mode chat sends REAL chat turns → costs DeepSeek tokens AND loads the single
    worker that serves real users. Point it at a CANARY/staging, or use tiny
    concurrency against prod off-hours. It refuses to run >--max-cost-guard chats
    without --i-understand-cost.
  - --mode dd is intentionally NOT supported here (a DD is ~$ and ~100s each — do
    not load-test DDs against prod).

Usage:
  python scripts/loadtest_aria.py                         # safe dry-run ramp vs prod /health/live
  python scripts/loadtest_aria.py --base https://aria-intel.fly.dev --levels 5,10,25,50
  python scripts/loadtest_aria.py --mode chat --levels 2,5 --i-understand-cost   # REAL cost

Reads ARIA_INTERNAL_TOKEN from .env for authed endpoints.
"""
from __future__ import annotations

import argparse
import asyncio
import os
import statistics
import time
from pathlib import Path


def _load_token() -> str:
    env = Path(__file__).resolve().parent.parent / ".env"
    try:
        for line in env.read_text(encoding="utf-8", errors="ignore").splitlines():
            if line.startswith("ARIA_INTERNAL_TOKEN="):
                return line.split("=", 1)[1].strip().strip('"').strip("'")
    except Exception:
        pass
    return os.getenv("ARIA_INTERNAL_TOKEN", "")


async def _one(client, mode, base, token):
    """Fire one request; return (ok: bool, latency_s: float, note: str)."""
    import httpx
    t0 = time.monotonic()
    try:
        if mode == "dry-run":
            r = await client.get(f"{base}/health/live", timeout=30)
            ok = r.status_code == 200 and "build_rev" in r.text
            return ok, time.monotonic() - t0, str(r.status_code)
        # chat mode — real LLM turn (cheap, trivial prompt; still costs tokens)
        r = await client.post(
            f"{base}/api/aria/chat",
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            json={"message": "Reply with one short sentence: load-test ping.",
                  "session_id": f"loadtest_{int(t0*1000)}"},
            timeout=120,
        )
        ok = r.status_code == 200
        return ok, time.monotonic() - t0, str(r.status_code)
    except Exception as e:  # noqa: BLE001
        return False, time.monotonic() - t0, type(e).__name__


async def _level(client, mode, base, token, n):
    results = await asyncio.gather(*[_one(client, mode, base, token) for _ in range(n)])
    lat = sorted(r[1] for r in results)
    oks = sum(1 for r in results if r[0])
    def pct(p):
        if not lat:
            return 0.0
        i = min(len(lat) - 1, int(round((p / 100) * (len(lat) - 1))))
        return lat[i]
    notes = {}
    for r in results:
        if not r[0]:
            notes[r[2]] = notes.get(r[2], 0) + 1
    return {
        "n": n, "ok": oks, "fail": n - oks,
        "p50": pct(50), "p95": pct(95), "p99": pct(99),
        "max": max(lat) if lat else 0.0,
        "mean": statistics.mean(lat) if lat else 0.0,
        "fail_notes": notes,
    }


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="https://aria-intel.fly.dev")
    ap.add_argument("--mode", choices=["dry-run", "chat"], default="dry-run")
    ap.add_argument("--levels", default="5,10,25,50",
                    help="comma-separated concurrency levels to ramp through")
    ap.add_argument("--max-cost-guard", type=int, default=10,
                    help="refuse >this many total chat requests without --i-understand-cost")
    ap.add_argument("--i-understand-cost", action="store_true",
                    help="acknowledge that --mode chat spends DeepSeek tokens + loads prod")
    args = ap.parse_args()

    import httpx
    levels = [int(x) for x in args.levels.split(",") if x.strip()]
    token = _load_token()

    if args.mode == "chat":
        total = sum(levels)
        if total > args.max_cost_guard and not args.i_understand_cost:
            print(f"REFUSING: --mode chat would send {total} real LLM requests (> {args.max_cost_guard}).")
            print("Re-run with --i-understand-cost (and prefer a canary, not live prod).")
            return
        if not token:
            print("No ARIA_INTERNAL_TOKEN found (.env) — chat mode needs it.")
            return

    print(f"ARIA load test · base={args.base} · mode={args.mode} · levels={levels}")
    print(f"{'conc':>5} {'ok':>4} {'fail':>4} {'p50':>7} {'p95':>7} {'p99':>7} {'max':>7}  notes")
    async with httpx.AsyncClient() as client:
        for n in levels:
            r = await _level(client, args.mode, args.base, token, n)
            print(f"{r['n']:>5} {r['ok']:>4} {r['fail']:>4} "
                  f"{r['p50']:>6.2f}s {r['p95']:>6.2f}s {r['p99']:>6.2f}s {r['max']:>6.2f}s  "
                  f"{r['fail_notes'] or ''}")
            # Knee signal: if p95 > 5s or any failures appear, you've found saturation.
            if r["p95"] > 5.0 or r["fail"] > 0:
                print(f"      ^ knee signal at concurrency {n}: p95={r['p95']:.1f}s, {r['fail']} failures")
            await asyncio.sleep(2)  # let the brain settle between levels
    print("\nInterpretation: the level where p95 spikes / failures begin is your single-worker knee.")
    print("Build Phase 1+ only when real usage approaches it (docs/aria_scaling_readiness_plan_2026_06_28.md).")


if __name__ == "__main__":
    asyncio.run(main())
