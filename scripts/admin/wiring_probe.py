#!/usr/bin/env python3
"""R-F1785 — Reusable §21 wiring-surface AST probe.

Run before Phase 1 and at each ~25-module batch audit (per the backfill plan,
docs/wiring_backfill_autonomous_plan_2026_06_22.md). Reuses wiring_harness's
own AST tooling, so the probe matches exactly what the gates enforce.

Usage:
    python scripts/admin/wiring_probe.py

Reports, across the harness's configured scan scope (TARGET_DIRS + TARGET_FILES):
  - total public functions (the backfill surface), sync vs async
  - generators (must stay HARD_EXEMPT — wrapping breaks stream/boot)
  - functions containing control-flow `raise` (GATE D gap-spam candidates)
  - already-@fail_wire'd modules
  - per-target counts + the heaviest modules
"""
from __future__ import annotations

import os
import sys
from collections import defaultdict

# Allow running from repo root without install.
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from aria_service.intel import wiring_harness as wh  # noqa: E402


def _iter_targets():
    """Yield (filepath, basename) for every .py file in the configured scope."""
    for d in wh.TARGET_DIRS:
        if not os.path.isdir(d):
            print(f"  !! MISSING TARGET_DIR: {d}")
            continue
        for fn in sorted(os.listdir(d)):
            if fn.endswith(".py"):
                yield os.path.join(d, fn), fn
    for f in wh.TARGET_FILES:
        if not os.path.isfile(f):
            print(f"  !! MISSING TARGET_FILE: {f}")
            continue
        yield f, os.path.basename(f)


def main() -> int:
    totals = defaultdict(int)
    gen_async, gen_sync, unexempt_gen = [], [], []
    raise_fns = 0
    already_wired: dict[str, int] = {}
    module_pub: dict[str, int] = {}

    for fpath, fname in _iter_targets():
        pubs = wh.scan_public_functions(fpath)
        wired = wh.fail_wire_decorators(fpath)
        module_pub[fpath] = len(pubs)
        for f in pubs:
            totals["public"] += 1
            totals[f["type"]] += 1
            if f["is_generator"]:
                totals["generator"] += 1
                rec = f"{fname}:{f['lineno']} {f['type']} {f['name']}()"
                (gen_async if f["type"] == "async" else gen_sync).append(rec)
                if not wh.is_exempt(fname, f["name"])[0]:
                    unexempt_gen.append(rec)
            if f["has_raise"]:
                raise_fns += 1
        if wired:
            already_wired[fname] = len(wired)

    print("=" * 70)
    print("§21 WIRING-SURFACE AST PROBE")
    print("=" * 70)
    print(f"\nPublic functions (backfill surface): {totals['public']}")
    print(f"  async: {totals['async']}   sync: {totals['sync']}")
    print(f"  generators (must stay HARD_EXEMPT): {totals['generator']} "
          f"(async {len(gen_async)} / sync {len(gen_sync)})")
    print(f"  contain control-flow 'raise' (GATE D candidates): {raise_fns}")
    print(f"\nAlready @fail_wire'd: {already_wired}")
    print(f"\nGenerators NOT in HARD_EXEMPT ({len(unexempt_gen)}) — LANDMINES:")
    for g in unexempt_gen:
        print(f"   {g}")
    print(f"\nTop 15 modules by public-fn count:")
    for m, c in sorted(module_pub.items(), key=lambda x: -x[1])[:15]:
        print(f"   {c:4d}  {m}")
    print(f"\nModules scanned: {len(module_pub)}")

    # Gate status (so the probe also reports green/red).
    results = wh.run_all_gates()
    print()
    wh.print_gate_results(results)
    return 0 if not any(results.values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
