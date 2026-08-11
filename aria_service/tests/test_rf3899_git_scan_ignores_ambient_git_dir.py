"""R-F3899 — the allocator's git scan obeyed ambient GIT_DIR, overriding its cwd.

`_git_log_records` passes `cwd=root` so that, per R-F3248, "the repo whose history
governs THIS registry file" decides which numbers are taken. But **GIT_DIR overrides
cwd**: with it set, git reads the repository the environment names and ignores where
the process is standing.

git exports GIT_DIR and GIT_WORK_TREE for EVERY hook it runs, so anything invoked
from a pre-commit/pre-push hook inherits them. Measured 2026-08-11: exporting GIT_DIR
makes 4 of the 12 `test_r_number_registry_rf540` tests fail, because a tmp-path
registry suddenly starts skipping the real repo's ~3,300 numbers.

FOUND THE HARD WAY: the pre-push verifier failed on a push from a git WORKTREE, while
the identical pytest selection passed in both trees when run by hand. That gap —
green when run directly, red inside the hook — is the signature of ambient
environment, and it is the same "green in one mode, dead in the other" shape as
R-F3886 (`--check-all` passing while the staged path crashed).

WHY IT IS NOT MERELY A TEST PROBLEM. `reserve()` calls this. A reservation made from
inside a hook — a verifier, or ARIA's autonomous coder running under one — allocates
against whichever repo the environment names. That is precisely the collision this
module exists to prevent, and it fails toward OVER-skipping, so the symptom is a
suspiciously high next number rather than an error.
"""
from __future__ import annotations

import os

import pytest

from aria_service.intel import r_number_registry as reg


def test_the_env_filter_strips_the_overriding_vars():
    os.environ["GIT_DIR"] = "/some/other/repo/.git"
    os.environ["GIT_WORK_TREE"] = "/some/other/repo"
    try:
        env = reg._env_without_git_overrides()
        assert "GIT_DIR" not in env
        assert "GIT_WORK_TREE" not in env
        assert "PATH" in env, "the rest of the environment must survive"
    finally:
        os.environ.pop("GIT_DIR", None)
        os.environ.pop("GIT_WORK_TREE", None)


def test_the_scan_is_stable_under_a_hostile_git_dir(monkeypatch):
    """CAPABILITY TEST — the user-visible symptom. The set of R-numbers git knows
    must not change because a hook exported GIT_DIR."""
    reg._git_scan_cache.clear()
    before, ok_before = reg.r_numbers_known_to_git()
    assert ok_before, "baseline scan must succeed for this test to mean anything"

    monkeypatch.setenv("GIT_DIR", "/nonexistent/other/.git")
    monkeypatch.setenv("GIT_WORK_TREE", "/nonexistent/other")
    reg._git_scan_cache.clear()
    after, ok_after = reg.r_numbers_known_to_git()

    assert ok_after, "the scan must still read the governing repo, not the env's"
    assert after == before, (
        f"ambient GIT_DIR changed the scan result by "
        f"{len(before ^ after)} R-numbers — allocation would differ inside a hook")


def test_reserve_is_unaffected_by_ambient_git_dir(tmp_path, monkeypatch):
    """The whole point: a claim made from inside a hook must match one made outside."""
    reg._git_scan_cache.clear()
    ledger_a = tmp_path / "a.json"
    first = reg.reserve("outside a hook", path=ledger_a, repo_root=tmp_path)

    monkeypatch.setenv("GIT_DIR", "/nonexistent/other/.git")
    reg._git_scan_cache.clear()
    ledger_b = tmp_path / "b.json"
    second = reg.reserve("inside a hook", path=ledger_b, repo_root=tmp_path)

    assert first == second, (
        f"the same allocation gave {first} outside a hook and {second} inside one")
