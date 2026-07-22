"""R-F2863 — a registry coverage vault: what ARIA can look up, and whether it is LIVE.

TWO PROBLEMS THIS CLOSES

1. ARIA could not answer "is this data source actually working?". `capability_manifest`
   reports which jurisdictions have an adapter, but a jurisdiction whose upstream
   registry is down looks IDENTICAL to one that works. A source that is registered
   but dead, presented as coverage, is a false clean about our own capability — and
   it is the kind that reaches a customer as a confident empty result.

2. `_SUPPORTED_JURISDICTIONS` was hand-maintained ALONGSIDE the dispatch table, so a
   jurisdiction could be half-wired: present in one and absent from the other.
   In `dispatch` only -> never reachable (the gate rejects it first). In
   `_SUPPORTED_JURISDICTIONS` only -> claims coverage it cannot serve.
   The two sets happened to be IN SYNC when this was written (25/25, verified), so
   this half is PROPHYLACTIC: it makes the drift class impossible rather than fixing
   a live bug. Deriving one from the other is the same "never hand-maintained"
   promise capability_manifest already makes about itself.

THE HONESTY RULE, and the whole point of the module:
    liveness is TRI-STATE and defaults to UNPROVEN.
    live=True  -> we OBSERVED a successful lookup
    live=False -> we OBSERVED failures and no success
    live=None  -> never observed. NOT "probably fine".
Never claim a source is live because it is configured. That is the false clean this
platform exists to refuse, applied to our own capability claims.
"""
from __future__ import annotations

import asyncio

import pytest

from aria_service.intel import registry_coverage as rc
from aria_service.intel import registry_adapters as ra


@pytest.fixture(autouse=True)
def _isolated_state(monkeypatch):
    """In-memory state so tests never touch the real store."""
    store: dict = {}

    async def _get_json_strict(key):
        return store.get(key)

    async def _set_json(key, obj, ex=None, **kw):
        store[key] = obj
        return True

    from aria_service.intel import redis_store as rs
    monkeypatch.setattr(rs, "get_json_strict", _get_json_strict)
    monkeypatch.setattr(rs, "set_json", _set_json)
    return store


# ── 1. the half-wire class is now impossible ─────────────────────────────────

def test_supported_set_is_derived_from_the_dispatch_table():
    """Every dispatchable jurisdiction is supported, and vice versa — by construction."""
    assert set(ra._DISPATCH) == set(ra._SUPPORTED_JURISDICTIONS), (
        "the supported set must be DERIVED from dispatch, not maintained beside it"
    )


def test_dispatch_is_LATE_bound_to_the_module_namespace():
    """REGRESSION GUARD for a bug this ticket introduced and Pass 2 caught.

    Hoisting the dispatch table to module scope made it capture function OBJECTS
    at import time, so patching the module attribute no longer affected dispatch
    — it kept calling the original. Before the hoist the table was rebuilt on
    every call, which gave late binding for free, and
    test_rf302_capability_dispatch_routes_fi_to_prh depends on it.
    """
    calls = []

    async def _intercept(name, reg_number):
        calls.append(name)
        return None

    async def _no_gleif(name, iso2, reg_number):
        return None      # a None adapter result falls through to the GLEIF
                         # fallback, which makes a REAL network call — never
                         # let the suite depend on an external endpoint.

    original = ra._lookup_finland
    original_gleif = ra._gleif_global_fallback
    ra._lookup_finland = _intercept
    ra._gleif_global_fallback = _no_gleif
    try:
        asyncio.run(ra.lookup_entity(name="Modirum Oy", jurisdiction_iso2="FI"))
    finally:
        ra._lookup_finland = original
        ra._gleif_global_fallback = original_gleif
    assert calls == ["Modirum Oy"], (
        "patching the module attribute must intercept dispatch (late binding)"
    )


def test_a_new_adapter_needs_only_one_registration():
    """NEGATIVE CONTROL: adding to dispatch alone must make it supported.

    If this fails, the second hand-maintained list is back.
    """
    async def _fake(name, reg_number):
        return None

    ra._DISPATCH["ZZ"] = _fake
    try:
        assert "ZZ" in ra.supported_jurisdictions(), (
            "a jurisdiction in dispatch must be supported without a second edit"
        )
    finally:
        ra._DISPATCH.pop("ZZ", None)


# ── 2. liveness defaults to UNPROVEN, never to live ──────────────────────────

def test_unobserved_jurisdiction_is_UNPROVEN_not_live():
    """★ The honesty line: configured is not the same as working."""
    cov = asyncio.run(rc.coverage())
    ch = cov["jurisdictions"]["CH"]
    assert ch["adapter"], "CH must have an adapter registered"
    assert ch["live"] is None, f"never-observed must be None, got {ch['live']!r}"
    assert ch["status"] == "unproven", f"got {ch['status']!r}"
    assert ch["last_success_at"] is None


def test_a_successful_lookup_marks_it_live():
    asyncio.run(rc.record_outcome("CH", "switzerland_zefix_lindas", "success"))
    cov = asyncio.run(rc.coverage())
    ch = cov["jurisdictions"]["CH"]
    assert ch["live"] is True
    assert ch["status"] == "live"
    assert ch["last_success_at"], "a success must carry a timestamp as evidence"


