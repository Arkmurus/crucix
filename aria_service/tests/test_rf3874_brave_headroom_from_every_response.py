"""R-F3874 — the pre-exhaustion gauge was wired only to the exhaustion event.

THE DEFECT. R-F3870 established that Brave publishes `x-ratelimit-limit /
-remaining / -reset / -policy` on EVERY response, and built `parse_rate_limit_headers`
+ `_record_plan_limits` to read ARIA's own headroom from the provider's accounting —
including a `_WARN_AT` alert at 80% consumed, so the operator hears about a ceiling
BEFORE it is hit.

But `_search_brave` passes `headers=` to the meter on exactly ONE of its five branches:
the 429. The success branch — which is the overwhelming majority of calls, and which
carries the identical `x-ratelimit-*` headers — discarded them, as did the auth-failure
and http-error branches.

So `crucix:aria:brave:plan_limits` could only ever be written by a 429: **the gauge
built to warn before exhaustion could only be fed by exhaustion itself**, and its 80%
warning path was unreachable in production. The evidence in R-F3870's own docstring was
measured on an `HTTP 200 with results` — a branch the fix did not read.

Live 2026-08-11: `brave_usage.plan_limits: null`, `monthly: {}`.

THE SECOND DEFECT, same family. `usage_report` reads with the non-strict `get`/`get_json`,
whose documented contract returns `None` on a STORE FAILURE as well as on a genuinely
absent key (R-F1, redis_store.py:299-303). So an unreadable store renders as
`monthly: {}, plan_limits: null` — "Brave has never been called and advertises no
limits" — which is indistinguishable from a healthy, quiet key.

That is §17's fabricated-P0 shape (`spent_usd: 0.0` from a probe with no connection)
reproduced INSIDE the module written to prevent exactly that class, and §1's three
Phase A gates certified by an absence. A meter that cannot say "I could not measure"
is not a meter.

THIRD: plan limits went out RAW, with no age. A 31-day window read weeks ago would
present as current headroom after a plan downgrade.
"""
from __future__ import annotations

import time

import pytest

from aria_service.intel import brave_usage as bu


# ── the root cause: every branch holding a response must feed the gauge ─────────

def _meter_call(src: str, outcome: str) -> str:
    """The full `_brave_meter(...)` call for one outcome, paren-balanced.

    Naive `find(")")` stops at the `)` inside a nested call like `_safe_body(resp)`
    and silently reports a TRUNCATED argument list — a guard that goes blind rather
    than failing (R-F3791), and it produced a false failure on the one branch that
    was already correct.
    """
    start = src.find(f"_brave_meter({outcome}")
    assert start != -1, f"no _brave_meter call for {outcome}"
    depth = 0
    for i in range(src.find("(", start), len(src)):
        if src[i] == "(":
            depth += 1
        elif src[i] == ")":
            depth -= 1
            if depth == 0:
                return src[start:i + 1]
    raise AssertionError(f"unbalanced parens scanning {outcome}")


def test_the_success_branch_passes_rate_limit_headers():
    """THE ROOT CAUSE, pinned at the call site. Brave sends x-ratelimit-* on a 200;
    reading them only on a 429 means the gauge can only be fed by the event it
    exists to pre-empt."""
    from aria_service.tests._source_probe import function_source
    from aria_service.intel import web_search

    src = function_source(web_search, "_search_brave")
    ok_call = _meter_call(src, '"ok"')
    assert "headers=" in ok_call, (
        "the SUCCESS branch discards Brave's published headroom — R-F3874. "
        f"got: {ok_call!r}")


def test_every_branch_with_a_response_feeds_the_gauge():
    """auth_failed and http_error also carry the headers. A gauge fed by a subset
    of responses under-reports consumption, which errs toward false reassurance."""
    from aria_service.tests._source_probe import function_source
    from aria_service.intel import web_search

    src = function_source(web_search, "_search_brave")
    for outcome in ('"ok"', '"auth_failed"', '"http_error"', '"rate_limited"'):
        seg = _meter_call(src, outcome)
        assert "headers=" in seg, f"{outcome} branch does not pass headers (R-F3874)"


def test_the_parser_can_still_detect_a_branch_that_drops_headers():
    """R-F3858 — a guard that cannot fail is not a guard. This is the control:
    the extractor must return a call whose headers are genuinely absent."""
    fake = 'await _brave_meter("ok" if r else "empty", status=200, body=_safe_body(resp))'
    assert "headers=" not in _meter_call(fake, '"ok"')
    assert _meter_call(fake, '"ok"').endswith(")")


@pytest.mark.asyncio
async def test_a_successful_call_records_the_published_limits(monkeypatch):
    """The capability, end to end through record_call: a 200 must persist what the
    provider said about the plan."""
    written: dict[str, object] = {}

    async def _set_json(k, v, ex=None, **kw): written[k] = v
    async def _incr(k, *a, **kw): return 1
    async def _expire(k, s): return True
    async def _get(k): return None

    monkeypatch.setattr(bu.rs, "set_json", _set_json)
    monkeypatch.setattr(bu.rs, "incr", _incr)
    monkeypatch.setattr(bu.rs, "expire", _expire)
    monkeypatch.setattr(bu.rs, "get", _get)

    # The exact headers measured live on the production key (R-F3870).
    headers = {
        "x-ratelimit-limit": "50, 0",
        "x-ratelimit-policy": "50;w=1, 0;w=2678400",
        "x-ratelimit-remaining": "49, 0",
        "x-ratelimit-reset": "1, 1763914",
    }
    await bu.record_call("ok", status=200, headers=headers)

    assert bu._PLAN_KEY in written, (
        "a SUCCESSFUL Brave call must record the plan limits it was handed")
    windows = written[bu._PLAN_KEY]["windows"]
    assert windows[0] == {"window_s": 1, "limit": 50, "remaining": 49,
                          "reset_s": 1, "capped": True, "utilisation_pct": 2.0}
    # The 31-day window: limit 0 on an HTTP 200 means UNCAPPED, never exhausted.
    assert windows[1]["capped"] is False


