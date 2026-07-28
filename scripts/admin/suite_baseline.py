"""R-F3373 — make the CLAUDE.md §16 baseline rule mechanically checkable.

§16 says "New R-numbers must not add to the failing-test count." That rule has
never had a machine to enforce it, for two independent reasons found on
2026-07-28:

  1. CI does not run the suite. Per aria_service/tests/conftest.py (R-F927), the
     "Test ARIA Python service" workflow runs ONLY test_imports.py and
     test_lifespan_smoke.py with a minimal dep set — no torch, no
     sentence-transformers — so deploys are not gated on the full suite at all.

  2. The only place it runs is a developer box, and there it is fragile: four
     consecutive BACKGROUND pytest runs were terminated externally (reaching 3%,
     19%, 11% and 0 bytes — no summary, no exit code) on a machine with 7.7 GB of
     RAM and ~0.5 GB free. Foreground runs are unaffected.

     CONTROLLED 2026-07-28: a minimal background process (a 25-minute heartbeat,
     near-zero memory, same background mechanism) ran to completion and exited 0
     — three times longer than pytest ever survived. So background execution
     itself is NOT the problem and there is no blanket time limit; an earlier
     full-suite background run also survived 1h20m. The kill tracks the WORKLOAD.
     Ruled out by direct evidence: antivirus (Kaspersky log empty in the window),
     process crash (no Windows Error Reporting entries), and Windows resource
     exhaustion (no System event 2004). The remaining hypothesis is pressure from
     the suite's own footprint — it loads torch/chromadb and spawns an
     encode-offload child (see R-F3347) — but that is NOT yet proven, and the
     experiment that would prove it (a heavy run with a memory sampler) is
     deferred rather than guessed at.

So the baseline was, in practice, whatever someone last remembered to measure by
hand — and CLAUDE.md's figure had drifted ~3x stale for two months (R-F3368).

This script closes that. It runs the suite in FOREGROUND segments (immune to the
background-kill problem), aggregates the results, and — the part that matters —
diffs the FAILURE SET against docs/suite_baseline.json rather than comparing
counts. A count can stay flat while one test starts failing and another starts
passing; the repo's own doctrine is to judge by failure-set diff.

  python scripts/admin/suite_baseline.py                 # run + diff vs baseline
  python scripts/admin/suite_baseline.py --record        # re-record the baseline
  python scripts/admin/suite_baseline.py --resume-from 7 # continue after a kill

Exit code is 1 if any NEW failure appeared — that is the §16 gate.

HONEST LIMIT, stated because the number gets quoted: a segmented run CANNOT see
order-dependent failures. The repo's own record is 149 segmented vs 165
single-process, i.e. 16 invisible. This measures a FLOOR, and --record writes
that caveat into the file it produces.
"""
from __future__ import annotations

import argparse
import json
import pathlib
import re
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
TESTS = ROOT / "aria_service" / "tests"
BASELINE = ROOT / "docs" / "suite_baseline.json"

_SUMMARY = re.compile(r"(?:(\d+) failed,? )?(?:\d+ )?(?:passed|error)")
_COUNTS = re.compile(r"(\d+) (failed|passed)")


def _test_files() -> list[pathlib.Path]:
    return sorted(TESTS.glob("test_*.py"))


