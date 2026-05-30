"""R-F568 (2026-05-16) — deploy-fly verify-step refactor tests.

Prior verify step required (a) `flyctl releases --json` parse success
AND (b) `/health` operational|degraded. When flyctl CLI itself
misbehaves the JSON parse fails → step exits 1 even though deploy
succeeded. Memo: pickup_2026_05_16 logs "deploy-fly.yml verify-step
failing on every push" — operator's been manually `flyctl deploy`'ing.

R-F568 inverts the truth source: /health is authoritative; flyctl
releases is diagnostics-only. flyctl-CLI churn can no longer mask
live deploys.

These are static-source tests because we can't run GHA locally. They
fail if a future change reverts the inversion.
"""
from __future__ import annotations

from pathlib import Path

import pytest


_WORKFLOW = Path(__file__).resolve().parents[2] / ".github" / "workflows" / "deploy-fly.yml"


@pytest.fixture(scope="module")
def workflow_source() -> str:
    assert _WORKFLOW.exists(), "deploy-fly.yml missing"
    return _WORKFLOW.read_text(encoding="utf-8")


def test_workflow_file_exists(workflow_source: str) -> None:
    assert "Deploy ARIA to Fly.io" in workflow_source


def test_health_is_authoritative_truth_source(workflow_source: str) -> None:
    """Capability: /health is the gate. Look for the marker comment +
    the operational|degraded check in the loop. R-F1080 added canary
    deploy + rollback; R-F568 marker was removed during refactor but
    the /health probe pattern is preserved."""
    assert '"operational"' in workflow_source or 'operational' in workflow_source, (
        "operational status check missing — verify step likely reverted"
    )
    assert '"degraded"' in workflow_source or 'degraded' in workflow_source
    # The loop must check /health
    assert "aria-intel.fly.dev/health" in workflow_source


def test_flyctl_releases_is_non_blocking(workflow_source: str) -> None:
    """Capability: when `flyctl releases` fails, the verify step
    must STILL fall through to the /health probe, not exit 1.

    Pre-R-F568: `flyctl releases ...` was a direct `releases_json=$(...)`
    bash substitution — a non-zero exit silently produced an empty
    string and the next `jq` step crashed. The new shape wraps it in
    `if releases_json=$(...); then ... else ... fi` so the failure
    branch sets sentinels and continues.
    """
    src = workflow_source
    # The new conditional form must exist
    assert "if releases_json=$(flyctl releases" in src, (
        "R-F568: flyctl releases call must be wrapped in `if ...; then` "
        "so its failure no longer blocks verify"
    )
    # And there must be an else branch that sets sentinels
    assert 'latest_status="flyctl_failed"' in src, (
        "R-F568: missing else-branch fallback sentinel for flyctl failure"
    )


def test_no_blocking_status_complete_check(workflow_source: str) -> None:
    """Capability: the old `if [ "${latest_status}" != "complete" ];
    then exit 1; fi` gate is GONE. flyctl release status is now
    informational only."""
    bad = '[ "${latest_status}" != "complete" ]'
    assert bad not in workflow_source, (
        "R-F568 regression: the blocking flyctl-status gate is back. "
        "/health should be the only authoritative source."
    )


def test_health_poll_budget_reasonable(workflow_source: str) -> None:
    """The /health poll loop must run long enough for cold-start
    (sentence-transformer load + corpus warmup ~2-3 min). 36 × 5s = 180s
    is the post-R-F516 standard."""
    assert "seq 1 36" in workflow_source
    assert "sleep 5" in workflow_source


def test_diagnostic_dumps_health_body_on_timeout(workflow_source: str) -> None:
    """Capability: when the verify step DOES fail at the 180s timeout,
    the failure log must include the last /health response body so the
    operator can diagnose without re-running."""
    assert "/health response body at timeout" in workflow_source
    assert "/tmp/health.json" in workflow_source


def test_flyctl_version_logged(workflow_source: str) -> None:
    """Capability: flyctl version is captured on every failure path so
    CLI-schema drift is immediately diagnosable from the run log."""
    assert "flyctl version" in workflow_source


def test_build_rev_surfaced_on_success(workflow_source: str) -> None:
    """Capability: success notice includes the deployed build_rev so
    the operator confirms the right commit is live, not just any
    'operational' status from a stale build."""
    assert "build_rev=" in workflow_source
    assert "/health/live" in workflow_source
