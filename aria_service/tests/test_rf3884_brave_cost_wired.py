"""R-F3884 — the Brave cost recorder existed, was purpose-built, and had NO CALLERS.

THE DEFECT, MEASURED LIVE 2026-08-11 (hours after R-F3868 was called done):

    GET /api/aria/cost/external
    -> {"by_service": {}, "total_calls": 0, "total_cost_usd": 0}

That is **verbatim the symptom C-23 records as the proof Brave was unmetered.** The
endpoint whose emptiness defined the defect was never changed by the fix.

WHY. `cost_tracker.record_brave_call()` already existed — purpose-built for exactly
this, with a documented price (`_BRAVE_DEFAULT_COST_PER_CALL = 0.005`, "$5 / 1,000
queries … operator can override via BRAVE_COST_PER_CALL_USD"). A repo-wide grep found
**zero call sites**. R-F3868 then built a SECOND, parallel counter (`brave_usage`) and
surfaced it on `/api/aria/search/health`, which is a genuine improvement and is live —
but it left the operator's cost surface exactly as empty as it found it.

So the honest statement of the root cause is not "nothing counts Brave". It is: **the
counter built for Brave was never wired to the call site, and the replacement was
verified against a different surface than the one that defined the defect.** Checking
the fix against the evidence the defect record cites would have caught it immediately.

THE CAP INTERACTION IS LOAD-BEARING AND IS WHY THIS IS NOT A FREE CHANGE.
`_flush_external_pending` step 4 writes into `COST_MONTH_PREFIX{month}` — the SAME
composite month rollup `assert_monthly_cap` reads — so external spend counts toward
the §17 monthly cap. That is correct (Brave is real money and the operator watches
that number), but it means wiring this ADDS spend to the ceiling. Verified before
shipping: month-to-date was ~$48 of $600, and Brave at $0.005/call would add tens of
dollars a month, not hundreds.

WHAT THIS REFUSES TO DO: bill for a call Brave never answered. A timeout produced no
HTTP response, so it is recorded as an ATTEMPT with cost 0.0 rather than being either
hidden or charged — the same "never invent a number" rule that keeps
`monthly_quota()` returning None instead of zero.
"""
from __future__ import annotations

import pytest

from aria_service.intel import brave_usage as bu


def test_the_purpose_built_recorder_is_actually_called():
    """THE ROOT CAUSE, pinned along the WHOLE chain rather than at one file.

    `record_brave_call` sat with zero callers while the surface it feeds reported an
    empty dict. The billing hook belongs in `brave_usage.record_call` — the single
    funnel every `_search_brave` branch already passes through — not duplicated
    across five call sites in web_search, which is how the 429 branch ended up as
    the only one carrying headers (R-F3874).

    So this asserts the chain: _search_brave -> _brave_meter -> brave_usage.record_call
    -> cost_tracker.record_brave_call. Asserting only the last link would pass on a
    recorder that nothing reaches."""
    from aria_service.tests._source_probe import function_source, module_source
    from aria_service.intel import web_search

    assert "record_brave_call" in module_source(bu), (
        "cost_tracker.record_brave_call has no caller — /api/aria/cost/external "
        "will keep reporting by_service: {} for a PAID engine (R-F3884)")
    assert "record_call" in function_source(web_search, "_brave_meter"), (
        "_brave_meter must reach brave_usage.record_call, or the billing hook is "
        "wired to something the search path never invokes")
    assert "_brave_meter" in function_source(web_search, "_search_brave"), (
        "the search path must route through the metering funnel")


@pytest.mark.asyncio
async def test_a_served_call_is_billed(monkeypatch):
    """A response from Brave means the query was served and counted against the
    plan, so it must reach the cost surface the operator watches."""
    seen: list[dict] = []

    async def _rec(**kw):
        seen.append(kw)
        return {}

    from aria_service.intel import cost_tracker as ct
    monkeypatch.setattr(ct, "record_brave_call", _rec)
    monkeypatch.setattr(bu.rs, "incr", lambda *a, **k: _ok(1))
    monkeypatch.setattr(bu.rs, "expire", lambda *a, **k: _ok(True))
    monkeypatch.setattr(bu.rs, "get", lambda *a, **k: _ok(None))
    monkeypatch.setattr(bu.rs, "set_json", lambda *a, **k: _ok(None))

    await bu.record_call("ok", status=200)
    assert seen, "a 200 from Brave must be billed"
    assert seen[0]["success"] is True
    assert seen[0].get("cost_per_call_usd") is None, (
        "the price must come from cost_tracker/BRAVE_COST_PER_CALL_USD, not be "
        "hardcoded at the call site")


@pytest.mark.asyncio
async def test_a_timeout_is_recorded_but_not_billed(monkeypatch):
    """Brave never answered, so no query was served. Recording it as an attempt with
    cost 0.0 keeps it visible without inventing a charge — the same rule that makes
    monthly_quota() return None rather than zero."""
    seen: list[dict] = []

    async def _rec(**kw):
        seen.append(kw)
        return {}

    from aria_service.intel import cost_tracker as ct
    monkeypatch.setattr(ct, "record_brave_call", _rec)
    monkeypatch.setattr(bu.rs, "incr", lambda *a, **k: _ok(1))
    monkeypatch.setattr(bu.rs, "expire", lambda *a, **k: _ok(True))
    monkeypatch.setattr(bu.rs, "get", lambda *a, **k: _ok(None))
    monkeypatch.setattr(bu.rs, "set_json", lambda *a, **k: _ok(None))

    await bu.record_call("timeout")
    assert seen, "a timeout must still be visible as an attempt"
    assert seen[0]["cost_per_call_usd"] == 0.0, "must not bill for an unanswered call"
    assert seen[0]["success"] is False


@pytest.mark.asyncio
async def test_cost_recording_never_breaks_the_search(monkeypatch):
    """Observability must never raise into the search path — the invariant
    `_brave_meter` and `_safe_headers` already protect."""
    async def _boom(**kw):
        raise RuntimeError("cost store down")

    from aria_service.intel import cost_tracker as ct
    monkeypatch.setattr(ct, "record_brave_call", _boom)
    monkeypatch.setattr(bu.rs, "incr", lambda *a, **k: _ok(1))
    monkeypatch.setattr(bu.rs, "expire", lambda *a, **k: _ok(True))
    monkeypatch.setattr(bu.rs, "get", lambda *a, **k: _ok(None))
    monkeypatch.setattr(bu.rs, "set_json", lambda *a, **k: _ok(None))

    await bu.record_call("ok", status=200)      # must not raise


def test_external_spend_feeds_the_monthly_cap_and_that_is_deliberate():
    """A reader must not be able to wire another paid service in here believing it
    is free of the §17 ceiling. `_flush_external_pending` writes COST_MONTH_PREFIX,
    the same rollup assert_monthly_cap reads."""
    from aria_service.tests._source_probe import function_source
    from aria_service.intel import cost_tracker

    src = function_source(cost_tracker, "_flush_external_pending")
    assert "COST_MONTH_PREFIX" in src, (
        "if external spend no longer feeds the month rollup, the R-F3884 docstring "
        "and the cap reasoning behind it are stale — re-check before trusting them")


async def _ok(v):
    return v
