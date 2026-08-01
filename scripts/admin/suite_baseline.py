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
import hashlib
import json
import pathlib
import re
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
TESTS = ROOT / "aria_service" / "tests"
BASELINE = ROOT / "docs" / "suite_baseline.json"


# ── R-F3622: the validity record, and why it lives HERE now ──────────────────
#
# docs/suite_baseline.md says its 2026-08-01 figure was "measured by
# scratchpad/measure.py, which snapshots a SHA-256 over every tracked
# aria_service/**/*.py before and after the run and prints VALID=YES|NO".
#
# That file does not exist. It was written into a session scratchpad and went
# with the session — so the ONE number the repo treats as authoritative could not
# be reproduced by anybody, and the check that made it trustworthy was not part
# of the tool that records baselines. This script (R-F3373) is that tool, and it
# had no validity check at all: it would happily `--record` a run corrupted by a
# peer commit landing mid-flight.
#
# The corruption is real and documented (R-F3597): `inspect.getsource` slices the
# file from disk using line numbers captured at IMPORT. On a tree two agents
# share, a mid-run commit shifts those lines and it returns a DIFFERENT
# function's body — silently, because the wrong slice is still valid Python. Two
# attempts at the 2026-08-01 baseline were destroyed that way, reading 147 and
# 110 for a suite whose real figure was ~110 throughout.
#
# So: hash the tree before and after, print VALID=YES|NO, and REFUSE to --record
# when it is NO. A number nobody can reproduce is not a baseline, and a baseline
# recorded from a corrupted run is worse than none.
def tree_hash() -> str:
    """SHA-256 over every tracked aria_service/**/*.py, path and content.

    Tracked-only and content-addressed: an untracked scratch file or a peer's
    unstaged edit elsewhere in the repo must not invalidate a run, but any change
    to the code under test must.
    """
    listing = subprocess.run(
        ["git", "ls-files", "aria_service/**/*.py"],
        cwd=ROOT, capture_output=True, text=True,
    ).stdout.split()
    digest = hashlib.sha256()
    for rel in sorted(listing):
        digest.update(rel.encode("utf-8"))
        try:
            digest.update((ROOT / rel).read_bytes())
        except OSError:
            # A file that vanished mid-run is itself a change — record it as one
            # rather than skipping it, or a deletion would read as a clean tree.
            digest.update(b"<missing>")
    return digest.hexdigest()[:16]

_SUMMARY = re.compile(r"(?:(\d+) failed,? )?(?:\d+ )?(?:passed|error)")
_COUNTS = re.compile(r"(\d+) (failed|passed)")


def _test_files() -> list[pathlib.Path]:
    return sorted(TESTS.glob("test_*.py"))


def _normalise(node_id: str, tests_dir: pathlib.Path) -> str:
    """`<path>::<test>` -> tests-dir-relative, forward slashes.

    pytest prints paths relative to ROOTDIR, so for the real suite they already
    arrive as `aria_service/tests/x.py::t`; for a tests dir OUTSIDE the repo it
    prints absolute. Stripping one hardcoded prefix only worked for the former,
    which meant the ids could silently stop matching the baseline — and a gate
    whose ids do not match reports every known failure as fixed and every
    observed one as new.
    """
    path, sep, rest = node_id.partition("::")
    try:
        path = pathlib.Path(path).resolve().relative_to(tests_dir.resolve()).as_posix()
    except (ValueError, OSError):
        path = path.replace("\\", "/")
        marker = "aria_service/tests/"
        if marker in path:
            path = path.split(marker, 1)[1]
    return f"{path}{sep}{rest}"


def _run_segment(files: list[pathlib.Path], timeout_s: int, tests_dir: pathlib.Path) -> tuple[int, int, list[str], bool]:
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
    failures = [_normalise(line[len("FAILED "):].split(" ")[0], tests_dir)
                for line in out.splitlines() if line.startswith("FAILED ")]
    # A hung segment is the wedge signature: no summary, a pytest-timeout dump.
    hung = "Timeout ++" in out or ("passed" not in out and "failed" not in out)
    return failed, passed, failures, hung


