"""R-F3896 — `--install` pointed at the look-alike dir and DE-INSTALLED the real hook.

R-F1958 diagnosed this exactly and fixed only half of it. Its docstring:

    "core.hooksPath is scripts/git-hooks/ ... while the checks' own installer
     pointed at the look-alike scripts/githooks/ dir. The two near-identical
     directory names diverged, orphaning the checks."

It added a working hook to `scripts/git-hooks/` and never touched `install_hook()`,
which still targeted `scripts/githooks/`. Measured 2026-08-11: that directory still
holds a FROZEN COPY of the old Python checker (dated Aug 3) — no R-F3878 C-number
gate, still carrying the R-F3886 NameError that made every check fail open, and
without the R-F3888 false-positive fix.

OBSERVED LIVE while verifying it: running `python scripts/pre-commit --install`
overwrote a correct `core.hooksPath` with the orphan directory. **An installer that
de-installs the working hook is the most expensive kind of dead wiring, because
USING THE TOOL is what breaks it** — and it reports "Installed:" while doing so.

This is the fourth distinct failure in one chain, and each looked configured:
    R-F3885  core.hooksPath unset          -> the hook was never invoked
    R-F3886  the checker crashed           -> fail-open, every check skipped
    R-F3888  a false positive              -> the instinct is --no-verify
    R-F3896  the installer targets a stale copy
"""
from __future__ import annotations

import importlib.util
from importlib.machinery import SourceFileLoader

from aria_service.tests._source_probe import repo_path

_loader = SourceFileLoader("_pc_rf3896", str(repo_path("scripts/pre-commit")))
_spec = importlib.util.spec_from_loader("_pc_rf3896", _loader)
pc = importlib.util.module_from_spec(_spec)
_loader.exec_module(pc)


def test_the_installer_targets_the_directory_the_live_hook_lives_in():
    """The two names differ by ONE hyphen, which is why they diverged unnoticed."""
    from aria_service.tests._source_probe import function_source

    src = function_source(pc, "install_hook")
    assert '"git-hooks"' in src, (
        "install_hook must target scripts/git-hooks (hyphenated) — the look-alike "
        "scripts/githooks holds a frozen copy of the old checker (R-F3896)")
    assert '"githooks"' not in src.replace('"git-hooks"', ""), (
        "install_hook still references the orphan scripts/githooks directory")


def test_the_targeted_directory_actually_contains_a_working_hook():
    """Pointing at the right name is worthless if the hook there is not the one that
    drives the checks — the R-F3885 lesson (presence is not activation)."""
    hook = repo_path("scripts/git-hooks/pre-commit")
    assert hook.exists()
    body = hook.read_text(encoding="utf-8")
    assert "scripts/pre-commit" in body, "the installed hook must drive the checker"
    assert "VERIFICATION FAILED" in body, "it must block on the explicit sentinel"


def test_the_stale_duplicate_is_recorded_not_silently_trusted():
    """`scripts/githooks/` is NOT deleted — freeze §26 requires three proofs and the
    quarantine ladder. It is recorded so the next reader does not re-point at it.

    If it is ever removed, this test should go with it; until then its existence
    must stay visible, because a second file named almost the same as the live one
    is how this defect survived two fixes."""
    orphan = repo_path("scripts/githooks/pre-commit")
    if not orphan.exists():
        return          # removed through the ladder — nothing left to warn about
    live = repo_path("scripts/git-hooks/pre-commit").read_text(encoding="utf-8")
    stale = orphan.read_text(encoding="utf-8")
    assert stale != live, (
        "the two hooks are now identical — if that is deliberate, collapse them "
        "through the deletion ladder rather than leaving two copies to diverge again")
    from aria_service.tests._source_probe import function_source
    assert "R-F3896" in function_source(pc, "install_hook"), (
        "the orphan's existence must stay documented at the installer")
