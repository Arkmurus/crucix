"""R-F3628 step 2 — make the internal-token cross-tenant branch OBSERVABLE.

WHY THIS EXISTS
---------------
`routes/aria.py:238` declares the auth ContextVar with the PERMISSIVE default:

    _auth_is_internal_var = ContextVar("aria_auth_is_internal", default=True)

All three readers are `if not _auth_is_internal_var.get(): <deny>`, so the default
GRANTS cross-tenant access. Production is safe today only because two accidents stack:

  1. ARIA_API_TOKEN == ARIA_INTERNAL_TOKEN (both digest 15fb531c831949cc), so
     require_aria_token's `bool(tok and not compare_digest(...))` always evaluates
     False — the internal branch has NEVER executed in production; and
  2. the setter always runs in the same context as the reader (R-F2456 made
     _router_auth_dep async precisely so the .set() propagates).

Rotating the tokens apart activates branch (1) for the first time. These tests exist so
that activation lands on covered code instead of on a comment.

WHAT THIS FILE DOES AND DOES NOT CLAIM
--------------------------------------
It PINS CURRENT BEHAVIOUR. It does not assert the behaviour is correct — that is the
decision R-F3628 step 3/4 makes. Pinning first means the eventual flip to
`default=False` shows up as a deliberate, reviewable diff in these assertions rather
than as a silent change in production.

THE CONTEXTVAR TRAP
-------------------
`monkeypatch` cannot patch a ContextVar's value, and setting it in the ambient test
context does not reliably reach code under test — this repo has already been bitten
(R-F3449, "a ContextVar set in the AMBIENT context"). Every test below uses
`var.set(...)` with the returned token and `var.reset(token)` in a finally.
"""

from __future__ import annotations

import pytest

from aria_service.routes import aria as _aria

VAR = _aria._auth_is_internal_var


class _internal:
    """Set the auth ContextVar for the duration of the block, then restore it."""

    def __init__(self, value: bool):
        self.value = value
        self.token = None

    def __enter__(self):
        self.token = VAR.set(self.value)
        return self

    def __exit__(self, *exc):
        VAR.reset(self.token)
        return False


# ── The default itself, pinned so step 3 is a visible diff ───────────────────

def test_the_contextvar_default_is_currently_permissive():
    """The whole of R-F3628 in one assertion.

    A security-relevant ContextVar whose default GRANTS access is safe only while the
    setter is guaranteed to run first. When step 3 flips this to False, THIS test is
    the one that must be deliberately updated — which is the point: the change becomes
    a reviewed decision instead of an invisible behavioural shift.
    """
    assert VAR.get() is True, (
        "routes/aria.py:238 default. If this is now False, R-F3628 step 3 has landed — "
        "update this test and confirm the in-process callers below were re-checked."
    )


# ── _dd_report_access_allowed (routes/aria.py:1250) ──────────────────────────

def test_unscoped_caller_is_granted_every_report_when_internal():
    """The branch that has never run in production. `user_id=''` + internal → True
    for a report owned by someone else entirely."""
    victim = {"user_id": "someone-else", "user_email_domain": "victim.example"}
    with _internal(True):
        assert _aria._dd_report_access_allowed(victim, "") is True


def test_unscoped_caller_is_denied_when_not_internal():
    """Today's only reachable state: the tokens are identical, so auth always sets
    False and the unscoped caller is refused."""
    victim = {"user_id": "someone-else", "user_email_domain": "victim.example"}
    with _internal(False):
        assert _aria._dd_report_access_allowed(victim, "") is False


def test_owner_scoping_is_unaffected_by_the_internal_flag():
    """The flag must only govern the UNSCOPED path. A caller presenting a user_id is
    judged by ownership either way — otherwise flipping the default in step 3 would
    silently change access for ordinary authenticated users, which is not the intent.
    """
    owned = {"user_id": "alice", "user_email_domain": "acme.example"}
    other = {"user_id": "bob", "user_email_domain": "other.example"}
    for flag in (True, False):
        with _internal(flag):
            assert _aria._dd_report_access_allowed(owned, "alice") is True, flag
            assert _aria._dd_report_access_allowed(other, "alice") is False, flag


# ── _dd_owned_entity_ids (routes/aria.py:1518) ───────────────────────────────

@pytest.mark.asyncio
async def test_unscoped_owned_ids_returns_NO_FILTER_when_internal():
    """`None` is not "nothing" — it is the sentinel for "apply no ownership filter".
    Callers treat it as unrestricted, so this is the cross-tenant grant in its most
    dangerous form: an absence that reads as permission."""
    with _internal(True):
        assert await _aria._dd_owned_entity_ids("") is None


@pytest.mark.asyncio
async def test_unscoped_owned_ids_returns_EMPTY_SET_when_not_internal():
    """Deny is an empty set, NOT None. The two are opposite meanings in the same
    return position — the distinction is the whole ACL."""
    with _internal(False):
        assert await _aria._dd_owned_entity_ids("") == set()


@pytest.mark.asyncio
async def test_none_and_empty_set_are_not_interchangeable():
    """Guard against a future refactor collapsing the sentinel. If someone 'tidies'
    `return None` into `return set()` — or vice versa — the ACL inverts silently."""
    with _internal(True):
        permissive = await _aria._dd_owned_entity_ids("")
    with _internal(False):
        restrictive = await _aria._dd_owned_entity_ids("")
    assert permissive is None and restrictive == set()
    assert permissive is not restrictive


# ── The in-process caller the docstring calls out ────────────────────────────

def test_a_caller_that_never_ran_auth_inherits_the_permissive_default():
    """routes/aria.py:1268-1271 says the default is True "for auth-bypass callers
    (tests / public-bypass), so their behaviour is unchanged".

    So the default is DELIBERATE for in-process callers, and step 3 is therefore NOT
    unconditionally behaviour-neutral: the 10/10 caller trace proved every HTTP reader
    runs the setter first, but a direct Python call into these helpers does not.
    This test states that dependency explicitly so step 3 cannot be shipped without
    confronting it.
    """
    victim = {"user_id": "someone-else"}
    # No _internal() block: this is a bare call, exactly like an in-process caller.
    assert _aria._dd_report_access_allowed(victim, "") is True, (
        "a caller that never ran auth is currently treated as INTERNAL"
    )
