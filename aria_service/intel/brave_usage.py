"""brave_usage — meter the paid search engine DD depends on.

R-F3868 (2026-08-11). Operator directive the same day: "brave API will be
responding and be responsible for DD reports."

WHY THIS EXISTS. Brave is now the sole DD search engine (R-F3847) and ARIA's paid
primary (R-F2318), and **nothing was counting its calls**. `/api/aria/cost/external`
returned `by_service: {}, total_calls: 0`, and Brave appears nowhere in the monthly
cost breakdown. So the question "how much of the plan have we used?" had no answer
at all — not a wrong answer, no answer.

That is precisely how the OpenSanctions exhaustion was discovered (§18): not by a
gauge, but by a `429` in production, after which **no amount of retrying, pacing or
breaker cooldown could clear it** because the plan was simply spent. On the DD path
that failure lands mid-report, on a customer. An unmeasured dependency reads
exactly like a healthy one — the same shape as the §1 gates certified by an absence
and the §17 cost probe that read `0.0` because it had no connection.

WHAT THIS DELIBERATELY DOES NOT DO: invent headroom. A fabricated denominator
would be worse than no gauge, because it reads as reassurance.

R-F3870 CORRECTION — an earlier version of this docstring said "Brave does not
publish the plan's remaining allowance on the response". That was FALSE and is
left here as the correction rather than quietly deleted. Brave publishes
`x-ratelimit-limit / -remaining / -reset / -policy` on EVERY response, so ARIA
reads her own headroom from the provider's accounting (`parse_rate_limit_headers`)
instead of depending on an operator-supplied number that goes stale.
`BRAVE_MONTHLY_QUOTA` remains supported as an override for a ceiling the headers
do not advertise, but it is no longer the only source — and it was never a good one.

The lesson generalises: before filing "the operator must tell us X", check whether
the provider already does. Measuring beats asking.

THE 429 DISTINCTION IS LOAD-BEARING. §18 records the OpenSanctions defect where a
monthly-quota exhaustion was reported as a rate limit — "a wrong cause pointing at
a wrong fix", telling the reader ARIA was going too fast when the plan was spent.
Brave returns 429 for BOTH, so `classify_429` reads the body, keeps the raw text
for audit, and refuses to guess: an unrecognised 429 stays `rate_limit_or_unknown`
rather than being promoted to a quota verdict nobody measured.
"""
from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from typing import Any

from . import redis_store as rs
from .wire import fail_wire

logger = logging.getLogger("aria.brave_usage")

_MONTH_PREFIX = "crucix:aria:brave:usage:month:"      # + YYYY-MM
_DAY_PREFIX = "crucix:aria:brave:usage:day:"          # + YYYY-MM-DD
_STATE_KEY = "crucix:aria:brave:usage:state"          # last 429, classification
_PLAN_KEY = "crucix:aria:brave:plan_limits"           # R-F3870: provider-published limits
#: 400 days, mirroring cost_tracker.COST_MONTH_TTL — long enough to be a record.
_MONTH_TTL = 34_560_000
_DAY_TTL = 2 * 86_400
#: Warn once consumption crosses this fraction of a KNOWN quota.
_WARN_AT = 0.8
#: R-F3874 — beyond this, a plan reading is a historical note, not current headroom.
#: A plan can be downgraded between observations, so presenting an old window as
#: available capacity would be a fabricated reassurance.
_PLAN_STALE_S = 86_400

#: Outcomes worth separating. "ok" is the only one that returned results.
OUTCOMES = ("ok", "rate_limited", "auth_failed", "http_error", "timeout", "empty")


def _month() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m")


def _day() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def monthly_quota() -> int | None:
    """The plan's monthly call ceiling, or None when the operator has not set it.

    None is an honest "we do not know", NOT zero and NOT a default. Every consumer
    must render it as `unknown` rather than computing a percentage against a number
    nobody verified.
    """
    raw = (__import__("os").getenv("BRAVE_MONTHLY_QUOTA") or "").strip()
    if not raw:
        return None
    try:
        v = int(raw)
        return v if v > 0 else None
    except ValueError:
        return None


