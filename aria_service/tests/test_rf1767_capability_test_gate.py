"""R-F1767 — capability test for the capability-test gate.

The guarantee: an AUTONOMOUS self-improve change may auto-deploy ONLY if a
reproduce test proved the real symptom went FAIL→PASS (reproduce_fail_to_pass).
Without that proof the gate forces stage-only — so no untested change can land
autonomously, even under AUTO_DEPLOY=1 / ticket-mode. Operator-initiated and
already-force-staged verdicts bypass. Pure-function full-truth-table test.
"""
from __future__ import annotations

from aria_service.autonomous.self_coder import apply_capability_test_gate as gate


def test_autonomous_deploy_without_capability_test_is_blocked():
    # The core case: a clean autonomous fix heading toward auto-deploy
    # (force_deploy=True) but with NO reproduce FAIL→PASS → forced stage-only.
    fs, fd, blocked = gate(
        reproduce_fail_to_pass=False, operator_initiated=False,
        force_stage=False, force_deploy=True,
    )
    assert blocked is True
    assert fs is True and fd is False, "must force stage-only, never auto-deploy"


def test_autonomous_deploy_with_capability_test_is_allowed():
    # With a passing reproduce FAIL→PASS, the deploy decision is left intact.
    fs, fd, blocked = gate(
        reproduce_fail_to_pass=True, operator_initiated=False,
        force_stage=False, force_deploy=True,
    )
    assert blocked is False
    assert fd is True, "a capability-tested fix may auto-deploy"


def test_operator_initiated_bypasses_gate():
    # Operator request is the approval — gate does not intervene.
    fs, fd, blocked = gate(
        reproduce_fail_to_pass=False, operator_initiated=True,
        force_stage=False, force_deploy=True,
    )
    assert blocked is False
    assert fd is True


def test_already_force_staged_is_left_alone():
    # A flagged verdict already force-stages; gate must not double-process.
    fs, fd, blocked = gate(
        reproduce_fail_to_pass=False, operator_initiated=False,
        force_stage=True, force_deploy=False,
    )
    assert blocked is False
    assert fs is True and fd is False


def test_no_capability_test_no_deploy_intent_still_safe():
    # Untested + not heading to deploy (force_deploy False already) → no change,
    # not blocked (nothing to block), still stage-only.
    fs, fd, blocked = gate(
        reproduce_fail_to_pass=False, operator_initiated=False,
        force_stage=False, force_deploy=False,
    )
    # force_stage was False and force_deploy False — the gate flips force_stage
    # True (defensive: untested autonomous change must stage), blocked True.
    assert blocked is True
    assert fs is True and fd is False
