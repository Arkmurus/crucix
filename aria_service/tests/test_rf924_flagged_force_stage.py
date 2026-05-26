"""R-F924 — a FLAGGED code-review verdict must ALWAYS force-stage, never auto-deploy.

Landmine (found 2026-05-27, audited live on aria-intel): with
ARIA_CODER_AUTO_DEPLOY_AND_TICKET=1 (ticket-mode), the inline staging formula
in self_coder.py computed force_stage=False AND force_deploy=False for a FLAGGED
verdict, so it fell through to the R-F462 default gate — and AUTO-DEPLOYED a
flagged change when AUTO_DEPLOY=1. That contradicted "Claude's flag still wins."

resolve_staging_decision() is the extracted pure decision. The invariant: a
flagged verdict force-stages regardless of ticket-mode.
"""
from __future__ import annotations

from aria_service.autonomous.self_coder import resolve_staging_decision


def _decide(is_flagged, is_blocked, ticket, force_only=False):
    return resolve_staging_decision(
        is_flagged=is_flagged, is_blocked=is_blocked,
        ticket_mode_enabled=ticket, force_stage_only=force_only,
    )


def test_rf924_flagged_under_ticket_mode_force_stages_NOT_deploys():
    """THE landmine: flagged + ticket-mode must stage, never deploy."""
    force_stage, force_deploy = _decide(is_flagged=True, is_blocked=False, ticket=True)
    assert force_stage is True, "REGRESSION: flagged verdict did not force staging"
    assert force_deploy is False, "REGRESSION: flagged verdict would auto-deploy under ticket-mode"


def test_rf924_flagged_without_ticket_mode_force_stages():
    force_stage, force_deploy = _decide(is_flagged=True, is_blocked=False, ticket=False)
    assert force_stage is True
    assert force_deploy is False


def test_rf924_clean_under_ticket_mode_auto_deploys():
    """A clean (not-flagged) verdict under ticket-mode is the ONLY auto-deploy path."""
    force_stage, force_deploy = _decide(is_flagged=False, is_blocked=False, ticket=True)
    assert force_stage is False
    assert force_deploy is True


def test_rf924_clean_without_ticket_mode_defers_to_gate():
    """No ticket-mode, not flagged → neither forced; the R-F462 change_type gate decides."""
    force_stage, force_deploy = _decide(is_flagged=False, is_blocked=False, ticket=False)
    assert force_stage is False
    assert force_deploy is False


def test_rf924_operator_code_request_always_stages():
    """force_stage_only (operator /code, R-F852) always stages, even clean+ticket."""
    force_stage, force_deploy = _decide(is_flagged=False, is_blocked=False, ticket=True, force_only=True)
    assert force_stage is True
    assert force_deploy is False


def test_rf924_blocked_defensively_stages():
    """Blocked is handled by an earlier early-return, but defensively never deploys."""
    force_stage, force_deploy = _decide(is_flagged=False, is_blocked=True, ticket=True)
    assert force_stage is True
    assert force_deploy is False


def test_rf924_no_input_combination_deploys_a_flagged_fix():
    """Exhaustive guarantee: across the whole truth table, a flagged verdict
    NEVER yields force_deploy=True."""
    for is_blocked in (False, True):
        for ticket in (False, True):
            for force_only in (False, True):
                _, force_deploy = _decide(
                    is_flagged=True, is_blocked=is_blocked,
                    ticket=ticket, force_only=force_only,
                )
                assert force_deploy is False, (
                    f"flagged fix would deploy with is_blocked={is_blocked} "
                    f"ticket={ticket} force_only={force_only}"
                )
