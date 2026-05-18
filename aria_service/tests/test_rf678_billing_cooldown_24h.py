"""R-F678 (2026-05-18) — billing failures get 24h cooldown, auth stays 30min.

Live fly logs 2026-05-18 07:13:35 — three back-to-back POSTs to
api.anthropic.com all returned HTTP 400 "credit balance too low" within
25ms. The breaker set a 1800s cooldown per the F29-era policy, but
two more in-flight POSTs burned through anyway. With operator having
explicitly decided NOT to top up Anthropic at this stage, a 30-min
re-probe wastes the same 3 calls every 30 minutes — ~96 wasted
calls/day on a provider that has no credit.

R-F678 splits the HARD cooldown by kind:
  - "billing"        → 24h (provider won't auto-resolve; operator
                       hasn't committed to top-up)
  - "auth" / other   → 30min (key rotation is fast)

The 3-way race window in `complete()` (check-cooldown → HTTP →
record-failure) is not closed — closing it would serialise normal
traffic. Instead the cooldown extension reduces wasted calls per
race window from ~96/day to ≤6/day worst-case.
"""
from __future__ import annotations

import logging
import time

import pytest


def _build_chain(provider_name: str = "anthropic"):
    from aria_service.llm.fallback import FallbackProvider
    chain = FallbackProvider.__new__(FallbackProvider)
    chain._stats = {provider_name: {"calls": 0, "failures": 0, "last_failure": 0,
                                     "cooldown_until": 0, "last_kind": ""}}
    return chain


class _FakeProvider:
    name = "anthropic"


def _make_error(kind: str):
    from aria_service.llm.provider import ProviderError
    return ProviderError(
        "anthropic", f"{kind} synthesised for test",
        kind=kind, retryable=False,
    )


def test_rf678_billing_cooldown_is_24h():
    chain = _build_chain()
    t0 = time.time()
    chain._record_failure(_FakeProvider(), chain._stats["anthropic"],
                          _make_error("billing"))
    cooldown = chain._stats["anthropic"]["cooldown_until"]
    # Should be roughly now + 86400 (24h). Allow 5s slack for clock drift.
    expected = t0 + 86400
    assert abs(cooldown - expected) < 5, (
        f"R-F678: billing cooldown should be ~24h ({expected}), got {cooldown} "
        f"(delta {cooldown - t0:.0f}s)"
    )


def test_rf678_auth_cooldown_stays_30min():
    """Auth failures must keep the 30-min cooldown — operator may
    rotate a key in minutes; we don't want to lock out for a day."""
    chain = _build_chain()
    t0 = time.time()
    chain._record_failure(_FakeProvider(), chain._stats["anthropic"],
                          _make_error("auth"))
    cooldown = chain._stats["anthropic"]["cooldown_until"]
    expected = t0 + 1800
    assert abs(cooldown - expected) < 5, (
        f"R-F678: auth cooldown should stay 30min ({expected}), got {cooldown} "
        f"(delta {cooldown - t0:.0f}s)"
    )


def test_rf678_hard_cooldown_helper_classmethod():
    """The kind→duration mapper is a classmethod so it works on
    instances and on the class object directly."""
    from aria_service.llm.fallback import FallbackProvider
    assert FallbackProvider._hard_cooldown_for_kind("billing") == 86400
    assert FallbackProvider._hard_cooldown_for_kind("auth") == 1800
    # Unknown kinds fall back to the standard hard cooldown — defensive.
    assert FallbackProvider._hard_cooldown_for_kind("other") == 1800
    assert FallbackProvider._hard_cooldown_for_kind("") == 1800


def test_rf678_burst_debounce_still_works_with_24h(caplog):
    """The F29 burst-debounce check (suppress N peers from re-logging
    within 5s) must still work with the longer billing cooldown.
    Otherwise the live race would emit 3 ERROR lines per Anthropic 400
    burst rather than 1."""
    chain = _build_chain()

    with caplog.at_level(logging.DEBUG, logger="aria.llm.fallback"):
        for _ in range(5):
            chain._record_failure(_FakeProvider(), chain._stats["anthropic"],
                                  _make_error("billing"))

    error_lines = [r for r in caplog.records
                   if "HARD cooldown" in r.message and r.levelname == "ERROR"]
    debug_lines = [r for r in caplog.records if "burst peer" in r.message]
    assert len(error_lines) == 1, (
        f"R-F678: burst-debounce broken with 24h cooldown — got "
        f"{len(error_lines)} ERROR lines"
    )
    assert len(debug_lines) == 4, (
        f"R-F678: expected 4 DEBUG burst-peer lines, got {len(debug_lines)}"
    )


def test_rf678_billing_error_message_includes_24h_duration(caplog):
    """The operator scanning fly logs should see the 86400s number in
    the ERROR line, not the misleading 1800s number from the old code."""
    chain = _build_chain()

    with caplog.at_level(logging.ERROR, logger="aria.llm.fallback"):
        chain._record_failure(_FakeProvider(), chain._stats["anthropic"],
                              _make_error("billing"))

    error_records = [r for r in caplog.records
                     if "HARD cooldown" in r.message and r.levelname == "ERROR"]
    assert len(error_records) == 1
    assert "86400" in error_records[0].message, (
        f"R-F678: ERROR line should show 86400s duration, got: "
        f"{error_records[0].message}"
    )