def classify_429(body: str, headers: Any = None) -> str:
    """Is this 429 a PACING problem or a SPENT PLAN? They need opposite responses.

    §18: OpenSanctions reported an exhausted monthly plan as a rate limit, so the
    DD obstacle line told the reader ARIA was going too fast when the correct
    action was an operator upgrade. Retrying cannot fix a spent plan, and upgrading
    is not the answer to going too fast.

    Refuses to guess. An unrecognised 429 returns `rate_limit_or_unknown`, never a
    quota verdict — "could not classify" is not "classified as transient" (§22),
    but promoting it to `quota_exhausted` would raise a false alarm that costs the
    operator money to act on.
    """
    text = (body or "").lower()
    # THE DISCRIMINATOR IS THE BILLING PERIOD, NOT THE PHRASE "RATE LIMIT".
    # Providers describe a monthly ceiling in rate-limit language: the real
    # OpenSanctions body (§18) is "This API key has exceeded its RATE LIMIT FOR THE
    # MONTH". An earlier draft of this function keyed off "rate limit" and bucketed
    # that as pacing — reproducing the very defect this function exists to prevent,
    # caught only because a test asserted the real body text. A period word wins.
    period_markers = ("for the month", "this month", "monthly", "per month",
                      "billing", "quota", "plan", "subscription", "credits",
                      "upgrade", "higher limit")
    pacing_markers = ("per second", "per minute", "requests per second",
                      "requests per minute", "slow down", "rate limit per")
    if any(m in text for m in period_markers):
        return "quota_exhausted"
    if any(m in text for m in pacing_markers):
        return "rate_limit"
    # Retry-After on a per-second limiter is small; a spent plan has no useful one.
    try:
        ra = float((headers or {}).get("Retry-After") or 0)
        if 0 < ra <= 120:
            return "rate_limit"
    except Exception:
        pass
    return "rate_limit_or_unknown"


def parse_rate_limit_headers(headers: Any) -> list[dict[str, Any]]:
    """Brave PUBLISHES its own limits — read them instead of asking the operator.

    R-F3870. Measured live on the production key:

        x-ratelimit-limit     = 50, 0
        x-ratelimit-policy    = 50;w=1, 0;w=2678400
        x-ratelimit-remaining = 49, 0
        x-ratelimit-reset     = 1, 1763914

    Two windows, comma-separated and positionally aligned across all four headers:
    a 1-second window (limit 50, 49 left) and a 2,678,400s ≈ 31-day window. So ARIA
    can measure her own headroom directly, which is a better answer than
    BRAVE_MONTHLY_QUOTA — an operator-supplied number is a guess that goes stale,
    this is the provider's own accounting.

    THE TRAP, AND IT IS THE WHOLE REASON THIS FUNCTION IS CAREFUL. The 31-day
    window reports `limit 0, remaining 0` — and that same response was **HTTP 200
    with results**. So `0` here means "no cap advertised on this plan", NOT
    "exhausted". Reading `remaining == 0` as exhaustion would raise a false P0
    against a perfectly healthy key: exactly the absence-collapsing-into-a-
    measurement error that produced the `spent_usd: 0.0` scare (§17) and the three
    Phase A gates certified by an absence (§1). A window with `limit <= 0` is
    therefore reported as `capped: False` and is never eligible for an alert.
    """
    def _parts(name: str) -> list[str]:
        try:
            raw = headers.get(name) if hasattr(headers, "get") else None
        except Exception:
            raw = None
        return [p.strip() for p in str(raw).split(",")] if raw else []

    limits = _parts("x-ratelimit-limit")
    remaining = _parts("x-ratelimit-remaining")
    resets = _parts("x-ratelimit-reset")
    policies = _parts("x-ratelimit-policy")

    out: list[dict[str, Any]] = []
    for i, lim in enumerate(limits):
        def _num(seq: list[str], idx: int) -> int | None:
            try:
                return int(float(seq[idx]))
            except (IndexError, ValueError, TypeError):
                return None

        window = None
        try:                                   # "0;w=2678400" -> 2678400
            if i < len(policies) and "w=" in policies[i]:
                window = int(float(policies[i].split("w=", 1)[1]))
        except (ValueError, TypeError):
            window = None
        lim_n = _num(limits, i)
        rem_n = _num(remaining, i)
        capped = bool(lim_n and lim_n > 0)
        entry: dict[str, Any] = {
            "window_s": window,
            "limit": lim_n,
            "remaining": rem_n,
            "reset_s": _num(resets, i),
            # See the docstring: 0 means "no cap advertised", never "exhausted".
            "capped": capped,
        }
        if capped and rem_n is not None:
            entry["utilisation_pct"] = round((lim_n - rem_n) / lim_n * 100, 2)
        out.append(entry)
    return out


