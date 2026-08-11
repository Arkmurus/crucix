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

    if status == 429:
        await _note_exhaustion(classify_429(body, headers), body)
    else:
        await _maybe_warn_headroom()


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
    """What ARIA knows about her paid search engine's consumption (§25.3)."""
    month, day = _month(), _day()
    out: dict[str, Any] = {"month": month, "day": day}
    for label, prefix, key in (("monthly", _MONTH_PREFIX, month),
                               ("daily", _DAY_PREFIX, day)):
        counts: dict[str, int] = {}
        for field in ("total",) + OUTCOMES:
            try:
                v = await rs.get(f"{prefix}{key}:{field}")
            except Exception:
                v = None
            if v:
                counts[field] = int(v)
        out[label] = counts
    quota = monthly_quota()
    used = int(out.get("monthly", {}).get("total") or 0)
    out["quota"] = quota
    if quota:
        out["remaining"] = max(0, quota - used)
        out["utilisation_pct"] = round(used / quota * 100, 2)
    else:
        # Never a number nobody verified. See the module docstring.
        out["remaining"] = None
        out["utilisation_pct"] = None
        out["quota_hint"] = "set BRAVE_MONTHLY_QUOTA to enable headroom + pre-exhaustion alerts"
    try:
        out["last_429"] = await rs.get_json(_STATE_KEY)
    except Exception:
        out["last_429"] = None
    # R-F3870 — the provider's own accounting, which beats any number we guess.
    try:
        out["plan_limits"] = await rs.get_json(_PLAN_KEY)
    except Exception:
        out["plan_limits"] = None
    return out
