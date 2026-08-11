"""R-F3868 — meter the paid search engine DD depends on.

Operator directive 2026-08-11: "brave API will be responding and be responsible
for DD reports."

Brave is the sole DD search engine (R-F3847) and ARIA's paid primary (R-F2318),
and NOTHING was counting its calls. `/api/aria/cost/external` returned
`by_service: {}, total_calls: 0`; Brave appears nowhere in the monthly cost
breakdown. So "how much of the plan have we used?" had no answer — not a wrong
answer, no answer.

That is exactly how the OpenSanctions exhaustion was discovered (§18): by a `429`
in production, after which no amount of retrying, pacing or breaker cooldown could
clear it, because the plan was simply spent. On the DD path that lands mid-report,
on a customer. An unmeasured dependency reads exactly like a healthy one — the
same shape as the §1 gates certified by an absence, and the §17 cost probe that
read `0.0` because it had no connection.

TWO PROPERTIES ARE LOAD-BEARING:

  1. NEVER INVENT HEADROOM. A fabricated denominator is worse than no gauge
     because it reads as reassurance. R-F3870 CORRECTION: this said "Brave does
     not return it", which was FALSE — Brave publishes x-ratelimit-* on every
     response, so ARIA reads the provider's own accounting rather than depending
     on an operator-supplied number. Before filing "the operator must tell us X",
     check whether the provider already does.

  2. A PACING 429 IS NOT A SPENT PLAN. §18 records the OpenSanctions defect of
     reporting the second as the first — "a wrong cause pointing at a wrong fix",
     telling the reader ARIA was going too fast when the correct action was an
     operator upgrade. Brave returns 429 for both, so the body is read, kept for
     audit, and an unrecognised 429 is NOT promoted to a quota verdict.
"""
from __future__ import annotations

import pytest

from aria_service.intel import brave_usage as bu


# ── property 2: the 429 distinction ─────────────────────────────────────────────

def test_a_spent_plan_is_classified_as_quota_exhausted():
    body = ("This API key has exceeded its monthly quota. "
            "Please upgrade your plan or wait for the reset.")
    assert bu.classify_429(body) == "quota_exhausted"


def test_a_pacing_limit_is_classified_as_rate_limit():
    assert bu.classify_429("Too many requests per second. Slow down.") == "rate_limit"


def test_an_unrecognised_429_is_not_promoted_to_a_quota_verdict():
    """Refusing to classify is not the same as classifying as transient (§22) —
    but inventing `quota_exhausted` raises a false alarm the operator pays money to
    act on. So it gets its own honest value."""
    assert bu.classify_429("") == "rate_limit_or_unknown"
    assert bu.classify_429("service unavailable") == "rate_limit_or_unknown"


def test_a_short_retry_after_indicates_pacing():
    assert bu.classify_429("", {"Retry-After": "3"}) == "rate_limit"


def test_a_quota_message_mentioning_rate_is_not_misread():
    """The OpenSanctions body says 'exceeded its rate limit for the month'. The
    word 'rate' must not drag a monthly exhaustion back into the pacing bucket —
    that IS the §18 defect, verbatim."""
    body = ("This API key has exceeded its rate limit for the month. "
            "Please wait to retry or contact support for a higher limit.")
    assert bu.classify_429(body) != "rate_limit", (
        "a MONTHLY exhaustion reported as pacing is the exact §18 defect")


# ── property 1: never invent headroom ───────────────────────────────────────────

def test_no_quota_configured_means_unknown_not_zero(monkeypatch):
    monkeypatch.delenv("BRAVE_MONTHLY_QUOTA", raising=False)
    assert bu.monthly_quota() is None


def test_a_junk_quota_is_unknown_not_a_default(monkeypatch):
    monkeypatch.setenv("BRAVE_MONTHLY_QUOTA", "not-a-number")
    assert bu.monthly_quota() is None
    monkeypatch.setenv("BRAVE_MONTHLY_QUOTA", "0")
    assert bu.monthly_quota() is None


