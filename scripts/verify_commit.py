#!/usr/bin/env python3
"""R-F559 — pre-push verifier.

Enforces the verify-after-fix discipline by checking the pending push:

  1. pytest passes on the full aria_service test suite (or a fast subset
     selected by --fast).
  2. If commits in the push range mention an R-F<n>, a matching
     `tests/test_*rf<n>*.py` file must exist in the working tree.
  3. The R-number registry (R-F540) records the claimed R-numbers.

Exits 0 on pass, non-zero on any violation. Designed to be invoked from
a `pre-push` git hook (see `scripts/git-hooks/pre-push`).

Usage:
    python scripts/verify_commit.py                  # full suite
    python scripts/verify_commit.py --fast           # imports + smoke only
    python scripts/verify_commit.py --range origin/main..HEAD

Exit codes:
    0  — all checks pass
    1  — pytest failed
    2  — R-number missing test file
    3  — R-number not in reservation log
    4  — internal error
    5  — tolerated pytest xfail marker found
"""
from __future__ import annotations

import argparse
import ast
import json
import re
import subprocess
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
_R_NUMBER_PATTERN = re.compile(r"R-F(\d{2,4})")


def _run(cmd: list[str], cwd: Path | None = None) -> tuple[int, str, str]:
    proc = subprocess.run(
        cmd,
        cwd=cwd or _REPO,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    return proc.returncode, proc.stdout, proc.stderr


def _r_numbers_in_range(rev_range: str) -> set[str]:
    """R-numbers CLAIMED by commits in the range.

    R-F3281 — SUBJECT ONLY, and this is the difference between a gate that gets
    used and a gate that gets bypassed.

    This read `--format=%s%n%b`, so every R-number mentioned anywhere in a commit
    BODY counted as a claim needing its own test file. A message that explains a
    fix by citing the R-numbers it builds on ("the R-F3089 gate", "R-F3199
    removed captcha solving") was therefore demanding test files for other
    people's shipped work, and the verifier exited 2 on a perfectly disciplined
    push. A gate that fails on correct behaviour teaches everyone to reach for
    --no-verify, and then it protects nothing.

    The registry already settled this, in scan_shipped_r_numbers: "THE SUBJECT
    LINE IS THE SHIP RECORD; THE BODY IS NOT ... a subject match is evidence of
    implementation while a body match is evidence only of a reference." The same
    rule applies here, for the same reason.

    Bookkeeping commits are excluded on the same basis deploy.ps1 uses (R-F3247):
    a `chore: reserve/ship/mark` commit records numbers, it does not implement
    them.
    """
    rc, out, err = _run(["git", "log", "--format=%s", rev_range])
    if rc != 0:
        # No such ref pair (e.g. fresh branch with no upstream) — fall
        # back to inspecting the last commit only.
        rc2, out2, _ = _run(["git", "log", "-1", "--format=%s"])
        if rc2 != 0:
            return set()
        out = out2
    subjects = [
        s for s in out.splitlines()
        if not re.match(r"^\s*chore:\s*(reserve|ship|mark)\b", s, re.I)
    ]
    return {
        f"R-F{m.group(1)}"
        for s in subjects
        for m in _R_NUMBER_PATTERN.finditer(s)
    }


def _registry_r_numbers() -> set[str]:
    """Return all R-numbers ever reserved per the registry (R-F540)."""
    p = _REPO / "data" / "r_number_reservations.json"
    if not p.exists():
        return set()
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        return {r["r_number"] for r in data.get("reservations", [])}
    except Exception:
        return set()


_REGISTRY_REL = "data/r_number_reservations.json"


def _committed_registry_r_numbers() -> set[str]:
    """R-F3284 - the registry AS COMMITTED, not as it sits on this disk.

    Every other reader takes the working-tree file, which is exactly what makes
    the drift invisible: a claim reserved here looks present here and is absent
    everywhere else. Reading HEAD is the only way to answer the real question,
    "is this claim durable yet?".
    """
    rc, out, _ = _run(["git", "show", f"HEAD:{_REGISTRY_REL}"])
    if rc != 0:
        return set()
    try:
        data = json.loads(out)
        return {r["r_number"] for r in data.get("reservations", [])}
    except Exception:
        return set()


def _uncommitted_registry_numbers() -> set[str]:
    """Reservations that exist only in this working tree."""
    return _registry_r_numbers() - _committed_registry_r_numbers()


def _uncommitted_in_range(r_numbers: set[str]) -> set[str]:
    """R-F3284 - of the R-numbers BEING PUSHED, which are not yet durable?

    Scoped to the push on purpose. A blanket "the registry is dirty" failure
    would fire whenever any peer had an in-flight reservation, and a gate that
    fails on someone else's work teaches everyone to reach for --no-verify.
    """
    return {r for r in r_numbers if r in _uncommitted_registry_numbers()}


def _test_files_present_for(r_number: str) -> list[Path]:
    """Return all test files that anchor a given R-number.

    Convention: test files for an R-number contain the lowercase id
    `rf<n>` in the file name OR reference the R-number string in source.
    """
    n = r_number.split("R-F")[-1]
    matches: list[Path] = []
    for f in _all_test_files():
        name = f.name.lower()
        if f"rf{n}" in name or f"_f{n}" in name:
            matches.append(f)
            continue
        # R-F3281 — a FIXTURE is not a claim. Tests pass fake R-numbers as whole
        # quoted literals ("R-F99999" in test_rf3200 and in this verifier's own
        # test, both proving an unknown number finds nothing), while a real
        # anchor appears in prose: a docstring or a comment describing the work.
        # Counting fixtures made the matcher self-referential, so the file
        # asserting "no test exists for R-F99999" was returned as the test for
        # R-F99999. Strip standalone quoted occurrences, then look again.
        txt = _test_text(f)
        if r_number not in txt:
            continue
        prose = re.sub(rf"""["']{re.escape(r_number)}["']""", "", txt)
        # R-F3281 — DIGIT BOUNDARY. A bare substring test made R-F99999 match
        # the fixture "R-F999999" in test_rf3200, and by the same rule R-F3270
        # would match R-F32700. Anchor the end so a shorter number cannot claim
        # a longer one's test.
        if re.search(rf"{re.escape(r_number)}(?!\d)", prose):
            matches.append(f)
    return matches


_TEST_TEXT_CACHE: dict[Path, str] = {}


def _all_test_files() -> list[Path]:
    """R-F3281 — BOTH tiers, not just Python.

    This only globbed aria_service/tests, so a web-tier fix could never satisfy
    the gate: its capability test lives in test/<name>.test.mjs by convention, and
    the verifier simply could not see it. An R-number whose test the gate cannot
    find is reported as undisciplined work, which is a false accusation.

    R-F4353 (C-298) — DISCOVER THE TREES, DO NOT LIST THEM. R-F3281 widened
    this from one hardcoded directory to two, and a third appeared anyway:
    `aria_cli/tests` holds 47 test files that this function could not see. The
    consequence is not cosmetic — it BLOCKS THE PUSH and reports the author as
    having shipped an R-number with no test, which is the "false accusation"
    the docstring above already warns about. It happened to R-F4351, whose
    capability test existed and was simply in a directory nobody had added to
    the list — and it blocked a second session's four unrelated commits behind
    it, because the gate runs over the whole push range.

    The cost is worse than a false accusation. A fails-closed hook that cannot
    be satisfied HONESTLY pushes the next author toward `--no-verify`, which
    disables the gate for EVERY R-number in the push, not just the one it
    misjudged. A gate people must route around is a gate that has stopped
    working.

    A list of known places rots every time a package is added. Globbing
    `*/tests/test_*.py` finds them by SHAPE instead, so the next package is
    covered on the day it is created rather than the day someone notices.
    Kept to one level deep on purpose: it is bounded, fast, and matches the
    repo's actual layout (`<package>/tests/`), where a full recursive walk
    would drag in .venv and node_modules.
    """
    out: list[Path] = []
    for tests_dir in sorted(_REPO.glob("*/tests")):
        if tests_dir.is_dir():
            out += sorted(tests_dir.glob("test_*.py"))
    node = _REPO / "test"
    if node.exists():
        out += sorted(node.glob("*.test.mjs"))
    return out


def _test_text(f: Path) -> str:
    """Read a test file once. R-F3281 — the content scan used to be gated on a
    NAME heuristic (`rf<first-3-digits>` in the filename), which defeated the
    documented behaviour: the docstring promises "file name OR reference the
    R-number string in source", but a fix that correctly lives in the existing
    test file it repairs (test_rf2379_*.py for an R-F3270 fix) was never even
    opened. Scanning every test file is cheap once, and cached here."""
    if f not in _TEST_TEXT_CACHE:
        try:
            _TEST_TEXT_CACHE[f] = f.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            _TEST_TEXT_CACHE[f] = ""
    return _TEST_TEXT_CACHE[f]


def _display_path(path: Path) -> str:
    """Render a path for the operator WITHOUT assuming it sits under the repo.

    R-F4298 (C-252). This was `path.relative_to(_REPO)`, which RAISES
    ValueError for anything outside the checkout — so the gate crashed with a
    traceback on the very input it exists to report. A guard that dies while
    describing what it found tells the operator nothing about the finding.

    Not hypothetical: this repo runs work in `git worktree` trees, and pytest's
    default tmp_path lives under the system temp directory. R-F4205's own test
    drives the gate with exactly such a path and had NEVER passed under a
    default tmpdir — it went green only when run with `--basetemp` pointed
    inside the repo, which is what the leftover `.pytest_rf4205_*` directories
    are. A test that passes because of HOW it was invoked is not evidence
    about the code.

    Relative when it can be, absolute when it cannot. Both are readable; a
    traceback is not.
    """
    try:
        return str(path.relative_to(_REPO))
    except ValueError:
        return str(path)


def _tolerated_xfails(files: list[Path] | None = None) -> list[tuple[Path, int]]:
    """Return executable pytest xfail markers; known regressions must fail normally."""
    findings: list[tuple[Path, int]] = []
    for path in files if files is not None else _all_test_files():
        try:
            tree = ast.parse(path.read_text(encoding="utf-8", errors="ignore"))
        except (OSError, SyntaxError):
            continue
        pytest_aliases = {"pytest"}
        xfail_aliases: set[str] = set()
        mark_aliases: set[str] = set()
        for imported in ast.walk(tree):
            if isinstance(imported, ast.Import):
                pytest_aliases.update(
                    alias.asname or alias.name
                    for alias in imported.names if alias.name == "pytest"
                )
            elif isinstance(imported, ast.ImportFrom) and imported.module == "pytest":
                for alias in imported.names:
                    local_name = alias.asname or alias.name
                    if alias.name == "xfail":
                        xfail_aliases.add(local_name)
                    elif alias.name == "mark":
                        mark_aliases.add(local_name)
        for node in ast.walk(tree):
            if not isinstance(node, (ast.Call, ast.Attribute)):
                continue
            func = node.func if isinstance(node, ast.Call) else node
            if isinstance(func, ast.Name) and func.id in xfail_aliases:
                finding = (path, node.lineno)
                if finding not in findings:
                    findings.append(finding)
                continue
            if not isinstance(func, ast.Attribute) or func.attr != "xfail":
                continue
            owner = func.value
            direct = isinstance(owner, ast.Name) and owner.id in pytest_aliases
            marked = (
                isinstance(owner, ast.Attribute)
                and owner.attr == "mark"
                and isinstance(owner.value, ast.Name)
                and owner.value.id in pytest_aliases
            )
            imported_mark = isinstance(owner, ast.Name) and owner.id in mark_aliases
            if direct or marked or imported_mark:
                finding = (path, node.lineno)
                if finding not in findings:
                    findings.append(finding)
    return findings


def _run_pytest(fast: bool) -> int:
    """Return pytest exit code. fast=True restricts to import + smoke."""
    if fast:
        cmd = [
            sys.executable, "-m", "pytest",
            "aria_service/tests/test_imports.py",
            "aria_service/tests/test_r_number_registry_rf540.py",
            "-q", "--no-header",
        ]
    else:
        cmd = [
            sys.executable, "-m", "pytest",
            "aria_service/tests/",
            "-q", "--no-header",
            "-x",                           # stop on first failure
        ]
    rc, out, err = _run(cmd)
    sys.stdout.write(out)
    if err:
        sys.stderr.write(err)
    return rc


def main() -> int:
    p = argparse.ArgumentParser(description="R-F559 pre-push verifier")
    p.add_argument("--range", default="origin/main..HEAD",
                   help="git rev range to inspect (default: origin/main..HEAD)")
    p.add_argument("--fast", action="store_true",
                   help="run only imports + R-F540 registry tests")
    p.add_argument("--skip-tests", action="store_true",
                   help="skip pytest (for hook calls that already ran tests)")
    args = p.parse_args()

    # ── 1. Discover R-numbers in the push range ───────────────────────
    r_numbers = _r_numbers_in_range(args.range)
    if r_numbers:
        print(f"[R-F559] R-numbers in range {args.range}: {sorted(r_numbers)}")
    else:
        print(f"[R-F559] No R-numbers found in range {args.range} — "
              "skipping R-number-specific checks.")

    # ── 2. Validate each R-number has a test file ─────────────────────
    missing_tests: list[str] = []
    for rn in sorted(r_numbers):
        tests = _test_files_present_for(rn)
        if not tests:
            missing_tests.append(rn)
        else:
            print(f"[R-F559] {rn} test file(s): "
                  f"{', '.join(t.name for t in tests)}")
    if missing_tests:
        print(
            "\n[R-F559] FAIL — R-numbers missing test file:\n  "
            + "\n  ".join(missing_tests)
            + "\n\nAdd test_<r>.py with at least one capability test "
              "that proves the user-visible symptom is fixed "
              "(memory/feedback_capability_test_per_r_number.md).",
            file=sys.stderr,
        )
        return 2

    # R-F4205 — an xfail is a product gap reclassified as green. The exact
    # R-F1436 intent regressions survived for months because the fast push suite
    # printed "2 xfailed" and still exited zero. Reject executable xfail markers
    # across the entire Python test tree even when --fast runs only a subset.
    tolerated_xfails = _tolerated_xfails()
    if tolerated_xfails:
        details = "\n  ".join(
            f"{_display_path(path)}:{line}" for path, line in tolerated_xfails
        )
        print(
            "\n[R-F559] FAIL — tolerated pytest xfail markers are forbidden:\n  "
            + details
            + "\n\nFix the regression or make the environmental precondition an explicit skip.",
            file=sys.stderr,
        )
        return 5

    # ── 3. Validate each R-number is in the registry ──────────────────
    registry = _registry_r_numbers()
    if registry and r_numbers:
        unregistered = sorted(rn for rn in r_numbers if rn not in registry)
        if unregistered:
            print(
                "\n[R-F559] FAIL — R-numbers not reserved in R-F540 log:\n  "
                + "\n  ".join(unregistered)
                + "\n\nReserve via:\n  python scripts/admin/reserve_r_number.py "
                  "reserve \"short title\"",
                file=sys.stderr,
            )
            return 3

    # ── 3b. R-F3284 — the claim must be DURABLE, not merely present here ──
    #
    # A reservation lives in a git-tracked file that nothing forces anyone to
    # commit, so it can sit in one working tree indefinitely. Measured on
    # 2026-07-27: the committed copy stopped at R-F3277 while the working tree
    # held R-F3281. Until it is committed the claim does not exist for anybody
    # else, which is how R-F3187 lost R-F3133/3134/3135 (reserved, used in
    # shipped code, then absent from the log), and why this very gate read stale
    # inside a worktree and had to be diagnosed before it could be trusted.
    #
    # Push is the right moment: it is when the work becomes shared, and the first
    # moment another agent can be handed the same number.
    if r_numbers:
        undurable = sorted(_uncommitted_in_range(set(r_numbers)))
        if undurable:
            print(
                "\n[R-F559] FAIL — R-numbers reserved ONLY in this working tree:\n  "
                + "\n  ".join(undurable)
                + "\n\nThe reservation log is git-tracked and is the claim of record"
                  " (CLAUDE.md section 2). Until it is committed another agent can be"
                  " handed the same number, and a fresh clone cannot see the claim at"
                  " all.\n\nCommit it with this push:\n  git add "
                + _REGISTRY_REL,
                file=sys.stderr,
            )
            return 4

    # ── 4. Run pytest ─────────────────────────────────────────────────
    if args.skip_tests:
        print("[R-F559] --skip-tests passed; skipping pytest.")
        return 0

    rc = _run_pytest(fast=args.fast)
    if rc != 0:
        print(f"[R-F559] FAIL — pytest exited {rc}. Fix tests before push.",
              file=sys.stderr)
        return 1

    print("[R-F559] PASS — all checks green.")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(4)
    except Exception as e:
        print(f"[R-F559] INTERNAL ERROR: {e}", file=sys.stderr)
        sys.exit(4)
