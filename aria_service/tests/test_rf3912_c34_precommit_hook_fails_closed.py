"""C-34 / R-F3912 — the pre-commit hook silently skipped EVERY check.

Observed across four real commits in this session: each one printed

    Python was not found; run without arguments to install from the Microsoft Store...

and then committed successfully. No check ran. The commits were fine only because
the checks were run by hand.

TWO FAULTS, and the second is the one that matters.

1. INTERPRETER RESOLUTION IS WORKTREE-BLIND. The hook resolves the venv against
   `git rev-parse --show-toplevel`, which inside a git worktree is the WORKTREE root
   — and a worktree has no `.venv`. It then falls through to `command -v python3`,
   which on Windows resolves to the App Execution Alias shim: a stub that prints an
   advertisement to stdout and exits without running anything. CLAUDE.md now makes
   worktrees the normal way to work in this repo (a peer agent holds the main
   checkout dirty), so the hook is broken in exactly the configuration the project
   tells you to use.

2. FAIL-OPEN CANNOT TELL "RAN AND FOUND NOTHING" FROM "NEVER RAN". The hook blocks
   only on an explicit `VERIFICATION FAILED` sentinel and exits 0 otherwise, so the
   shim's advertisement — containing no sentinel — read as a clean pass. That is the
   C-29 defect sitting inside the guard whose whole job is to catch defects: absence
   of a failure report treated as evidence of health. Worse, in that state the hook
   could not have blocked a REAL failure either: the sentinel it greps for can never
   appear if the checker never starts, so it was not merely lenient — it was inert.

THE FIX IS AN INTERPRETER PROBE, deliberately the narrowest discriminator available:
`"$PY" -c "pass"`. The shim fails it (measured: exit 49); every real Python passes.

R-F1958's fail-open policy was deliberate — "a tooling bug must never wedge commits"
— and is KEPT EXACTLY. A checker that genuinely runs and then crashes still warns and
still allows the commit (`test_a_crashing_checker_still_fails_open` pins that, and it
is the line this fix must not cross). Only "no usable interpreter" blocks, which is a
local configuration error fixable in seconds and the one case where proceeding while
believing checks ran is most dangerous.

A sentinel-based variant was written first and discarded: requiring positive output
would also have wedged commits on any checker that crashes before printing, which is
precisely the fail-open case R-F1958 protected.

`git commit --no-verify` remains the escape hatch, so this can never truly wedge.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from aria_service.tests._source_probe import repo_path


HOOK = repo_path("scripts/git-hooks/pre-commit")
_GIT_SH_CANDIDATES = (
    Path(r"C:\Program Files\Git\bin\sh.exe"),
    Path(r"C:\Program Files\Git\usr\bin\sh.exe"),
)
SH = shutil.which("sh") or next(
    (str(candidate) for candidate in _GIT_SH_CANDIDATES if candidate.exists()),
    None,
)
_HOOK_UNAVAILABLE = not HOOK.exists() or SH is None
_HOOK_UNAVAILABLE_REASON = (
    "hook not present in this checkout" if not HOOK.exists()
    else "POSIX shell unavailable on this platform"
)


def _run_hook_against(
    tmp_path: Path,
    stub_body: str,
    *,
    broken_interpreter: bool = False,
) -> subprocess.CompletedProcess:
    """Run the REAL hook in a throwaway git repo whose checker we control.

    Drives the actual shell script rather than asserting on its text, so the test
    fails if the logic is wrong regardless of how it is spelled.

    `broken_interpreter=True` reproduces the ACTUAL production failure: no repo
    venv is reachable and `python3` resolves to the Windows App Execution Alias
    shim, which prints an advertisement and exits 49 without running anything.
    """
    assert SH is not None, "POSIX shell is required to execute the hook fixture"
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    # The hook invokes "$PY $REPO_ROOT/scripts/pre-commit"; a python stub is the
    # cleanest way to control exactly what the checker prints and returns.
    (scripts / "pre-commit").write_text(stub_body, encoding="utf-8")

    env = dict(os.environ)
    if broken_interpreter:
        # No .venv anywhere the hook can reach, and a shim-alike first on PATH.
        fake_bin = tmp_path / "fakebin"
        fake_bin.mkdir()
        for name in ("python3", "python"):
            shim = fake_bin / name
            shim.write_text(
                "#!/bin/sh\n"
                "echo 'Python was not found; run without arguments to install from "
                "the Microsoft Store, or disable this shortcut from Settings'\n"
                "exit 49\n",
                encoding="utf-8",
            )
            shim.chmod(0o755)
        env["PATH"] = f"{fake_bin}{os.pathsep}{env['PATH']}"
    else:
        # A WORKING interpreter must be discoverable, mirroring a healthy checkout.
        # Delegating to the interpreter running this test keeps the fixture honest:
        # the hook probes a real Python and the checker stub really executes.
        real_bin = tmp_path / "realbin"
        real_bin.mkdir()
        for name in ("python3", "python"):
            shim = real_bin / name
            shim.write_text(
                f'#!/bin/sh\nexec "{sys.executable}" "$@"\n', encoding="utf-8"
            )
            shim.chmod(0o755)
        env["PATH"] = f"{real_bin}{os.pathsep}{env['PATH']}"

    return subprocess.run(
        [SH, str(HOOK)],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )


@pytest.mark.skipif(_HOOK_UNAVAILABLE, reason=_HOOK_UNAVAILABLE_REASON)
def test_hook_blocks_when_no_usable_interpreter_exists(tmp_path) -> None:
    """THE SYMPTOM, reproduced at its true cause.

    With no reachable venv and `python3` resolving to the Store shim, the checker
    never executes. The old hook exited 0 — so the commit proceeded believing the
    checks had passed, and in that state it could not have blocked a REAL failure
    either: the sentinel it greps for can never appear if nothing runs.
    """
    stub = "print('[pre-commit] OK — all files checked, no issues.')\n"
    proc = _run_hook_against(tmp_path, stub, broken_interpreter=True)

    assert proc.returncode != 0, (
        "C-34: no usable Python, yet the hook allowed the commit — 'did not run' "
        "is being read as 'ran and found nothing'"
    )
    combined = (proc.stdout + proc.stderr).lower()
    assert "did not run" in combined, (
        "the block must say WHY, or the developer will just re-run and be confused"
    )


@pytest.mark.skipif(_HOOK_UNAVAILABLE, reason=_HOOK_UNAVAILABLE_REASON)
def test_hook_allows_a_clean_verified_commit(tmp_path) -> None:
    """The positive sentinel must still pass — the guard has to be able to go green."""
    stub = "print('[pre-commit] OK — all files checked, no issues.')\n"
    proc = _run_hook_against(tmp_path, stub)

    assert proc.returncode == 0, (
        f"a clean check was blocked: {proc.stdout}{proc.stderr}"
    )


@pytest.mark.skipif(_HOOK_UNAVAILABLE, reason=_HOOK_UNAVAILABLE_REASON)
def test_hook_still_blocks_a_real_verification_failure(tmp_path) -> None:
    """R-F1958's original contract, unchanged."""
    stub = (
        "print('[pre-commit] VERIFICATION FAILED — COMMITTED SECRET:')\n"
        "print('  aws key in config.py')\n"
    )
    proc = _run_hook_against(tmp_path, stub)

    assert proc.returncode != 0, "a real VERIFICATION FAILED no longer blocks"