async def _record_plan_limits(headers: Any) -> None:
    """Persist what the provider says about our plan, and alert on a REAL cap."""
    windows = parse_rate_limit_headers(headers)
    if not windows:
        return
    try:
        await rs.set_json(_PLAN_KEY, {"windows": windows, "at": time.time()})
    except Exception:
        logger.debug("[R-F3870] could not persist brave plan limits", exc_info=True)
    for w in windows:
        # Only a window the provider actually caps can be near exhaustion, and only
        # a LONG window is worth alerting on — a 1-second bucket at 90% is normal
        # pacing, not a problem anyone can act on.
        if not w.get("capped") or (w.get("window_s") or 0) < 3600:
            continue
        lim, rem = w.get("limit") or 0, w.get("remaining")
        if rem is None or lim <= 0 or (rem / lim) > (1 - _WARN_AT):
            continue
        logger.warning("[R-F3870] Brave plan window %ss: %s/%s remaining",
                       w.get("window_s"), rem, lim)
        try:
            from .engine_wiring import wire_failure
            wire_failure(
                module="brave_usage",
                detail=(f"Brave plan window {w.get('window_s')}s is at "
                        f"{w.get('utilisation_pct')}% ({rem}/{lim} remaining) — DD's "
                        f"designated search engine is approaching its ceiling. "
                        f"Operator action required BEFORE exhaustion."),
                gap_type="search_backend_failure",
                source="brave_usage:_record_plan_limits",
            )
        except Exception:      # pragma: no cover
            pass


@fail_wire(module="brave_usage", gap_type="engine_failure")
async def record_call(outcome: str, *, status: int | None = None,
                      body: str = "", headers: Any = None) -> None:
    """Count one Brave call. Best-effort: never raises into the search path."""
    outcome = outcome if outcome in OUTCOMES else "http_error"
    month, day = _month(), _day()
    for prefix, key, ttl in ((_MONTH_PREFIX, month, _MONTH_TTL),
                             (_DAY_PREFIX, day, _DAY_TTL)):
        try:
            total = await rs.incr(f"{prefix}{key}:total")
            await rs.incr(f"{prefix}{key}:{outcome}")
            if total == 1:                      # stamp the TTL on first write only
                await rs.expire(f"{prefix}{key}:total", ttl)
        except Exception:
            logger.debug("[R-F3868] brave usage counter write failed", exc_info=True)

    # R-F3870 — Brave publishes its own limits on EVERY response. Read them rather
    # than relying on an operator-supplied number that goes stale.
    if headers is not None:
        await _record_plan_limits(headers)

    # R-F3884 — AND put the SPEND on the operator's cost surface, not only the call
    # count on this module's own one. `cost_tracker.record_brave_call` was built for
    # exactly this, with a documented price and a BRAVE_COST_PER_CALL_USD override,
    # and had ZERO callers — so `/api/aria/cost/external` still reported
    # `by_service: {}, total_calls: 0`, which is verbatim the symptom C-23 cites as
    # the proof Brave was unmetered. R-F3868 counted calls here and left that
    # endpoint untouched: the fix was verified against a different surface than the
    # one whose emptiness defined the defect.
    await _record_spend(outcome, status)

    # §21a — BOTH branches must reach a brain sink. This module had wire_failure on
    # every error path and NOTHING on the success path, so the brain could see Brave
    # breaking but never see it working — and "no failure signal" is not evidence of
    # health (§1, §22). Caught by the pre-commit wiring check the moment R-F3886
    # revived it, having been dark in this module since R-F3868.
    # Routed through wire_success, i.e. the lightweight metric path (R-F1664), NOT
    # absorb: this is per-call telemetry and the heavy tier is what wedged the
    # absorb pipeline once already.
    if outcome == "ok":
        try:
            from .engine_wiring import wire_success
            wire_success(module="brave_usage",
                         summary="brave search call served",
                         source_id="brave_usage:R-F3884")
        except Exception:      # pragma: no cover - telemetry never blocks search
            pass

    if status == 429:
        await _note_exhaustion(classify_429(body, headers), body)
    else:
        await _maybe_warn_headroom()