def _run_segment(files: list[pathlib.Path], timeout_s: int) -> tuple[int, int, list[str], bool]:
    """Run one segment in the FOREGROUND. Returns (failed, passed, failures, hung)."""
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", *[str(f) for f in files],
         "-p", "no:randomly", "-q", f"--timeout={timeout_s}", "-p", "no:cacheprovider"],
        cwd=ROOT, capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    out = proc.stdout + proc.stderr
    failed = passed = 0
    for n, kind in _COUNTS.findall(out):
        if kind == "failed":
            failed = int(n)
        elif kind == "passed":
            passed = int(n)
    failures = [
        line[len("FAILED "):].split(" ")[0].replace("aria_service/tests/", "").replace("\\", "/")
        for line in out.splitlines() if line.startswith("FAILED ")
    ]
    # A hung segment is the wedge signature: no summary, a pytest-timeout dump.
    hung = "Timeout ++" in out or ("passed" not in out and "failed" not in out)
    return failed, passed, failures, hung


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--segment-size", type=int, default=75,
                    help="files per segment; 150 has been observed to exceed a 10-minute cap")
    ap.add_argument("--timeout", type=int, default=180, help="per-test timeout seconds")
    ap.add_argument("--resume-from", type=int, default=0, help="0-based segment index to start at")
    ap.add_argument("--record", action="store_true", help="rewrite docs/suite_baseline.json")
    args = ap.parse_args()

    files = _test_files()
    segments = [files[i:i + args.segment_size] for i in range(0, len(files), args.segment_size)]
    print(f"{len(files)} test files -> {len(segments)} segments of {args.segment_size}")

    total_f = total_p = 0
    all_failures: list[str] = []
    hung_segments: list[int] = []
    for idx, seg in enumerate(segments):
        if idx < args.resume_from:
            continue
        f, p, fails, hung = _run_segment(seg, args.timeout)
        total_f += f
        total_p += p
        all_failures.extend(fails)
        if hung:
            hung_segments.append(idx)
        flag = "  <-- HUNG (suite wedge; bisect this segment)" if hung else ""
        print(f"  [{idx + 1}/{len(segments)}] files {seg[0].name} .. {seg[-1].name}: "
              f"{f} failed, {p} passed{flag}", flush=True)

    observed = sorted(set(all_failures))
    print(f"\nTOTAL: {total_f} failed, {total_p} passed  ({len(observed)} distinct failing tests)")
    if hung_segments:
        print(f"WEDGE: segments {hung_segments} produced no summary — a test is hanging the run.")

    if args.record:
        BASELINE.write_text(json.dumps({
            "recorded_at": subprocess.run(["git", "log", "-1", "--date=short", "--pretty=%ad"],
                                          cwd=ROOT, capture_output=True, text=True).stdout.strip(),
            "commit": subprocess.run(["git", "rev-parse", "--short", "HEAD"],
                                     cwd=ROOT, capture_output=True, text=True).stdout.strip(),
            "method": "foreground segments (background pytest is killed externally on this box)",
            "caveat": "segmented runs cannot see order-dependent failures; historical 149 "
                      "segmented vs 165 single-process. This is a FLOOR.",
            "totals": {"failed": total_f, "passed": total_p,
                       "total": total_f + total_p, "files": len(files)},
            "failures": observed,
        }, indent=1) + "\n", encoding="utf-8", newline="\n")
        print(f"recorded -> {BASELINE.relative_to(ROOT)}")
        return 0

    if not BASELINE.exists():
        print(f"no baseline at {BASELINE.relative_to(ROOT)} — run with --record first")
        return 0

    known = set(json.loads(BASELINE.read_text(encoding="utf-8"))["failures"])
    new = sorted(set(observed) - known)

    # A PARTIAL run cannot speak to what is fixed: every test it did not execute
    # looks like it stopped failing. NEW failures are still valid (a test that
    # failed here really did fail), so the section-16 gate still applies — but the
    # FIXED list is suppressed rather than printed as a false win.
    complete = args.resume_from == 0 and not hung_segments
    if complete:
        fixed = sorted(known - set(observed))
        if fixed:
            print(f"\nFIXED since the baseline ({len(fixed)}):")
            for t in fixed:
                print(f"  + {t}")
    else:
        reason = "resumed mid-run" if args.resume_from else "a segment hung, so its tests never ran"
        print(f"\n(FIXED list suppressed: {reason} — untested tests would look fixed.)")
    if new:
        print(f"\nNEW FAILURES ({len(new)}) — CLAUDE.md section 16: an R-number must not add to these:")
        for t in new:
            print(f"  ! {t}")
        return 1
    print("\nNo new failures. Section 16 satisfied (against a FLOOR — see the caveat in the baseline).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