@pytest.mark.skipif(_HOOK_UNAVAILABLE, reason=_HOOK_UNAVAILABLE_REASON)
def test_a_crashing_checker_still_fails_open(tmp_path) -> None:
    """The DELIBERATE fail-open is preserved: a checker that RAN and crashed warns.

    This is the line C-34 must not cross. A tooling bug must never wedge commits;
    only 'never executed' — a local config error — is treated as blocking.
    """
    stub = (
        "import sys\n"
        "print('[pre-commit] running checks...')\n"
        "sys.stderr.write('Traceback: ImportError: no module named foo\\n')\n"
        "sys.exit(1)\n"
    )
    proc = _run_hook_against(tmp_path, stub)

    assert proc.returncode == 0, (
        "a CRASHING checker now wedges commits — that is the fail-open intent "
        "R-F1958 deliberately chose, and C-34 must not remove it"
    )


def test_hook_resolves_the_venv_from_the_main_checkout_not_just_the_worktree() -> None:
    """FAULT 1 — the reason this fired at all.

    `--show-toplevel` is the WORKTREE root, which has no .venv. The hook must also
    consult the common git dir's parent (the main checkout) before falling back to
    a bare `python3`, or it is broken in the exact configuration CLAUDE.md tells
    every session to use.
    """
    body = HOOK.read_text(encoding="utf-8")
    assert "--git-common-dir" in body, (
        "hook resolves the interpreter only from the worktree root, so the repo "
        "venv is invisible inside a worktree and it falls through to the shim"
    )


def test_pre_push_resolves_the_venv_the_same_way() -> None:
    """C-38 finding 10 — C-34 fixed pre-commit and left pre-push behind.

    pre-push carries the identical four-branch block and runs under `set -e`, so in a
    worktree it falls through to the Store shim, the shim exits non-zero, and the hook
    ABORTS — blocking the push outright. pre-commit failing open meant checks were
    silently skipped; pre-push failing closed means nothing can be pushed at all,
    which is why every push in the C-34 session needed a manual PATH shim.
    """
    push_hook = repo_path("scripts/git-hooks/pre-push")
    if not push_hook.exists():
        return
    body = push_hook.read_text(encoding="utf-8")
    assert "--git-common-dir" in body, (
        "pre-push still resolves the interpreter only from the worktree root — the "
        "same defect C-34 fixed in pre-commit, and it fails CLOSED here"
    )