def test_failures_without_any_success_mark_it_failing():
    asyncio.run(rc.record_outcome("NO", "norway_brreg", "error"))
    cov = asyncio.run(rc.coverage())
    no = cov["jurisdictions"]["NO"]
    assert no["live"] is False
    assert no["status"] == "failing"
    assert no["consecutive_failures"] == 1


def test_an_empty_result_is_not_a_failure_and_not_a_success():
    """A registry that answers 'no such company' is WORKING.

    Counting it as a failure would suspend a healthy source; counting it as a
    success would claim liveness we did not observe.
    """
    asyncio.run(rc.record_outcome("CH", "switzerland_zefix_lindas", "empty"))
    cov = asyncio.run(rc.coverage())
    ch = cov["jurisdictions"]["CH"]
    assert ch["live"] is None, "an empty result must not prove liveness"
    assert ch["status"] == "unproven"
    assert ch["consecutive_failures"] == 0, "an empty result is not a failure"


def test_a_success_clears_a_failure_streak():
    asyncio.run(rc.record_outcome("NO", "norway_brreg", "error"))
    asyncio.run(rc.record_outcome("NO", "norway_brreg", "error"))
    asyncio.run(rc.record_outcome("NO", "norway_brreg", "success"))
    no = asyncio.run(rc.coverage())["jurisdictions"]["NO"]
    assert no["consecutive_failures"] == 0
    assert no["status"] == "live"


# ── 3. the durable state must survive a transient store failure ──────────────

def test_a_deferred_store_read_must_not_wipe_liveness_history(monkeypatch):
    """The non-strict-read clobber class (R-F2664 / R-F2852 / R-F2854).

    A StoreReadError must SKIP the write, not persist an empty map over real
    history. Uses a strict read so the transient is distinguishable from 'empty'.
    """
    asyncio.run(rc.record_outcome("CH", "switzerland_zefix_lindas", "success"))

    from aria_service.intel import redis_store as rs
    writes: list = []

    async def _boom(key):
        raise rs.StoreReadError("state_store: no connection")

    async def _spy(key, obj, ex=None, **kw):
        writes.append(obj)
        return True

    monkeypatch.setattr(rs, "get_json_strict", _boom)
    monkeypatch.setattr(rs, "set_json", _spy)

    asyncio.run(rc.record_outcome("CH", "switzerland_zefix_lindas", "success"))
    assert writes == [], "a failed read must SKIP the write, never clobber history"


def test_a_hanging_store_degrades_to_unproven_and_does_not_hang(monkeypatch):
    """NEGATIVE CONTROL: this surface must answer when the store is SICK.

    That is precisely when someone asks "which of our sources still work". An
    unbounded await here would hang the caller — and the event-loop starvation
    history makes that a real foot-gun, not a hypothetical.
    """
    from aria_service.intel import redis_store as rs

    async def _hang(key):
        await asyncio.sleep(30)

    monkeypatch.setattr(rs, "get_json_strict", _hang)
    monkeypatch.setattr(rc, "_READ_TIMEOUT_S", 0.2)

    async def _run():
        return await asyncio.wait_for(rc.coverage(), timeout=5)

    cov = asyncio.run(_run())
    assert cov["jurisdictions"]["CH"]["status"] == "unproven", (
        "an unreadable store must degrade to unproven, never to a live claim"
    )
    assert cov["summary"]["live"] == 0


# ── 4. the exploration surface — what is NOT covered ─────────────────────────

def test_coverage_reports_the_uncovered_jurisdictions():
    """'What else can we explore' must be answerable from data, not memory."""
    cov = asyncio.run(rc.coverage())
    assert cov["summary"]["with_adapter"] >= 25
    assert cov["summary"]["manual_only"] > 0, "the uncovered gap must be visible"
    assert "MZ" in cov["manual_only"], "a hint-only jurisdiction must be listed"
    assert "CH" not in cov["manual_only"], "a covered jurisdiction must not be listed"
    # The two must partition the known world — no jurisdiction may vanish.
    overlap = set(cov["manual_only"]) & set(cov["jurisdictions"])
    assert not overlap, f"a jurisdiction cannot be both covered and manual-only: {overlap}"


def test_registry_status_is_UNKNOWN_until_observed_never_guessed():
    """★ Authority must not be inferred from the FUNCTION name.

    The `*_stub` convention (R-F2693) lives in the adapter STRING a lookup
    returns, not in the function. `_lookup_angola` returns "angola_gue_stub" —
    so deriving status from the function name would report all 8 stub adapters
    as registry AUTHORITY. That is a false clean about our own capability, and
    the evidence grade reads this field.

    So it stays None until a real lookup tells us what the adapter actually was.
    """
    cov = asyncio.run(rc.coverage())
    assert cov["jurisdictions"]["CH"]["registry_status"] is None, (
        "authority must not be guessed before a lookup has been observed"
    )


def test_observed_authority_and_stub_are_distinguished():
    """Once observed, a real registry and a stub must NOT read the same."""
    asyncio.run(rc.record_outcome("CH", "switzerland_zefix_lindas", "success"))
    asyncio.run(rc.record_outcome("AO", "angola_gue_stub", "success"))
    j = asyncio.run(rc.coverage())["jurisdictions"]

    assert j["CH"]["registry_status"] == "verified", "zefix IS a real registry source"
    assert j["AO"]["registry_status"] == "manual_required", (
        "a stub adapter must never report as registry authority"
    )
