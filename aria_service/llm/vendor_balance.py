"""R-F4229 / C-209 — read the LLM vendor's OWN published prepaid balance.

WHY THIS MODULE EXISTS, AND WHY `cost_tracker` COULD NOT DO IT.

`intel/cost_tracker.py` measures MODELLED spend: tokens counted off responses,
multiplied by a hardcoded price table, checked against an operator-set monthly
cap. That is our accounting of our own usage. A vendor's PREPAID BALANCE is a
different quantity in a different system, and the two are not merely different
scales — they can disagree without either being wrong. `cost_tracker.py:148`
records them diverging ~25x once already (deepseek-v4-flash falling through to
Claude pricing).

Live 2026-08-21 -> 2026-08-22 that gap became a total outage. DeepSeek returned
`HTTP 402 Insufficient Balance`, R-F678's 24h HARD billing cooldown armed, and
because `general_vendor_depth` is 1 (§17: deepseek_backup was removed) with
Anthropic confined to DD by RULE ONE, general chat and WhatsApp went dark. The
whole time `/api/aria/cost/monthly/status` read `spent_usd 107.35` of
`cap_usd 600.0` — 17.9% used, $492.65 "remaining". Every instrument we had said
healthy. Nothing in the tree could warn before zero, because nothing in the tree
had ever asked the vendor.

THE VENDOR PUBLISHES IT. Measured from inside aria-intel, same key, HTTP 200:

    GET https://api.deepseek.com/user/balance
    {"is_available": false,
     "balance_infos": [{"currency": "USD", "total_balance": "-0.02",
                        "granted_balance": "0.00", "topped_up_balance": "-0.02"}]}

This is the §27f rule applied to the LLM chain: before declaring a dependency
blocked or escalating to the operator, read what the provider already publishes.
R-F3868/R-F3870 learned it for Brave — "an unmeasured dependency reads exactly
like a healthy one, right up to the 429".

THREE HONESTY RULES, each load-bearing and each pinned by a test:

  1. **UNREADABLE IS NEVER EXHAUSTED.** A DNS failure, a timeout or an HTTP 500
     means COULD NOT MEASURE. Rendering that as "no credit" would arm an outage
     response against a funded account; rendering it as "fine" would hide a real
     one. `available` is tri-state and `None` is a first-class answer — the same
     rule §1 records three Phase A gates being certified by an absence for
     breaking, and §27f's `plan_limits_state`.
  2. **AN UNSUPPORTED VENDOR IS DECLARED, NEVER INVENTED.** Anthropic publishes
     no balance endpoint on the Messages API, so `anthropic` reads
     `unsupported` and no request is made. A gauge that guesses is worse than
     one that abstains.
  3. **THE GAUGE IS NOT THE SUBJECT.** A gauge fault and a vendor refusal are
     different faults with opposite remedies (a code/network fix vs the
     operator's wallet), so they are wired to the brain under different modules
     and must never be merged — the R-F3693 `inconclusive` vs
     `still_locked_out` distinction, applied here.

NO BREAKER, DELIBERATELY. A circuit breaker on this call would, after a few
failures, pin the gauge at `unreadable` for its whole cooldown — i.e. the
instrument would go dark exactly when the network is unhealthy, which is the
failure class this module exists to remove. Throttling is by poll interval
(`ARIA_LLM_BALANCE_POLL_INTERVAL_S`, default 900s), not by breaker.
"""

from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass

logger = logging.getLogger("aria.llm.vendor_balance")

# ── States: how well do we know the balance? ────────────────────────────────
STATE_FRESH = "fresh"                    # measured, from the vendor's own body
STATE_UNREADABLE = "unreadable"          # COULD NOT MEASURE — never "empty"
STATE_UNSUPPORTED = "unsupported"        # this vendor publishes no balance
STATE_NEVER_OBSERVED = "never_observed"  # supported, not yet polled

# ── Severities: what should a human do about it? ────────────────────────────
SEVERITY_OK = "ok"
SEVERITY_LOW = "low"                     # still serving — but not for long
SEVERITY_EXHAUSTED = "exhausted"         # the vendor is refusing, measured
SEVERITY_UNKNOWN = "unknown"             # unreadable or unsupported

# Vendor identity is the FIRST underscore-segment of the provider name, matching
# `general_vendor_depth` in fallback.get_health() (R-F3634): `deepseek` and
# `deepseek_backup` are two chain entries built from ONE key on ONE account, so
# they share one balance. Keying on the full provider name would poll the same
# account twice and report it as two independent gauges.
_BALANCE_ENDPOINTS: dict[str, str] = {
    "deepseek": "https://api.deepseek.com/user/balance",
}

