"""R-F3885 — the hook test certified PRESENCE and called it ACTIVATION.

`test_rf1958_precommit_hook_active` asserts the hook file exists at
`scripts/git-hooks/pre-commit` and describes that directory as "the ACTIVE
core.hooksPath dir". It never asserts that `core.hooksPath` is actually SET — and in
this clone it is unset, locally and globally. So **no local hook runs at all**, for
any of the ~12 checks it drives, and the test that exists to prove otherwise is
green.

That is the R-F1958 defect recurring one level up. R-F1958's own docstring records
the original: the checks "NEVER RAN" because `core.hooksPath` pointed at
`scripts/git-hooks/` while the installer pointed at the look-alike
`scripts/githooks/`. It fixed the file's location and then asserted the file's
location — so the *same* class of failure, the config not pointing where anyone
believes it points, remained invisible.

It is the house pattern: a guard whose universe is empty always certifies (R-F3791),
an absence that reads exactly like health (§1, §17, C-25).

WHAT THIS TEST DOES NOT DO: fail the suite because a developer's clone is not
configured. `core.hooksPath` is per-clone local state, CI checks out fresh, and a
suite that goes red on someone's git config would be muted within a day. It reports
the honest state and pins the REPO-side invariants that must hold for activation to
be possible at all — so `python scripts/pre-commit --install` (or the one-liner in
CLAUDE.md §26a) is all that is ever needed.
"""
from __future__ import annotations

import subprocess

from aria_service.tests._source_probe import repo_path


def _hooks_path() -> str:
    try:
        out = subprocess.run(["git", "config", "--get", "core.hooksPath"],
                             cwd=str(repo_path(".")), capture_output=True,
                             text=True, timeout=20)
        return (out.stdout or "").strip()
    except Exception:
        return ""


def test_the_hook_lives_where_activation_would_point():
    """The repo-side half of activation: the file must be in the directory the
    documented one-liner sets core.hooksPath to. This CAN fail, and must."""
    hook = repo_path("scripts/git-hooks/pre-commit")
    assert hook.exists(), (
        "scripts/git-hooks/pre-commit is missing — `git config core.hooksPath "
        "scripts/git-hooks` would activate an empty directory, silently disabling "
        "every check while looking configured")


def test_the_hook_invokes_the_checker_that_carries_the_gates():
    """Activation is worthless if the hook does not reach the checks. R-F3878's
    C-number gate, and the ~11 others, all hang off scripts/pre-commit."""
    body = repo_path("scripts/git-hooks/pre-commit").read_text(encoding="utf-8")
    assert "scripts/pre-commit" in body
    # Fail-safe: block on the explicit sentinel, never on a crash (R-F1958).
    assert "VERIFICATION FAILED" in body


def test_activation_state_is_reported_not_silently_assumed():
    """THE POINT. Whatever core.hooksPath is, the suite must not imply the hook runs
    when it does not. This asserts only that the question is ANSWERABLE — the value
    itself is per-clone and is printed, never asserted."""
    configured = _hooks_path()
    active = configured.replace("\\", "/").rstrip("/").endswith("scripts/git-hooks")
    print(f"\ncore.hooksPath = {configured or '(unset)'}  -> local hook "
          f"{'ACTIVE' if active else 'NOT ACTIVE'}")
    if not active:
        print("  Local pre-commit checks do NOT run in this clone. Enforcement is CI\n"
              "  (ci.yml -> scripts/pre-commit --check-all, plus the dedicated\n"
              "  defect-register-gate.yml). To enable locally:\n"
              "      git config core.hooksPath scripts/git-hooks")
    assert isinstance(configured, str), "the activation state must be knowable"


def test_rf1958_no_longer_claims_activation_it_does_not_check():
    """The wording is the defect: 'the ACTIVE core.hooksPath dir' reads as a proven
    fact when nothing verified it. It must point at this file instead of asserting
    activation it never established."""
    src = repo_path("aria_service/tests/test_rf1958_precommit_hook_active.py").read_text(
        encoding="utf-8")
    assert "R-F3885" in src, (
        "test_rf1958 still describes its directory as ACTIVE without checking "
        "core.hooksPath — it must cross-reference R-F3885, which measures it")