# ── absence must not read as health ─────────────────────────────────────────────

@pytest.mark.asyncio
async def test_an_unreadable_store_is_reported_as_unreadable_not_as_zero(monkeypatch):
    """§17's fabricated P0, in miniature. A dead store previously rendered as
    `monthly: {}, plan_limits: null` — 'Brave was never called' — which reads as
    health. It must say it could not measure."""
    async def _boom(*a, **k):
        raise bu.rs.StoreReadError("state_store: no connection")

    monkeypatch.setattr(bu.rs, "get_strict", _boom)
    monkeypatch.setattr(bu.rs, "get_json_strict", _boom)

    rep = await bu.usage_report()
    assert rep["store_readable"] is False, (
        "an unreadable store must be declared, not rendered as an empty meter")
    assert rep["monthly"] is None, "counts we could not read are unknown, not zero"
    assert rep.get("plan_limits_state") == "unreadable"


@pytest.mark.asyncio
async def test_a_genuinely_quiet_meter_is_reported_as_never_observed(monkeypatch):
    """The other half of property 1 — and the half that keeps this honest. A store
    that reads fine and simply has nothing in it must NOT be reported unreadable."""
    async def _get_strict(k): return None
    async def _get_json_strict(k): return None

    monkeypatch.setattr(bu.rs, "get_strict", _get_strict)
    monkeypatch.setattr(bu.rs, "get_json_strict", _get_json_strict)

    rep = await bu.usage_report()
    assert rep["store_readable"] is True
    assert rep["monthly"] == {}
    assert rep["plan_limits_state"] == "never_observed"
    assert rep["plan_limits"] is None


@pytest.mark.asyncio
async def test_stale_plan_limits_are_declared_stale(monkeypatch):
    """A 31-day window read weeks ago is not current headroom. Reporting it raw
    would let a plan downgrade present as available capacity."""
    old = time.time() - (40 * 86400)

    async def _get_strict(k): return None
    async def _get_json_strict(k):
        if k == bu._PLAN_KEY:
            return {"windows": [{"window_s": 2678400, "limit": 1000,
                                 "remaining": 900, "capped": True}], "at": old}
        return None

    monkeypatch.setattr(bu.rs, "get_strict", _get_strict)
    monkeypatch.setattr(bu.rs, "get_json_strict", _get_json_strict)

    rep = await bu.usage_report()
    assert rep["plan_limits_state"] == "stale"
    assert rep["plan_limits"]["age_s"] >= 39 * 86400


@pytest.mark.asyncio
async def test_fresh_plan_limits_are_not_declared_stale(monkeypatch):
    """Property 2 (R-F3858) — the staleness guard must be able to come back clean."""
    async def _get_strict(k): return None
    async def _get_json_strict(k):
        if k == bu._PLAN_KEY:
            return {"windows": [{"window_s": 2678400, "limit": 1000,
                                 "remaining": 900, "capped": True}],
                    "at": time.time() - 60}
        return None

    monkeypatch.setattr(bu.rs, "get_strict", _get_strict)
    monkeypatch.setattr(bu.rs, "get_json_strict", _get_json_strict)

    rep = await bu.usage_report()
    assert rep["plan_limits_state"] == "fresh"


@pytest.mark.asyncio
async def test_the_operator_is_not_asked_for_what_the_provider_already_publishes(
        monkeypatch):
    """R-F3870's lesson — measuring beats asking. Once the provider's own limits are
    on record, telling the operator to go set BRAVE_MONTHLY_QUOTA is a fabricated
    task, and a fabricated task is how a real one gets ignored."""
    monkeypatch.delenv("BRAVE_MONTHLY_QUOTA", raising=False)

    async def _get_strict(k): return None
    async def _get_json_strict(k):
        if k == bu._PLAN_KEY:
            return {"windows": [{"window_s": 2678400, "limit": 2000,
                                 "remaining": 1500, "capped": True}],
                    "at": time.time()}
        return None

    monkeypatch.setattr(bu.rs, "get_strict", _get_strict)
    monkeypatch.setattr(bu.rs, "get_json_strict", _get_json_strict)

    rep = await bu.usage_report()
    assert "quota_hint" not in rep, (
        "the provider published a real ceiling — do not ask the operator for one")


@pytest.mark.asyncio
async def test_the_hint_survives_when_nothing_publishes_a_ceiling(monkeypatch):
    """...and the converse, so the hint is not simply deleted."""
    monkeypatch.delenv("BRAVE_MONTHLY_QUOTA", raising=False)

    async def _get_strict(k): return None
    async def _get_json_strict(k): return None

    monkeypatch.setattr(bu.rs, "get_strict", _get_strict)
    monkeypatch.setattr(bu.rs, "get_json_strict", _get_json_strict)

    rep = await bu.usage_report()
    assert "BRAVE_MONTHLY_QUOTA" in rep.get("quota_hint", "")


@pytest.mark.asyncio
async def test_reporting_never_raises_into_the_caller(monkeypatch):
    """Observability must never break the surface that renders it."""
    async def _boom(*a, **k):
        raise RuntimeError("unexpected")

    monkeypatch.setattr(bu.rs, "get_strict", _boom)
    monkeypatch.setattr(bu.rs, "get_json_strict", _boom)
    rep = await bu.usage_report()
    assert isinstance(rep, dict)