_DEFAULT_TIMEOUT_S = 10.0


def vendor_of(provider_name: str) -> str:
    return (provider_name or "").strip().lower().split("_")[0]


def supports(provider_name: str) -> bool:
    """True iff this vendor publishes a balance we can read."""
    return vendor_of(provider_name) in _BALANCE_ENDPOINTS


def _warn_threshold_usd() -> float:
    """Balance at or below which we warn while the vendor is still serving.

    A function, not a constant, so the threshold is read at call time — the
    §17 lesson that an env-var lever captured at import is not a lever.
    """
    try:
        return float(os.getenv("ARIA_LLM_BALANCE_WARN_USD", "10") or 10)
    except (TypeError, ValueError):
        return 10.0


def _timeout_s() -> float:
    try:
        return float(os.getenv("ARIA_LLM_BALANCE_TIMEOUT_S", "") or _DEFAULT_TIMEOUT_S)
    except (TypeError, ValueError):
        return _DEFAULT_TIMEOUT_S


@dataclass(frozen=True)
class BalanceReading:
    provider: str
    vendor: str
    state: str
    available: bool | None
    total_balance: float | None
    currency: str | None
    observed_at: float | None
    detail: str = ""

    @property
    def is_exhausted(self) -> bool:
        """ONLY true on a MEASURED refusal.

        Not a convenience property — it is the guard that stops an unreadable
        gauge from being treated as an empty account anywhere downstream.
        """
        return self.state == STATE_FRESH and self.available is False

    def describe(self) -> str:
        if self.state != STATE_FRESH:
            return f"{self.state}({self.detail})" if self.detail else self.state
        amount = ("unparsed" if self.total_balance is None
                  else f"{self.total_balance:.2f}")
        return (f"{amount} {self.currency or ''}".strip()
                + f", vendor says available={self.available}")

    def to_dict(self) -> dict:
        return {
            "state": self.state,
            "available": self.available,
            "total_balance": self.total_balance,
            "currency": self.currency,
            "observed_at": self.observed_at,
            "age_s": (None if self.observed_at is None
                      else round(time.time() - self.observed_at, 1)),
            "severity": severity(self),
            "warn_threshold_usd": _warn_threshold_usd(),
            "detail": self.detail,
        }


def severity(reading: BalanceReading) -> str:
    """Map a reading to what a human should do about it.

    `unknown` is deliberately NOT collapsed into `ok`: "I could not ask" must
    never render as "there is money". That collapse is the exact shape of the
    fabricated Phase A gates in §1.
    """
    if reading.state != STATE_FRESH:
        return SEVERITY_UNKNOWN
    if reading.available is False:
        return SEVERITY_EXHAUSTED
    if reading.total_balance is not None and reading.total_balance <= _warn_threshold_usd():
        return SEVERITY_LOW
    if reading.available is True:
        return SEVERITY_OK
    return SEVERITY_UNKNOWN


def unknown(provider_name: str, state: str, detail: str = "") -> BalanceReading:
    return BalanceReading(
        provider=provider_name, vendor=vendor_of(provider_name), state=state,
        available=None, total_balance=None, currency=None,
        observed_at=None, detail=detail,
    )


async def _fetch(url: str, api_key: str, timeout: float) -> tuple[int, dict]:
    """The one outbound call. Module-level so tests can replace it wholesale."""
    import httpx
    headers = {"Authorization": f"Bearer {api_key}", "Accept": "application/json"}
    async with httpx.AsyncClient(timeout=timeout) as client:  # no-breaker: a breaker would pin this gauge at `unreadable` for its cooldown, i.e. blind the instrument exactly when the network is sick — the failure class R-F4229 exists to remove. Throttled by poll interval instead.
        resp = await client.get(url, headers=headers)
        try:
            body = resp.json()
        except Exception:
            body = {}
        return resp.status_code, (body if isinstance(body, dict) else {})


def _parse(provider_name: str, body: dict) -> BalanceReading:
    """Parse the vendor body. An unexpected shape is UNREADABLE, not empty."""
    if "is_available" not in body:
        return unknown(provider_name, STATE_UNREADABLE,
                       "response carried no is_available field")
    available = bool(body.get("is_available"))
    infos = body.get("balance_infos") or []
    chosen: dict = {}
    if isinstance(infos, list) and infos:
        chosen = next(
            (i for i in infos
             if isinstance(i, dict) and str(i.get("currency", "")).upper() == "USD"),
            infos[0] if isinstance(infos[0], dict) else {},
        )
    total: float | None
    try:
        raw = chosen.get("total_balance")
        total = None if raw is None else float(raw)
    except (TypeError, ValueError):
        # Availability is still known — the vendor told us. Only the NUMBER is
        # missing, so report the half we measured rather than discarding both.
        total = None
    return BalanceReading(
        provider=provider_name, vendor=vendor_of(provider_name),
        state=STATE_FRESH, available=available, total_balance=total,
        currency=(str(chosen.get("currency")) if chosen.get("currency") else None),
        observed_at=time.time(),
    )