async def _record_spend(outcome: str, status: int | None) -> None:
    """Book this call against the external cost surface — and the §17 cap.

    THE CAP INTERACTION IS DELIBERATE. `_flush_external_pending` writes the composite
    `COST_MONTH_PREFIX{month}` rollup that `assert_monthly_cap` reads, so Brave spend
    counts toward the monthly ceiling. That is correct — Brave is real money and the
    operator watches that number daily (§17) — but it is a behavioural change, so it
    was checked against live headroom before shipping (~$48 of $600 month-to-date;
    Brave at $0.005/call adds tens of dollars a month, not hundreds).

    NEVER BILL FOR A CALL BRAVE DID NOT ANSWER. A timeout produced no HTTP response,
    so no query was served; it is recorded as an attempt at cost 0.0 rather than
    hidden or charged. Same rule that keeps `monthly_quota()` returning None instead
    of a comforting zero.

    The PRICE is not decided here. It comes from cost_tracker's documented default
    ($5/1,000, the conservative Pro-plan ceiling) or the operator's
    BRAVE_COST_PER_CALL_USD — a second hardcoded rate in this module would be a
    number nobody could reconcile with the first.
    """
    try:
        from . import cost_tracker as _ct
        await _ct.record_brave_call(
            operation="search",
            # R-F4083 (C-131) — `empty` is an ANSWER, not an error. Brave
            # returned HTTP 200 and found nothing, which for an obscure DD
            # subject is frequently the correct result.
            #
            # This read `success=(outcome == "ok")`, so every empty result
            # incremented `errors` on /cost/external. Live 2026-08-16: 234 calls,
            # 135 ok, 99 empty and ZERO rate_limited/auth_failed/http_error/
            # timeout — yet the cost surface reported "71 errors", and R-F4064
            # then rendered that as a red "Fail rate 42%". A search engine that
            # answers "no results" was being reported as broken.
            #
            # Caught reviewing my own R-F4064: it is the same defect this whole
            # batch is about — a state that is not a failure, rendered as one —
            # committed while fixing it. The empty RATE is still a real signal
            # about search quality and still measured, on
            # /search/health.brave_usage.monthly, where it is named `empty`
            # rather than dressed as an error.
            success=(outcome in ("ok", "empty")),
            # R-F4094 (C-138) — hand over the EVIDENCE too. `success` alone made
            # the C-131 correction above unreachable for every row already
            # written; a day after it shipped the panel still read "42% fail"
            # against a ledger showing zero failures. The outcome label lets the
            # rate be DERIVED at read time, so a future reclassification applies
            # to history instead of only to the future.
            outcome=outcome,
            # status is None only when no response arrived (timeout/transport error).
            cost_per_call_usd=None if status is not None else 0.0,
        )
    except Exception:      # pragma: no cover - cost bookkeeping never blocks search
        logger.debug("[R-F3884] brave cost recording unavailable", exc_info=True)


async def _note_exhaustion(kind: str, body: str) -> None:
    """Record and ANNOUNCE a 429. The body text is kept because §18 established it
    is the only signal that distinguishes the two causes."""
    state = {
        "last_429_at": time.time(),
        "classification": kind,
        # Truncated, but kept verbatim — a classification nobody can audit is a
        # guess wearing a verdict's clothes.
        "body": (body or "")[:400],
        "month": _month(),
    }
    try:
        await rs.set_json(_STATE_KEY, state)
    except Exception:
        logger.debug("[R-F3868] could not persist brave 429 state", exc_info=True)

    logger.warning("[R-F3868] Brave 429 classified as %s — body=%r", kind, (body or "")[:160])
    # §21a — DD's search engine refusing service must reach the brain, not just a
    # log line nobody reads. This is the alert that did not exist for OpenSanctions.
    try:
        from .engine_wiring import wire_failure
        wire_failure(
            module="brave_usage",
            detail=(f"Brave returned 429 classified as {kind}. DD's designated search "
                    f"engine is refusing service; retrying cannot clear a spent plan. "
                    f"body={(body or '')[:200]!r}"),
            gap_type="search_backend_failure",
            source="brave_usage:_note_exhaustion",
        )
    except Exception:      # pragma: no cover
        pass


async def _maybe_warn_headroom() -> None:
    """Warn BEFORE exhaustion, but only when a real quota is configured."""
    quota = monthly_quota()
    if not quota:
        return                                  # no denominator -> no percentage
    try:
        used = int(await rs.get(f"{_MONTH_PREFIX}{_month()}:total") or 0)
    except Exception:
        return
    if used < int(quota * _WARN_AT):
        return
    marker = f"{_MONTH_PREFIX}{_month()}:warned"
    try:
        if await rs.get(marker):
            return                              # once per month, not per call
        await rs.set(marker, "1", ex=_MONTH_TTL)
    except Exception:
        return
    logger.warning("[R-F3868] Brave usage at %d/%d for %s", used, quota, _month())
    try:
        from .engine_wiring import wire_failure
        wire_failure(
            module="brave_usage",
            detail=(f"Brave monthly usage {used}/{quota} ({used / quota:.0%}) for "
                    f"{_month()} — DD's designated search engine is approaching its "
                    f"plan ceiling. Operator action required BEFORE exhaustion."),
            gap_type="search_backend_failure",
            source="brave_usage:_maybe_warn_headroom",
        )
    except Exception:      # pragma: no cover
        pass