@pytest.mark.asyncio
async def test_the_report_refuses_to_compute_a_percentage_without_a_quota(monkeypatch):
    monkeypatch.delenv("BRAVE_MONTHLY_QUOTA", raising=False)

    async def _get(k): return "40" if k.endswith(":total") else None
    async def _get_json(k): return None
    monkeypatch.setattr(bu.rs, "get", _get)
    monkeypatch.setattr(bu.rs, "get_json", _get_json)

    rep = await bu.usage_report()
    assert rep["quota"] is None
    assert rep["utilisation_pct"] is None, "a percentage of an unknown ceiling is a fiction"
    assert rep["remaining"] is None
    assert "BRAVE_MONTHLY_QUOTA" in rep.get("quota_hint", "")


@pytest.mark.asyncio
async def test_the_report_computes_headroom_once_a_quota_is_known(monkeypatch):
    monkeypatch.setenv("BRAVE_MONTHLY_QUOTA", "1000")

    async def _get(k): return "250" if k.endswith(":total") else None
    async def _get_json(k): return None
    monkeypatch.setattr(bu.rs, "get", _get)
    monkeypatch.setattr(bu.rs, "get_json", _get_json)

    rep = await bu.usage_report()
    assert rep["quota"] == 1000
    assert rep["remaining"] == 750
    assert rep["utilisation_pct"] == 25.0


# ── metering must never break the search it observes ───────────────────────────

@pytest.mark.asyncio
async def test_recording_survives_a_dead_store(monkeypatch):
    async def _boom(*a, **k):
        raise RuntimeError("state_store: no connection")

    monkeypatch.setattr(bu.rs, "incr", _boom)
    monkeypatch.setattr(bu.rs, "expire", _boom)
    monkeypatch.setattr(bu.rs, "set_json", _boom)
    monkeypatch.setattr(bu.rs, "get", _boom)
    await bu.record_call("ok", status=200)      # must not raise


def test_success_is_counted_not_only_failure():
    """A meter that counts only failures cannot answer 'how much of the plan have
    we used', which is the question that matters BEFORE the plan is spent."""
    from aria_service.tests._source_probe import function_source
    from aria_service.intel import web_search

    src = function_source(web_search, "_search_brave")
    assert '_brave_meter("ok"' in src
    assert '_brave_meter("rate_limited"' in src


def test_every_brave_outcome_branch_is_metered():
    """§21a — success AND failure. A branch that returns without counting is a
    call that silently consumed quota."""
    from aria_service.tests._source_probe import function_source
    from aria_service.intel import web_search

    src = function_source(web_search, "_search_brave")
    for outcome in ("ok", "rate_limited", "auth_failed", "http_error", "timeout"):
        assert f'_brave_meter("{outcome}"' in src, f"{outcome} branch is unmetered"


def test_the_429_body_is_captured_for_audit():
    """A classification nobody can audit is a guess wearing a verdict's clothes —
    and §18 proves the body text is the only signal that separates the two causes."""
    from aria_service.tests._source_probe import function_source

    src = function_source(bu, "_note_exhaustion")
    assert '"body"' in src and "wire_failure" in src


@pytest.mark.asyncio
async def test_exhaustion_reaches_the_brain(monkeypatch):
    """§21a — DD's search engine refusing service must not be a log line nobody
    reads. This is the alert that did not exist for OpenSanctions."""
    seen = {}

    def _wf(**kw):
        seen.update(kw)

    import aria_service.intel.engine_wiring as ew
    monkeypatch.setattr(ew, "wire_failure", _wf)

    async def _ok(*a, **k): return None
    monkeypatch.setattr(bu.rs, "set_json", _ok)

    await bu._note_exhaustion("quota_exhausted", "monthly quota exceeded")
    assert seen.get("gap_type") == "search_backend_failure"
    assert "quota_exhausted" in seen.get("detail", "")


# ── R-F3870: read the provider's own accounting ────────────────────────────────

#: The exact headers measured on the production key, 2026-08-11.
_LIVE_HEADERS = {
    "x-ratelimit-limit": "50, 0",
    "x-ratelimit-policy": "50;w=1, 0;w=2678400",
    "x-ratelimit-remaining": "49, 0",
    "x-ratelimit-reset": "1, 1763914",
}


