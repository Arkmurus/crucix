"""R-F4305 / C-258 - 21 training scripts hardcoded a checkout that does not exist.

Found while trying to regenerate aria-llm-v0.4-dpo. The driver opens with:

    REPO="/c/code/crucix"; cd "$REPO"

There is no `/c/code/crucix` on this machine - the checkout is `/c/Code/Aria`.
`cd` to a missing directory under `set -uo pipefail` (no `-e`) does NOT abort;
the script carries on in whatever directory it happened to start in, so every
relative path afterwards resolves somewhere unintended. **The whole training
script suite was unrunnable on this checkout**, and the failure mode is silent
drift rather than a clean error.

CLAUDE.md section 16 names this exact hazard: the old machine "is gone. Do not
hardcode a checkout path here or in tests - use `_source_probe.repo_path()`." The
Python side took that lesson; 21 shell scripts never did.

THE FIX RESOLVES FROM THE SCRIPT'S OWN LOCATION, not from the caller's cwd and
not from a literal. `git rev-parse --show-toplevel` is preferred, with a
BASH_SOURCE-relative fallback so the script still works in a worktree, a tarball
copy on a pod, or anywhere git is unavailable - all three are real deployment
shapes here, since these scripts are rsynced onto RunPod pods where the repo is
`/workspace/crucix` and there is no .git at all.

This guard is deliberately a REPO-WIDE scan rather than a check on one file: the
defect is that a literal was copied 21 times, so pinning a single script would
leave twenty ways to reintroduce it.
"""
from __future__ import annotations

import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parents[2]

#: Any absolute checkout literal. Matches the /c/code/crucix family and the
#: Windows C:\code\crucix form, in scripts and in Python.
_HARDCODED = re.compile(
    r"(?:/[a-zA-Z]/(?:code|Code)/(?:crucix|Aria)\b)"
    r"|(?:[A-Za-z]:\\+(?:code|Code)\\+(?:crucix|Aria)\b)"
)

_SEARCH_DIRS = ("scripts", "aria_service", "aria_cli")

#: SHELL ONLY, and the scoping is deliberate rather than lazy.
#:
#: The defect is an EXECUTABLE `cd` to a literal that may not exist. Python does
#: not have it - section 16 pushed that side onto `_source_probe.repo_path()`
#: long ago - and scanning .py flags two things that must NOT be touched:
#: a docstring in self_improve.py explaining the hazard, and
#: test_rf3133_deploy_native_stderr.py, which quotes CAPTURED STDERR from a real
#: deploy failure. Rewriting captured evidence to satisfy a linter is precisely
#: what got R-F4282 abandoned: it does not correct the record, it falsifies it.
_SUFFIXES = {".sh", ".ps1"}

#: This file necessarily contains the literal it bans, in its own pattern.
_EXEMPT = {
    "aria_service/tests/test_rf4305_no_hardcoded_checkout_path.py",
}


def _offenders() -> list[tuple[str, int, str]]:
    out: list[tuple[str, int, str]] = []
    for d in _SEARCH_DIRS:
        base = ROOT / d
        if not base.exists():
            continue
        for f in base.rglob("*"):
            if f.suffix.lower() not in _SUFFIXES or not f.is_file():
                continue
            rel = f.relative_to(ROOT).as_posix()
            if rel in _EXEMPT or "__pycache__" in rel:
                continue
            try:
                text = f.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            for i, line in enumerate(text.splitlines(), 1):
                stripped = line.strip()
                # A COMMENT explaining the ban is not a violation of it. Without
                # this, the fix's own rationale trips the guard - the same
                # prose-versus-code confusion that made the first version of
                # R-F4297's check go red on a correct tree.
                if stripped.startswith("#"):
                    continue
                if _HARDCODED.search(line):
                    out.append((rel, i, stripped[:100]))
    return out


def test_no_script_hardcodes_a_checkout_path() -> None:
    """THE CAPABILITY TEST. A script that cd's to a path which does not exist
    keeps running in the wrong directory - it does not fail cleanly."""
    bad = _offenders()
    assert not bad, (
        "hardcoded checkout path(s) - these break on any machine whose checkout "
        "is named or located differently (CLAUDE.md section 16):\n  "
        + "\n  ".join(f"{p}:{n}: {t}" for p, n, t in bad[:15])
    )


def test_the_guard_can_actually_fail(tmp_path) -> None:
    """A scan that matches nothing certifies everything (R-F3858). Prove the
    pattern still detects the literal it was written for."""
    assert _HARDCODED.search('REPO="/c/code/crucix"; cd "$REPO"')
    assert _HARDCODED.search("cd /c/code/crucix")
    assert _HARDCODED.search(r"C:\code\crucix\.venv")


def test_the_guard_does_not_flag_ordinary_paths() -> None:
    """It must not fire on relative paths or unrelated absolutes, or it becomes
    noise that gets muted."""
    for ok in ("cd scripts/train", "/workspace/crucix/scripts",
               "REPO=$(git rev-parse --show-toplevel)", "data/training/x.jsonl"):
        assert not _HARDCODED.search(ok), ok


def test_the_driver_resolves_the_repo_dynamically() -> None:
    """The specific script that blocked the v0.4-dpo regeneration."""
    src = (ROOT / "scripts/train/run_v04_dpo_cycle.sh").read_text(
        encoding="utf-8", errors="replace")
    assert "rev-parse --show-toplevel" in src or "BASH_SOURCE" in src, (
        "run_v04_dpo_cycle.sh still does not resolve its own repo root")


def test_resolution_survives_no_git(tmp_path) -> None:
    """These scripts are rsynced onto RunPod pods where the repo is
    /workspace/crucix with no .git, so a git-only resolution would break exactly
    where it is needed most."""
    src = (ROOT / "scripts/train/run_v04_dpo_cycle.sh").read_text(
        encoding="utf-8", errors="replace")
    assert "BASH_SOURCE" in src, (
        "no fallback for a checkout without git - the pod copy has no .git")
