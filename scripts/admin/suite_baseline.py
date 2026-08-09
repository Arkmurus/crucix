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


def environment_fingerprint() -> dict:
    """Identify the interpreter + installed package set this measurement ran on.

    R-F3794. A baseline diff answers "which tests newly fail", and every reader has
    taken that to mean "which commits broke something". It does not: the failure set
    is a function of the CODE and the ENVIRONMENT, and nothing recorded the second
    one. On 2026-08-08 a diff of 126-vs-103 produced "36 new failures", of which at
    least five were caused by no commit at all — this box's venv had been rebuilt on
    2026-08-03 and the FastAPI it resolved changed `include_router` so that
    `app.routes` no longer enumerates (R-F3791, defects.md C-12).

    C-01 predicted exactly this ("a bump can move the baseline with no commit at
    all") and pinning did not solve it: pinning makes the set REPRODUCIBLE, while
    this makes a shift LEGIBLE. They are different problems, and only the second one
    stops a dependency change from being mistaken for a regression.

    `packages_sha256` is taken over a sorted, normalised `pip freeze` so it is stable
    across pip's ordering. Editable installs are dropped: their line embeds an
    absolute checkout path, which would make the hash machine-specific and therefore
    useless for comparing two runs.
    """
    fp: dict = {
        "python": sys.version.split()[0],
        "platform": sys.platform,
        "machine": __import__("platform").machine(),
    }
    try:
        frozen = subprocess.run([sys.executable, "-m", "pip", "freeze"],
                                capture_output=True, text=True, timeout=120).stdout
    except Exception as exc:  # never let fingerprinting break a measurement
        # Honest absence, not a fabricated value: a reader must be able to tell
        # "not captured" from "captured and identical".
        fp["packages_sha256"] = None
        fp["error"] = f"{type(exc).__name__}: {exc}"
        return fp

    lines = sorted(ln.strip() for ln in frozen.splitlines()
                   if ln.strip() and not ln.startswith("-e "))
    fp["packages"] = len(lines)
    fp["packages_sha256"] = hashlib.sha256("\n".join(lines).encode()).hexdigest()[:16]
    # A few pins worth reading at a glance, because these are the ones that have
    # actually moved a baseline. Names are matched case-insensitively: pip freeze
    # echoes the distribution's own casing.
    watched = {"fastapi", "starlette", "pydantic", "httpx", "pytest", "torch", "chromadb"}
    fp["key_packages"] = {
        name.lower(): ver
        for name, _, ver in (ln.partition("==") for ln in lines)
        if name.lower() in watched and ver
    }
    return fp


def environment_drift_report(base_env: dict | None, now_env: dict) -> list[str]:
    """Lines warning that the dependency set moved between two measurements.

    R-F3794. Returns [] only when both environments are fingerprinted AND identical
    — the single case in which a new failure can be attributed to code without
    further thought.

    A MISSING baseline fingerprint yields a warning rather than silence. "Not
    captured" and "captured and identical" are different facts, and collapsing the
    first into the second is precisely the defect class §1 keeps recording: an
    absence read as a clean measurement.
    """
    if not base_env or not base_env.get("packages_sha256"):
        return ["",
                "NOTE: this baseline predates environment fingerprinting (R-F3794), so a",
                "dependency change CANNOT be ruled out as the cause of any new failure below.",
                f"This run: python {now_env.get('python')}, "
                f"packages {now_env.get('packages_sha256')}."]

    if base_env.get("packages_sha256") == now_env.get("packages_sha256"):
        return []

    lines = ["", "*** ENVIRONMENT CHANGED SINCE THE BASELINE ***",
             f"  python   {base_env.get('python')} -> {now_env.get('python')}",
             f"  packages {base_env.get('packages_sha256')} -> {now_env.get('packages_sha256')}"]
    was_pkgs = base_env.get("key_packages") or {}
    now_pkgs = now_env.get("key_packages") or {}
    for name in sorted(set(was_pkgs) | set(now_pkgs)):
        if was_pkgs.get(name) != now_pkgs.get(name):
            lines.append(f"    {name}: {was_pkgs.get(name)} -> {now_pkgs.get(name)}")
    lines.append("  Any NEW failure below may be an environment delta, not a code "
                 "regression.")
    lines.append("  Rule the environment out before attributing it to a commit.")
    return lines


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


