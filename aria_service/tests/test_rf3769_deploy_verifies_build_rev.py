"""R-F3769 — CAPABILITY: the deploy verifier must compare build_rev, not just print it.

On 2026-08-07, run 31220316865 reported SUCCESS while shipping nothing: zero new fly
releases, live build_rev still the previous commit.

THE CHAIN:
  * the `deploy` step has continue-on-error: true — correct, because a torch build can
    outlast the flyctl client (§11c);
  * so `Verify release` exists as a fallback, gated on
    `if: steps.deploy.outcome == 'failure'`. It RAN, which proves the deploy failed;
  * it polled /health, saw the PREVIOUS build answering `operational`, and exited 0.
    It fetched build_rev and only echoed it.

A step named "Verify release" that verifies LIVENESS certifies every failed deploy
whose old build is still healthy — and `exit 0` also skipped the R-F1080 rollback
directly beneath it. Absence of the new build read as presence of a good one, in the
one place meant to catch exactly that. CLAUDE.md §11 already states the rule ("a
dispatched run is not a deploy until build_rev matches"); the value was in hand and
discarded.

These assertions are deliberately about the WORKFLOW TEXT: this logic runs in GitHub
Actions, so pytest cannot execute it. The comparison idiom itself was negative-
controlled against a matching sha, a non-matching sha, "unknown" and "" before shipping.

Run: python -m pytest aria_service/tests/test_rf3769_deploy_verifies_build_rev.py -v
"""
from __future__ import annotations

import pytest

from ._source_probe import repo_path

WF = repo_path(".github/workflows/deploy-fly.yml")


def _verify_step() -> str:
    import yaml
    d = yaml.safe_load(WF.read_text(encoding="utf-8"))
    steps = d["jobs"]["deploy"]["steps"]
    hits = [s for s in steps if s.get("name") == "Verify release"]
    assert hits, "the 'Verify release' step disappeared from deploy-fly.yml"
    return hits[0]["run"]


def test_the_expected_sha_is_captured():
    r = _verify_step()
    assert "EXPECTED_SHA=" in r, (
        "the verifier no longer captures the sha this run was supposed to ship, so it "
        "cannot tell a deployed build from the one already running"
    )


def test_build_rev_is_compared_not_merely_printed():
    """THE DEFECT: build_rev was fetched and echoed, never checked."""
    r = _verify_step()
    assert "build_rev#*" in r and "EXPECTED_SHA" in r, (
        "build_rev is not compared against EXPECTED_SHA. A live OLD build would "
        "certify a failed deploy — exactly run 31220316865."
    )


def test_success_is_reachable_only_through_that_comparison():
    """A single, guarded exit 0. An unguarded one restores the defect."""
    r = _verify_step()
    assert r.count("exit 0") == 1, (
        f"expected exactly one exit 0 in the verifier, found {r.count('exit 0')} — "
        f"an unguarded success path would bypass the build_rev check"
    )
    before = r.split("exit 0")[0]
    assert "build_rev#*" in before, (
        "exit 0 is reachable without passing the build_rev comparison"
    )


def test_the_rollback_path_survives():
    """R-F1080: a genuinely failed deploy must still roll back, not just warn."""
    r = _verify_step()
    assert "PREV_IMAGE" in r and "exit 1" in r, (
        "the automated rollback was removed; a failed deploy would leave the old "
        "build serving with no remediation"
    )


def test_the_failure_message_names_the_real_condition():
    """'did not reach operational' was misleading — the old build WAS operational."""
    r = _verify_step()
    assert "EXPECTED_SHA" in r.split("::error::")[-1] or "sha" in r.split("::error::")[-1], (
        "the timeout error does not mention the expected sha, so a reader is told "
        "the service is down when in fact the WRONG BUILD is up"
    )
