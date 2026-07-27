"""R-F3291 - the deploy audit file is written to a CWD-relative path.

    machines_deployer.py:66
        DEPLOY_HISTORY_DIR = Path("data/deploy_history")

Relative, so it resolves against whatever the process CWD happens to be. Every
other data path in this tree is anchored to an absolute project root (see
truth_verifier.py:315, `_PROJECT_ROOT / "data" / "deploy_history" / ...`), and
that inconsistency has two consequences.

OBSERVED, not theorised. Running the test suite from a git worktree appended a
FABRICATED record to the repository's real audit file:

    {"app": "aria-intel", "r_number": 1183,
     "image": "registry.fly.io/aria-intel:deployment-abcdef12",
     "commit_sha": "abcdef1234567890abcdef1234567890abcdef12"}

That is test fixture data in the file that answers "what was actually deployed".
It also dirties the working tree on every run, which blocked a rebase and quietly
degrades "git status is clean" as a deploy-safety signal, since a clean tree is
one of the conditions used before shipping.

IN PRODUCTION the same relativity means the history lands wherever the process was
launched from. `truth_verifier` reads it from the PROJECT ROOT, so a service
started from any other directory writes a history that the verifier then cannot
find, and reports absence rather than the deploys that happened.

Anchored to the project root, and the writer now accepts an explicit directory so
tests can point it somewhere disposable instead of at the real record.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from aria_service.autonomous import machines_deployer as md


def test_the_history_dir_is_absolute() -> None:
    """A relative path silently follows the CWD. Anchor it."""
    assert md.DEPLOY_HISTORY_DIR.is_absolute(), (
        f"DEPLOY_HISTORY_DIR is relative ({md.DEPLOY_HISTORY_DIR}); it resolves "
        "against the process CWD, so the audit file moves with the caller"
    )


def test_it_points_at_the_repo_data_dir() -> None:
    """Anchored, and anchored to the SAME place truth_verifier reads from."""
    parts = md.DEPLOY_HISTORY_DIR.parts
    assert parts[-2:] == ("data", "deploy_history"), md.DEPLOY_HISTORY_DIR
    assert (md.DEPLOY_HISTORY_DIR.parent.parent / "aria_service").exists(), (
        "must resolve inside the repo, not beside whatever the CWD was"
    )


def test_a_test_can_redirect_the_history_without_touching_the_real_file(tmp_path) -> None:
    """THE CAPABILITY: a fixture must be able to write somewhere disposable.

    Without this, every run of the deployer tests appends invented deploy records
    to the repository's audit trail.
    """
    real = md.DEPLOY_HISTORY_DIR / "aria-intel.json"
    before = real.read_text(encoding="utf-8") if real.exists() else None

    md._record_deploy_history(
        "aria-intel",
        {"app": "aria-intel", "r_number": 9999, "commit_sha": "deadbeef"},
        history_dir=tmp_path,
    )

    written = tmp_path / "aria-intel.json"
    assert written.exists(), "the record must land in the directory it was given"
    entries = json.loads(written.read_text(encoding="utf-8"))
    assert any(e.get("r_number") == 9999 for e in entries)

    after = real.read_text(encoding="utf-8") if real.exists() else None
    assert after == before, (
        "the REAL deploy audit file was modified by a test; that is how a "
        "fabricated deployment ends up in the record of what shipped"
    )