def dirty_measured_files() -> list[str]:
    """R-F3631 - tracked aria_service/**/*.py with UNCOMMITTED changes.

    tree_hash() reads the WORKING TREE, which is right: it must notice a mid-run
    edit whether or not anyone committed it. But --record separately stamps
    `commit: <git rev-parse HEAD>`, and on a dirty tree those two disagree about
    what was measured - the hash describes the files that ran, the sha describes a
    commit that does NOT contain them.

    Observed 2026-08-01: a run stamped `commit: 28b49e5a` while a peer agent's
    uncommitted work sat in aria_engine.py. Checking that sha out reproduces a
    different suite than the number describes. A baseline nobody can reproduce is
    the defect R-F3622 exists to prevent, arriving through a second door: not a
    tree that moved in TIME, but one that differs in SPACE from its own label.
    """
    out = subprocess.run(
        ["git", "status", "--porcelain", "--", "aria_service"],
        cwd=ROOT, capture_output=True, text=True,
    ).stdout.splitlines()
    dirty = []
    for line in out:
        status, _, path = line.partition(" ")[0], None, line[3:].strip()
        if line.startswith("??"):
            continue          # untracked is outside the hashed set - must not block
        if path.endswith(".py"):
            dirty.append(path)
    return sorted(dirty)

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