def note_transition(provider_name: str, reading: BalanceReading,
                    previous_severity: str | None) -> str:
    """§21a — reach the brain on a CHANGE of severity. Returns the new one.

    THIS LIVES HERE, NOT IN THE CALLER, for two reasons. The repo-wide wiring
    audit (`scripts/ci/wiring_audit.py`) scans PER MODULE and flagged this file
    `no-wiring` when the signals were emitted from `fallback.py` — correctly:
    §21b says no module ships dark, and a module whose entire job is
    observability being itself unobservable is the sharpest version of that.
    And the module that knows what a balance MEANS is the right one to own what
    is said about it.

    Three properties are load-bearing:

      * TRANSITION, not poll. A low balance read every 900s would emit 96
        identical signals a day; this repo has twice had a 500-slot capability
        ledger filled by exactly that shape.
      * A RECOVERY is wired as a SUCCESS. §25a: a limb she cannot feel coming
        back is not hers, and a top-up is the rarest, most operator-relevant
        state change this gauge can observe.
      * A GAUGE FAULT IS NOT A VENDOR FAULT. `unreadable` wires under a
        different module because the remedies are opposite — one needs a code or
        network fix, the other needs the operator's wallet. This is the R-F3693
        `inconclusive` vs `still_locked_out` distinction; merged, an unreachable
        endpoint would page the operator to top up a funded account.

    Never raises: an observability bug must not break the thing it observes.
    """
    sev = severity(reading)
    try:
        if sev == previous_severity:
            return sev
        from ..intel.engine_wiring import wire_success as _ws, wire_failure as _wf

        if sev == SEVERITY_UNKNOWN:
            if reading.state == STATE_UNSUPPORTED:
                return sev  # a permanent, knowable fact — not an event
            _wf(
                module="llm_vendor_balance_gauge",
                detail=(f"cannot read {provider_name} vendor balance: "
                        f"{reading.describe()}. The headroom gauge is dark — "
                        f"this says NOTHING about whether the account has credit."),
                gap_type="engine_failure",
                source=f"llm_vendor_balance_gauge:unreadable:{provider_name}",
            )
        elif sev == SEVERITY_OK:
            if previous_severity in (SEVERITY_LOW, SEVERITY_EXHAUSTED):
                _ws(
                    module="llm_vendor_balance",
                    summary=f"Vendor balance restored: {provider_name}",
                    detail=(f"{provider_name} prepaid balance is back above the "
                            f"warning threshold: {reading.describe()}")[:600],
                    source_id=f"llm_vendor_balance:restored:{provider_name}",
                )
        else:
            _wf(
                module="llm_vendor_balance",
                detail=(
                    f"{provider_name} prepaid vendor balance {sev}: "
                    f"{reading.describe()}. OPERATOR ACTION: top up the "
                    f"{reading.vendor} account. This is NOT the monthly cap — "
                    f"the cost meter measures our modelled spend and cannot see "
                    f"vendor credit."
                )[:600],
                gap_type="llm_provider_failure",
                source=f"llm_vendor_balance:{sev}:{provider_name}",
            )
    except Exception:
        logger.debug("[R-F4229] balance-transition wiring failed", exc_info=True)
    return sev


async def read_balance(provider_name: str, api_key: str,
                       *, timeout: float | None = None) -> BalanceReading:
    """Ask the vendor what our prepaid balance is. Never raises."""
    vendor = vendor_of(provider_name)
    url = _BALANCE_ENDPOINTS.get(vendor)
    if not url:
        return unknown(provider_name, STATE_UNSUPPORTED,
                       f"{vendor} publishes no balance endpoint")
    if not api_key:
        # Not "no money" — no way to ask. §22: state UNKNOWN, never infer.
        return unknown(provider_name, STATE_UNREADABLE, "no api key available")
    try:
        status, body = await _fetch(url, api_key,
                                    _timeout_s() if timeout is None else timeout)
    except Exception as exc:
        return unknown(provider_name, STATE_UNREADABLE,
                       f"{type(exc).__name__}: {str(exc)[:160]}")
    if status != 200:
        return unknown(provider_name, STATE_UNREADABLE, f"HTTP {status}")
    return _parse(provider_name, body)