def compare(observed: list[str], known: set[str], complete: bool) -> tuple[list[str], list[str] | None]:
    """R-F3377 — the §16 gate itself, extracted so it can be PROVEN to fire.

    Returns (new_failures, fixed_or_None). `fixed` is None for a partial or
    hung run: every test that did not execute would otherwise read as fixed,
    which is a false win. New failures stay valid either way — a test that
    failed really did fail — so the gate is never silently disabled.

    This lived inside main() and was therefore only reachable by running the
    whole suite, which meant the one behaviour that matters had never been
    exercised. R-F3373 shipped a gate nobody had seen fire.
    """
    new = sorted(set(observed) - known)
    fixed = sorted(known - set(observed)) if complete else None
    return new, fixed


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--segment-size", type=int, default=75,
                    help="files per segment; 150 has been observed to exceed a 10-minute cap")
    ap.add_argument("--timeout", type=int, default=180, help="per-test timeout seconds")
    ap.add_argument("--resume-from", type=int, default=0, help="0-based segment index to start at")
    ap.add_argument("--record", action="store_true", help="rewrite docs/suite_baseline.json")
    ap.add_argument("--max-segments", type=int, default=0,
                    help="stop after N segments. For SMOKE-TESTING the gate only — "
                         "it makes the run partial, so the 'fixed' list is suppressed "
                         "and the result must never be --record'ed as a baseline")
    ap.add_argument("--tests-dir", type=pathlib.Path, default=TESTS,
                    help="directory to collect test_*.py from (tests point this at a fixture "
                         "so the GATE itself can be exercised without running the real suite)")
    ap.add_argument("--baseline", type=pathlib.Path, default=BASELINE,
                    help="baseline file to compare against (tests point this at a fixture)")
    args = ap.parse_args()

    # R-F3622 — snapshot BEFORE anything runs.
    hash_before = tree_hash()
    print(f"tree {hash_before} @ {subprocess.run(['git', 'rev-parse', '--short', 'HEAD'], cwd=ROOT, capture_output=True, text=True).stdout.strip()}")

    files = sorted(args.tests_dir.glob("test_*.py"))
    segments = [files[i:i + args.segment_size] for i in range(0, len(files), args.segment_size)]
    if args.max_segments:
        segments = segments[:args.max_segments]
    print(f"{len(files)} test files -> {len(segments)} segments of {args.segment_size}")

    total_f = total_p = 0
    all_failures: list[str] = []
    hung_segments: list[int] = []
    for idx, seg in enumerate(segments):
        if idx < args.resume_from:
            continue
        f, p, fails, hung = _run_segment(seg, args.timeout, args.tests_dir)
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

    # R-F3622 — the validity record. Printed on EVERY run, not just --record, so
    # a number can never be quoted out of an unvalidated run.
    hash_after = tree_hash()
    valid = hash_after == hash_before
    print(f"\nVALID={'YES' if valid else 'NO'}  tree {hash_before} -> {hash_after}")
    if not valid:
        print("  The code under test CHANGED while the suite was running (a peer commit,"
              " a checkout, a stash pop). DISCARD this result — do not publish it and do"
              " not diff it. Re-run on a quiet tree. See R-F3597 for why a corrupted run"
              " still produces a plausible-looking number.")

    if args.record and not valid:
        print("refusing --record: a baseline measured while the tree moved is not a baseline")
        return 2
    if args.record and args.max_segments:
        print("refusing --record on a truncated run: it would erase every failure "
              "in the segments that never ran")
        return 2
    if args.record:
        # R-F3622 — write to args.baseline, not the module constant.
        # `--baseline` was honoured when COMPARING and ignored when RECORDING, so a
        # test that exercised the record path against a fixture would silently
        # overwrite docs/suite_baseline.json — the real one. That made the recording
        # half of this tool effectively untestable without collateral damage, which
        # is why it shipped without the validity check above.
        args.baseline.write_text(json.dumps({
            "recorded_at": subprocess.run(["git", "log", "-1", "--date=short", "--pretty=%ad"],
                                          cwd=ROOT, capture_output=True, text=True).stdout.strip(),
            "commit": subprocess.run(["git", "rev-parse", "--short", "HEAD"],
                                     cwd=ROOT, capture_output=True, text=True).stdout.strip(),
            "method": "foreground segments (background pytest is killed externally on this box)",
            "caveat": "segmented runs cannot see order-dependent failures; historical 149 "
                      "segmented vs 165 single-process. This is a FLOOR.",
            # R-F3622 — the validity record travels WITH the number. A reader must
            # not have to trust that whoever recorded it checked.
            "valid": True,
            "tree_hash": hash_before,
            "totals": {"failed": total_f, "passed": total_p,
                       "total": total_f + total_p, "files": len(files)},
            "failures": observed,
        }, indent=1) + "\n", encoding="utf-8", newline="\n")
        try:
            _shown = args.baseline.resolve().relative_to(ROOT)
        except ValueError:
            _shown = args.baseline
        print(f"recorded -> {_shown}")
        return 0

    if not args.baseline.exists():
        print(f"no baseline at {args.baseline} — run with --record first")
        return 0

    known = set(json.loads(args.baseline.read_text(encoding="utf-8"))["failures"])
    complete = args.resume_from == 0 and not hung_segments and not args.max_segments
    new, fixed = compare(observed, known, complete)

    if fixed:
        print(f"\nFIXED since the baseline ({len(fixed)}):")
        for t in fixed:
            print(f"  + {t}")
    elif fixed is None:
        reason = ("resumed mid-run" if args.resume_from
                  else "truncated by --max-segments" if args.max_segments
                  else "a segment hung, so its tests never ran")
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