def _run_single_process(tests_dir: pathlib.Path, timeout_s: int) -> tuple[int, int, list[str], bool]:
    """R-F3625 — ONE pytest process over the whole suite: the §16 measurement itself.

    This script could only ever produce a SEGMENTED floor, and its own docstring says
    so ("149 segmented vs 165 single-process, i.e. 16 invisible"). But the number
    CLAUDE.md §16 and docs/suite_baseline.md actually quote is the SINGLE-PROCESS one.
    So the committed tool could not reproduce the figure the repo treats as
    authoritative — which is the same defect as R-F3622 (the measurement lived
    somewhere the tool wasn't), just in a different place: the tool measured a
    different thing than the doc published.

    A segmented run cannot see order-dependent failures because each segment gets a
    fresh interpreter; state leaked by test A into test B only bites when both run in
    one process. That is not a rounding difference — it is a whole failure CLASS
    (see the order-dependent-tests work, R-F3449).

    Command is the documented §16 one, plus `-rf`:
        python -m pytest aria_service/tests/ -q --tb=line -p no:cacheprovider --timeout=600
    `-rf` only forces the failure summary to print so the failure SET can be extracted;
    it changes no collection, no ordering and no execution. The repo's doctrine is to
    diff the failure set, not the count, and without it there is no set to diff.

    Returns (failed, passed, failures, hung) — same shape as _run_segment.
    """
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", str(tests_dir),
         "-q", "--tb=line", "-p", "no:cacheprovider", f"--timeout={timeout_s}", "-rf"],
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
    # No summary at all means the process died (external kill / wedge), NOT a clean
    # run. Treating that as "0 failures" would publish a fabricated pass.
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
    ap.add_argument("--single-process", action="store_true",
                    help="R-F3625: run the WHOLE suite in one pytest process — the actual "
                         "CLAUDE.md §16 measurement. Slower and vulnerable to an external "
                         "kill on this box, but it is the only mode that can see "
                         "order-dependent failures, which a segmented run structurally "
                         "cannot. Use this for the authoritative number.")
    args = ap.parse_args()

    # R-F3622 — snapshot BEFORE anything runs.
    hash_before = tree_hash()
    _dirty = dirty_measured_files()
    print(f"tree {hash_before} @ {subprocess.run(['git', 'rev-parse', '--short', 'HEAD'], cwd=ROOT, capture_output=True, text=True).stdout.strip()}")

    files = sorted(args.tests_dir.glob("test_*.py"))
    total_f = total_p = 0
    all_failures: list[str] = []
    hung_segments: list[int] = []
    segments: list[list[pathlib.Path]] = []

    if args.single_process:
        # R-F3625 — the §16 measurement. One process, so order-dependent failures are
        # VISIBLE; a segmented run gives each chunk a fresh interpreter and cannot see
        # them at all.
        print(f"{len(files)} test files -> ONE pytest process (§16 mode, "
              f"per-test timeout {args.timeout}s)", flush=True)
        total_f, total_p, all_failures, hung = _run_single_process(args.tests_dir, args.timeout)
        if hung:
            hung_segments.append(0)
            print("WEDGE: no pytest summary — the process died or hung. This is NOT "
                  "'zero failures'; the run produced no measurement.")
        print(f"  {total_f} failed, {total_p} passed", flush=True)
    else:
        segments = [files[i:i + args.segment_size] for i in range(0, len(files), args.segment_size)]
        if args.max_segments:
            segments = segments[:args.max_segments]
        print(f"{len(files)} test files -> {len(segments)} segments of {args.segment_size} "
              f"(FLOOR — order-dependent failures are invisible; use --single-process "
              f"for the §16 number)")
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

    # R-F3624 — an invalid run must produce NO verdict at all, not just no record.
    #
    # R-F3622 stopped an invalid run being RECORDED and stopped there. Observed on the
    # very first real use (2026-08-01): the script printed `VALID=NO`, told the reader
    # to "DISCARD this result — do not publish it and do not diff it" — and then diffed
    # it anyway, emitting a 20-item "NEW FAILURES (20) — CLAUDE.md section 16" list and
    # exiting 1. A §16 gate verdict computed from data the tool has just declared
    # invalid is exactly the plausible-looking wrong answer R-F3597 is about, one layer
    # up: someone would have gone hunting twenty regressions that may not exist.
    #
    # (The invalidating change was peer commit 46eadcb5 adding a tracked test file
    # mid-run — so the failure SET genuinely shifted under the run: files collected in
    # later segments were not the files collected in earlier ones.)
    #
    # Exit 3, distinct from 0 (clean) / 1 (real new failures) / 2 (refused to record),
    # so a caller — CI included — can tell "the measurement failed" from "the code
    # failed". Those must never collapse into one signal.
    # Order matters: --record on an invalid run is the MORE specific case and keeps its
    # own exit 2, so "you tried to record garbage" stays distinguishable from "the
    # measurement was invalid". Putting the general check first would make this branch
    # unreachable.
    # R-F3631 - a dirty tree cannot be labelled with a commit.
    #
    # Scoped to the REAL baseline deliberately, not carved out to make tests pass. The
    # harm is specific: docs/suite_baseline.json is the authoritative record and stamps
    # `commit`, so a dirty tree makes it name a sha that does not contain what ran.
    # Recording to a fixture path in a temp dir makes no such claim, and a guard that
    # fired there would make the tool's own tests depend on whether an UNRELATED file
    # happened to be dirty - which is how a guard earns a reputation for crying wolf
    # and gets switched off.
    _recording_the_real_baseline = args.baseline.resolve() == BASELINE.resolve()
    if args.record and _dirty and _recording_the_real_baseline:
        print()
        print("refusing --record: %d tracked aria_service .py file(s) have "
              "UNCOMMITTED changes, so `commit` would name a sha that does not "
              "contain what ran:" % len(_dirty))
        for f in _dirty[:10]:
            print(f"  ~ {f}")
        print("  Commit or stash them, then re-run. The measurement itself is fine "
              "- it is the LABEL that would be a lie.")
        return 2
    if args.record and not valid:
        print("refusing --record: a baseline measured while the tree moved is not a baseline")
        return 2
    if not valid:
        print("\nNo verdict: the run is invalid, so neither the count nor the failure-set "
              "diff means anything. Re-run on a quiet tree.")
        return 3
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
            # R-F3625 — the method and its caveat must describe the run that actually
            # happened. Recording a single-process measurement under the segmented
            # caveat would understate its authority; recording a segmented one without
            # the caveat would overstate it. Either way the reader is misled about what
            # the number can see.
            "method": ("single pytest process (CLAUDE.md §16 measurement)"
                       if args.single_process
                       else "foreground segments (background pytest is killed externally on this box)"),
            "caveat": ("order-dependent failures ARE visible in this mode; this is the "
                       "authoritative §16 figure, not a floor."
                       if args.single_process
                       else "segmented runs cannot see order-dependent failures; historical 149 "
                            "segmented vs 165 single-process. This is a FLOOR."),
            # R-F3622 — the validity record travels WITH the number. A reader must
            # not have to trust that whoever recorded it checked.
            "valid": True,
            "tree_hash": hash_before,
            # R-F3794 — the environment travels WITH the number, for the same reason
            # R-F3622 made the validity record travel with it: a reader must not have
            # to assume the two runs being compared ran on the same dependency set.
            "environment": environment_fingerprint(),
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

    _baseline_doc = json.loads(args.baseline.read_text(encoding="utf-8"))
    known = set(_baseline_doc["failures"])
    complete = args.resume_from == 0 and not hung_segments and not args.max_segments
    new, fixed = compare(observed, known, complete)

    # R-F3794 — say so BEFORE the failure lists, because it changes how they read.
    # A new failure under a changed dependency set is not yet evidence of a code
    # regression, and the 2026-08-08 diff was read as 36 regressions when at least
    # five were a FastAPI behaviour change (defects.md C-12).
    for _line in environment_drift_report(_baseline_doc.get("environment"),
                                          environment_fingerprint()):
        print(_line)

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
