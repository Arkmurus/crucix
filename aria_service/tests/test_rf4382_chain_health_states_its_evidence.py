"""R-F4382 (C-327) — `resilient: true` must not mean "nobody has called recently".

THE DEFECT. `get_health()` reports `resilient` from `_chain_exhaustion_age()`,
which reads a flag set on total-chain failure and cleared on success — but that
flag EXPIRES after `_CHAIN_EXHAUSTION_TTL_S` (120s). So two very different
states are reported identically:

    a request SUCCEEDED recently                  -> resilient: true
    the last failure was >120s ago (or no call
    has been made at all since boot)              -> resilient: true

Measured live on aria-intel 2026-08-27, during a ~5h total outage of the sole
provider (the RunPod pod had EXITED; every path returned HTTP 404):

    first probe, chain idle   -> resilient: true,  last_exhaustion_age_s: null
    immediately after a chat  -> resilient: false, last_exhaustion_age_s: 1.6

The outage was continuous across both readings. The mechanism is not broken —
R-F3477 registers the failure correctly — but with sparse traffic the verdict
laps back to healthy every two minutes, so a monitor sampling `/health` sees
green for most of an ongoing outage. An unproven chain and a working chain are
the same reading, which is the absence-reads-as-health shape §1 records against
three Phase A gates and §17 against the cost probe.

THE FIX IS PROVENANCE, NOT A NEW VERDICT. `resilient` keeps its meaning exactly
(R-F3477's outcome semantics, and `self_introspect_guard` + admission both read
it), because redefining a live safety field to fix a reporting gap is how the
next defect gets introduced. What is added is the EVIDENCE behind it, in the
same idiom §27f already uses for Brave's `plan_limits_state`:

    chain_evidence: fresh_success | fresh_failure | stale | never_observed

`stale` is the state that was previously invisible and is the whole point: it
says "this verdict rests on nothing recent". A reader can no longer mistake an
untested chain for a proven one.

Run: python -m pytest aria_service/tests/test_rf4382_chain_health_states_its_evidence.py -v
"""
from __future__ import annotations

import time

import pytest


def _chain():
    """A chain object with its outcome bookkeeping reset, no providers dialled."""
    from aria_service.llm import fallback

    fb = fallback.FallbackProvider.__new__(fallback.FallbackProvider)
    fb.providers = []
    fb._stats = {}
    fb._chain_exhausted_at = 0.0
    fb._chain_last_success_at = 0.0
    return fb


def test_a_chain_that_has_never_served_is_not_reported_as_proven():
    """Boot state: no call has been made, so there is no evidence either way."""
    fb = _chain()
    h = fb.get_health()
    assert h.get("chain_evidence") == "never_observed", (
        f"a chain with no recorded outcome must say so; got "
        f"{h.get('chain_evidence')!r} alongside resilient={h.get('resilient')}"
    )


def test_a_recent_success_is_fresh_evidence():
    fb = _chain()
    fb._record_chain_success()
    h = fb.get_health()
    assert h.get("chain_evidence") == "fresh_success"
    assert h.get("last_success_age_s") is not None
    assert h["last_success_age_s"] < 5


def test_a_recent_failure_is_fresh_evidence():
    fb = _chain()
    fb._record_chain_exhausted()
    h = fb.get_health()
    assert h.get("chain_evidence") == "fresh_failure", (
        "a chain that just exhausted every provider has fresh evidence — of "
        "failure"
    )


def test_the_live_trap_a_lapsed_failure_is_stale_not_healthy():
    """THE DEFECT: a >120s-old failure read exactly like a working chain.

    This is the state the live probe caught during the outage — the flag had
    lapsed, `last_exhaustion_age_s` was null, and `resilient` was true while
    every request was returning HTTP 404.
    """
    from aria_service.llm import fallback

    fb = _chain()
    # A failure older than the exhaustion TTL — exactly what the sparse-traffic
    # outage produced between requests.
    fb._chain_exhausted_at = time.time() - (fallback._CHAIN_EXHAUSTION_TTL_S + 30)
    h = fb.get_health()

    assert h.get("last_exhaustion_age_s") is None, (
        "precondition: the TTL has lapsed, which is what made this invisible"
    )
    assert h.get("chain_evidence") == "stale", (
        f"a chain whose only evidence is a lapsed failure must report `stale`, "
        f"not be indistinguishable from a proven-healthy chain. Got "
        f"chain_evidence={h.get('chain_evidence')!r}, "
        f"resilient={h.get('resilient')}"
    )


def test_resilient_keeps_its_existing_meaning():
    """This adds provenance; it must NOT redefine a live safety field.

    `self_introspect_guard` and the admission path both read `resilient`.
    Changing what it means to fix a reporting gap is how the next defect gets
    introduced, so the contract is pinned here.
    """
    fb = _chain()
    fb._record_chain_exhausted()
    assert fb.get_health()["resilient"] is False

    fb2 = _chain()
    fb2._record_chain_success()
    assert fb2.get_health()["resilient"] is False, (
        "resilient also requires an ACTIVE provider (len(active) > 0); with no "
        "providers configured it stays False — R-F3477's semantics, unchanged"
    )


def test_success_supersedes_a_stale_failure():
    """Proof beats a stale flag — the existing `_record_chain_success` contract."""
    from aria_service.llm import fallback

    fb = _chain()
    fb._chain_exhausted_at = time.time() - (fallback._CHAIN_EXHAUSTION_TTL_S + 30)
    fb._record_chain_success()
    h = fb.get_health()
    assert h.get("chain_evidence") == "fresh_success", (
        "a real success must supersede a lapsed failure, not tie with it"
    )