def test_the_provider_publishes_its_limits_and_we_parse_both_windows():
    """R-F3870 — a better answer than BRAVE_MONTHLY_QUOTA: the provider's own
    accounting, on every response, instead of an operator guess that goes stale."""
    w = bu.parse_rate_limit_headers(_LIVE_HEADERS)

    assert len(w) == 2
    assert w[0]["window_s"] == 1 and w[0]["limit"] == 50 and w[0]["remaining"] == 49
    assert w[1]["window_s"] == 2678400          # ~31 days


def test_a_zero_limit_window_is_NOT_exhaustion():
    """THE TRAP. The 31-day window reports limit 0 / remaining 0, and that same
    response was HTTP 200 WITH RESULTS. So 0 means 'no cap advertised', not
    'spent'. Reading remaining==0 as exhaustion would raise a false P0 against a
    healthy key — the same absence-as-measurement error as the §17 `spent_usd: 0.0`
    scare and the §1 gates certified by an absence."""
    w = bu.parse_rate_limit_headers(_LIVE_HEADERS)

    assert w[1]["capped"] is False, "limit 0 on a 200 response means UNCAPPED"
    assert "utilisation_pct" not in w[1], "no percentage against a non-existent cap"


def test_a_real_cap_is_marked_capped_and_scored():
    w = bu.parse_rate_limit_headers({
        "x-ratelimit-limit": "2000",
        "x-ratelimit-policy": "2000;w=2678400",
        "x-ratelimit-remaining": "100",
        "x-ratelimit-reset": "500000",
    })
    assert w[0]["capped"] is True
    assert w[0]["utilisation_pct"] == 95.0


def test_missing_or_malformed_headers_yield_nothing_rather_than_guesses():
    assert bu.parse_rate_limit_headers({}) == []
    assert bu.parse_rate_limit_headers(None) == []
    junk = bu.parse_rate_limit_headers({"x-ratelimit-limit": "abc"})
    assert junk and junk[0]["limit"] is None and junk[0]["capped"] is False


@pytest.mark.asyncio
async def test_an_uncapped_window_never_alerts(monkeypatch):
    """The false-P0 guard, end to end: the live headers must produce NO alert."""
    fired = []

    import aria_service.intel.engine_wiring as ew
    monkeypatch.setattr(ew, "wire_failure", lambda **kw: fired.append(kw))

    async def _ok(*a, **k): return None
    monkeypatch.setattr(bu.rs, "set_json", _ok)

    await bu._record_plan_limits(_LIVE_HEADERS)
    assert fired == [], "an uncapped window must never raise an exhaustion alert"


@pytest.mark.asyncio
async def test_a_genuinely_low_long_window_does_alert(monkeypatch):
    """The other half: a REAL cap running out must reach the brain before it is
    spent — the alert that did not exist for OpenSanctions."""
    fired = []

    import aria_service.intel.engine_wiring as ew
    monkeypatch.setattr(ew, "wire_failure", lambda **kw: fired.append(kw))

    async def _ok(*a, **k): return None
    monkeypatch.setattr(bu.rs, "set_json", _ok)

    await bu._record_plan_limits({
        "x-ratelimit-limit": "2000",
        "x-ratelimit-policy": "2000;w=2678400",
        "x-ratelimit-remaining": "100",
        "x-ratelimit-reset": "500000",
    })
    assert fired and fired[0]["gap_type"] == "search_backend_failure"


@pytest.mark.asyncio
async def test_a_busy_one_second_bucket_is_not_an_incident(monkeypatch):
    """A 1s window at 90% is normal pacing, not something an operator can act on.
    Alerting on it would train everyone to ignore the alert."""
    fired = []

    import aria_service.intel.engine_wiring as ew
    monkeypatch.setattr(ew, "wire_failure", lambda **kw: fired.append(kw))

    async def _ok(*a, **k): return None
    monkeypatch.setattr(bu.rs, "set_json", _ok)

    await bu._record_plan_limits({
        "x-ratelimit-limit": "50",
        "x-ratelimit-policy": "50;w=1",
        "x-ratelimit-remaining": "2",
        "x-ratelimit-reset": "1",
    })
    assert fired == []
