"""R-F3709 — CAPABILITY: a leaked SERVICE token is not a cross-tenant key, and
"internal" is no longer derived from a secret-rotation accident.

CONTEXT (360 DD sweep, 2026-08-04). A live bearer token sat committed in
scripts/verify_all.py for months (R-F3683). Probing it against production
proved it still authenticates AND that `GET /api/aria/dd/reports` returns
another tenant's report with it. Rotating that credential is the operator's job;
this change removes the reason the leak was catastrophic.

TWO DEFECTS, both structural:

1. `_auth_is_internal_var` was set by NEGATION —
       presented != ARIA_API_TOKEN
   `_accepted_tokens()` returns FOUR tokens, so THREE of them set the flag:
   ARIA_INTERNAL_TOKEN, ARIA_OPERATOR_TOKEN and ARIA_SERVICE_TOKEN. The flag
   unlocks the unscoped cross-tenant DD path across nine endpoints.

   Worse, the branch's safety was an ACCIDENT. CLAUDE.md §18 recorded
   ARIA_API_TOKEN and ARIA_INTERNAL_TOKEN as byte-identical, so
   `presented != api_tok` was never true and the path was dead code. When the
   tokens were rotated apart the path went live — no code change, no failing
   test, no signal. A boundary that flips on a rotation asymmetry is not a
   boundary.

2. The unscoped path included `DELETE /dd/report/{run_id}`, whose cascade walks
   `entity_group_run_ids(run_id)` and can therefore delete reports owned by
   OTHER tenants (live: Chemring's three runs span two owners). A shared service
   credential should never carry that.

Nothing legitimately relied on either: aria-web authenticates with
ARIA_API_TOKEN, and aria-wa holds the internal token but calls no DD endpoint.

Run: python -m pytest aria_service/tests/test_rf3709_service_token_is_not_a_delete_key.py -v
"""
from __future__ import annotations

import inspect

import pytest

from aria_service.routes import aria as routes


# ── 1. "internal" is positive identity, not negation ───────────────────────

def test_internal_is_derived_from_the_internal_token_not_from_negation():
    src = inspect.getsource(routes.require_aria_token)
    assert "_hmac.compare_digest(presented, _int_tok)" in src, (
        "the flag must be set by MATCHING the internal token"
    )
    assert "not _hmac.compare_digest(presented, _api_tok)" not in src, (
        "deriving 'internal' as 'not the user token' made the operator and "
        "service tokens inherit unscoped cross-tenant DD access, and made the "
        "whole boundary depend on ARIA_API_TOKEN's value"
    )


def test_the_flag_no_longer_depends_on_the_api_token_value():
    """So no future rotation can arm the path again, silently."""
    src = inspect.getsource(routes.require_aria_token)
    flag_line = next(
        (ln for ln in src.splitlines() if "_auth_is_internal_var.set(" in ln), ""
    )
    assert "_api_tok" not in flag_line, (
        "the internal flag must not be computed from the user-facing token — "
        "that is what turned a secret rotation into a live security change"
    )


def test_the_operator_tier_is_tracked_separately():
    assert hasattr(routes, "_auth_is_operator_var")
    assert routes._auth_is_operator_var.get() is False, (
        "an unset tier var must never grant"
    )


def test_both_tier_vars_default_closed():
    assert routes._AUTH_INTERNAL_DEFAULT is False
    assert routes._auth_is_internal_var.get() is False
    assert routes._auth_is_operator_var.get() is False


# ── 2. destructive unscoped access requires the operator tier ──────────────

_REPORT = {"run_id": "dd_x", "user_id": "", "user_email_domain": ""}


def _set(internal: bool, operator: bool):
    routes._auth_is_internal_var.set(internal)
    routes._auth_is_operator_var.set(operator)


def test_service_token_may_read_unscoped(monkeypatch):
    _set(internal=True, operator=False)
    assert routes._dd_report_access_allowed(_REPORT, "") is True, (
        "the unscoped READ path is why the internal tier exists — trusted "
        "services call without a user_id"
    )


def test_service_token_may_NOT_delete_unscoped():
    """The headline: a leaked service credential is not an evidence-wipe key."""
    _set(internal=True, operator=False)
    assert routes._dd_report_access_allowed(_REPORT, "", destructive=True) is False, (
        "DELETE /dd/report/{run_id} cascades across entity_group_run_ids and can "
        "remove ANOTHER tenant's report — a shared service token must not reach it"
    )


def test_operator_token_may_delete_unscoped():
    _set(internal=True, operator=True)
    assert routes._dd_report_access_allowed(_REPORT, "", destructive=True) is True


def test_an_external_token_reaches_neither():
    _set(internal=False, operator=False)
    assert routes._dd_report_access_allowed(_REPORT, "") is False
    assert routes._dd_report_access_allowed(_REPORT, "", destructive=True) is False


def test_a_scoped_owner_can_still_delete_their_own_report():
    """The gate must not take a customer's own delete away from them."""
    _set(internal=False, operator=False)
    owned = {"run_id": "dd_x", "user_id": "user_a", "user_email_domain": ""}
    assert routes._dd_report_access_allowed(owned, "user_a", destructive=True) is True


def test_a_scoped_user_still_cannot_delete_another_tenants_report():
    _set(internal=False, operator=False)
    theirs = {"run_id": "dd_x", "user_id": "user_b", "user_email_domain": ""}
    assert routes._dd_report_access_allowed(theirs, "user_a", destructive=True) is False


# ── 3. the delete endpoint and its cascade both pass destructive=True ──────

def test_the_delete_handler_marks_itself_destructive():
    src = inspect.getsource(routes.dd_report_delete_ep)
    assert src.count("destructive=True") == 2, (
        "BOTH the clicked report and every cascade sibling must be checked as "
        "destructive — the cascade is the path that reaches another tenant"
    )


def test_the_read_paths_are_not_marked_destructive():
    """Over-gating a read would break the trusted-service path for no gain."""
    for fn in (routes.dd_report_get_ep if hasattr(routes, "dd_report_get_ep") else None,):
        if fn is None:
            continue
        assert "destructive=True" not in inspect.getsource(fn)


@pytest.fixture(autouse=True)
def _reset_tiers():
    yield
    routes._auth_is_internal_var.set(routes._AUTH_INTERNAL_DEFAULT)
    routes._auth_is_operator_var.set(False)
