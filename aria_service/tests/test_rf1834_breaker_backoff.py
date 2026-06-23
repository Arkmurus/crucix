"""R-F1834 — exponential-backoff circuit breaker.

Structural replacement for the recurring hand-tuned cooldown band-aid (§1).
Instead of bumping a fixed cooldown per backend (archive.is 900→3600, proposed
3600→86400), the cooldown grows exponentially each time the half-open probe
fails again and resets to base on the first success. These tests drive the real
CircuitBreaker, asserting the user-visible behaviour: a dead backend backs off
to the cap, a recovered one snaps back to base, and unrecoverable auth/billing
failures jump to the long floor.
"""
from __future__ import annotations

import time

import pytest

from aria_service.intel import circuit_breaker as cb_mod
from aria_service.intel.circuit_breaker import CircuitBreaker, classify_status


@pytest.fixture(autouse=True)
def _fresh():
    cb_mod._breakers.clear()
    yield
    cb_mod._breakers.clear()


def _trip(cb: CircuitBreaker, reason: str = "timeout"):
    """Drive consecutive failures until the breaker first trips OPEN."""
    for _ in range(cb.failure_threshold):
        cb.record_failure(reason=reason)


def test_first_trip_uses_base_cooldown():
    cb = CircuitBreaker(name="b", failure_threshold=3, cooldown_seconds=100)
    _trip(cb)
    assert cb.state == "OPEN"
    assert cb.open_cycles == 1
    assert cb._effective_cooldown() == 100  # base × 2^0


def test_repeated_probe_failures_back_off_exponentially():
    cb = CircuitBreaker(name="b", failure_threshold=1, cooldown_seconds=100, max_cooldown_seconds=10_000)
    cb.record_failure(reason="timeout")          # CLOSED→OPEN, cycle 1
    assert cb._effective_cooldown() == 100
    # Simulate cooldown expiry → HALF_OPEN → probe fails again → deeper backoff.
    for expected in (200, 400, 800):
        cb.last_failure_at = time.time() - cb._effective_cooldown() - 1
        assert cb.is_open() is False             # transitions to HALF_OPEN (probe)
        assert cb.state == "HALF_OPEN"
        cb.record_failure(reason="timeout")      # probe failed
        assert cb.state == "OPEN"
        assert cb._effective_cooldown() == expected


def test_backoff_capped_at_max():
    cb = CircuitBreaker(name="b", failure_threshold=1, cooldown_seconds=1000, max_cooldown_seconds=4000)
    cb.record_failure(reason="timeout")
    # Drive many re-trips; effective cooldown must never exceed the cap.
    for _ in range(20):
        cb.open_cycles += 1
        assert cb._effective_cooldown() <= 4000
    assert cb._effective_cooldown() == 4000


def test_success_resets_backoff_to_base():
    cb = CircuitBreaker(name="b", failure_threshold=1, cooldown_seconds=100, max_cooldown_seconds=10_000)
    cb.record_failure(reason="timeout")
    cb.open_cycles = 5                            # pretend it has backed off a lot
    assert cb._effective_cooldown() > 100
    cb.record_success()
    assert cb.state == "CLOSED"
    assert cb.open_cycles == 0
    # A fresh trip starts again from base — no lingering penalty (cures the flap).
    cb.record_failure(reason="timeout")
    assert cb._effective_cooldown() == 100


def test_unrecoverable_auth_jumps_to_floor():
    cb = CircuitBreaker(name="b", failure_threshold=1, cooldown_seconds=100)
    cb.record_failure(reason="auth")
    assert cb._effective_cooldown() == cb_mod._UNRECOVERABLE_FLOOR_SECONDS


def test_unrecoverable_billing_jumps_to_floor():
    cb = CircuitBreaker(name="b", failure_threshold=1, cooldown_seconds=100)
    cb.record_failure(reason="billing")
    assert cb._effective_cooldown() == cb_mod._UNRECOVERABLE_FLOOR_SECONDS


def test_classify_status_mapping():
    assert classify_status(429) == "rate_limit"
    assert classify_status(402) == "billing"
    assert classify_status(401) == "auth"
    assert classify_status(403) == "auth"
    assert classify_status(503) == "server"
    assert classify_status(200) == "other"


def test_to_dict_exposes_backoff_state():
    cb = CircuitBreaker(name="b", failure_threshold=1, cooldown_seconds=100)
    cb.record_failure(reason="rate_limit")
    d = cb.to_dict()
    assert d["open_cycles"] == 1
    assert d["effective_cooldown_seconds"] == 100
    assert d["max_cooldown_seconds"] == cb_mod._DEFAULT_MAX_COOLDOWN_SECONDS
    assert d["last_failure_reason"] == "rate_limit"


def test_archive_is_records_rate_limit_reason():
    """The archive.is fallback must now classify its 429 as rate_limit so the
    backoff escalates (was record_failure() with no reason → mislabelled timeout)."""
    import asyncio
    from unittest.mock import patch
    from aria_service.intel import researcher

    class FakeResp:
        status_code = 429
        text = "rate limited"
    class FakeClient:
        async def __aenter__(self): return self
        async def __aexit__(self, *a): pass
        async def get(self, *a, **k): return FakeResp()

    with patch("aria_service.intel.researcher.httpx.AsyncClient", return_value=FakeClient()):
        asyncio.run(researcher._try_archive_fallbacks("https://example.com/blocked"))

    cb = cb_mod._breakers.get("archive_is")
    assert cb is not None
    assert cb.last_failure_reason == "rate_limit"