@fail_wire(module="brave_usage", gap_type="engine_failure")
async def usage_report() -> dict[str, Any]:
    """What ARIA knows about her paid search engine's consumption (§25.3).

    R-F3874 — THIS REPORT USED TO RENDER ITS OWN BLINDNESS AS A CLEAN BILL OF HEALTH.

    It read through the non-strict `get`/`get_json`, whose documented contract
    returns `None` on a STORE FAILURE exactly as it does for a genuinely absent key
    (R-F1, redis_store.py:299-303). A wedged store therefore produced
    `monthly: {}, plan_limits: null` — "Brave has never been called and advertises no
    limits" — which is indistinguishable from a healthy, quiet key.

    That is §17's fabricated-P0 shape (`spent_usd: 0.0` from a probe with no store
    connection, which nearly became a P0 against a meter reading $48.26) reproduced
    INSIDE the module written to prevent that class, and it is the same absence-
    reads-as-health defect as the three Phase A gates in §1. A meter that cannot say
    "I could not measure" is not a meter.

    So the strict readers are used and a failure is DECLARED. `store_readable: False`
    with `monthly: None` is an honest unknown; `store_readable: True` with
    `monthly: {}` is a real, measured zero. They must never look alike again.
    """
    month, day = _month(), _day()
    out: dict[str, Any] = {"month": month, "day": day}
    readable = True

    for label, prefix, key in (("monthly", _MONTH_PREFIX, month),
                               ("daily", _DAY_PREFIX, day)):
        counts: dict[str, int] = {}
        failed = False
        for field in ("total",) + OUTCOMES:
            try:
                v = await rs.get_strict(f"{prefix}{key}:{field}")
            except Exception:
                # Store-layer failure: this count is UNKNOWN, not zero.
                failed = True
                break
            if v:
                try:
                    counts[field] = int(v)
                except (TypeError, ValueError):
                    pass
        if failed:
            readable = False
            out[label] = None
        else:
            out[label] = counts

    out["store_readable"] = readable

    # R-F3870 — the provider's own accounting, which beats any number we guess.
    # R-F3874 — with an explicit state, because "no reading" and "an unreadable
    # store" and "a reading from six weeks ago" are three different situations and
    # only one of them is headroom you can act on.
    plan: Any = None
    try:
        plan = await rs.get_json_strict(_PLAN_KEY)
        if not isinstance(plan, dict) or not plan.get("windows"):
            plan, state = None, "never_observed"
        else:
            age = max(0.0, time.time() - float(plan.get("at") or 0))
            plan = dict(plan)
            plan["age_s"] = round(age, 1)
            state = "stale" if age > _PLAN_STALE_S else "fresh"
    except Exception:
        readable, plan, state = False, None, "unreadable"
        out["store_readable"] = False
    out["plan_limits"] = plan
    out["plan_limits_state"] = state

    try:
        out["last_429"] = await rs.get_json_strict(_STATE_KEY)
    except Exception:
        out["last_429"] = None
        out["store_readable"] = False

    quota = monthly_quota()
    used = int((out.get("monthly") or {}).get("total") or 0)
    out["quota"] = quota
    if quota:
        out["remaining"] = max(0, quota - used)
        out["utilisation_pct"] = round(used / quota * 100, 2)
    else:
        # Never a number nobody verified. See the module docstring.
        out["remaining"] = None
        out["utilisation_pct"] = None
        # R-F3874 — and do NOT ask the operator for a ceiling the provider has
        # already published. R-F3870's lesson is "measuring beats asking"; a
        # fabricated task is how a real one gets ignored.
        if not _publishes_a_real_ceiling(plan, state):
            out["quota_hint"] = (
                "set BRAVE_MONTHLY_QUOTA to enable headroom + pre-exhaustion alerts")
    return out


def _publishes_a_real_ceiling(plan: Any, state: str) -> bool:
    """True when the provider itself gave us a usable cap on a long window.

    `capped` is load-bearing: R-F3870 measured `limit 0` on the 31-day window of an
    HTTP 200 WITH RESULTS, which means "no cap advertised", never "exhausted".
    A stale reading does not count — a plan can be downgraded between observations.
    """
    if state != "fresh" or not isinstance(plan, dict):
        return False
    return any(w.get("capped") and (w.get("window_s") or 0) >= 3600
               for w in (plan.get("windows") or []) if isinstance(w, dict))
