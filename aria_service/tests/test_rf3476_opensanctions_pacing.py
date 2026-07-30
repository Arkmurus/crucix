"""R-F3476 — OpenSanctions earned its own 429s, then blamed a missing key.

Observed live 2026-07-30, repeatedly, in a single log window:

    OpenSanctions rate-limited (free tier: 1 req/sec). Set OPENSANCTIONS_API_KEY
      for unlimited access.
    [circuit_breaker] opensanctions.org: CLOSED -> OPEN (3 consecutive failures,
      reason=rate_limit, cooldown=300s)
    [circuit_breaker] opensanctions.org: HALF_OPEN -> OPEN (probe failed,
      backoff now 2400s)

Two defects, and the first one wasted my own review time, which is the point.

1. THE MESSAGE IS UNCONDITIONAL. It tells the operator to set
   OPENSANCTIONS_API_KEY on every 429 — including when the key IS set.
   `flyctl secrets list` shows OPENSANCTIONS_API_KEY as Deployed, and
   `_opensanctions_headers()` does attach it as `Authorization: ApiKey ...`.
   So the log asserted a cause that the evidence contradicted, and I flagged
   "a deployed key that buys nothing" in the DD off the back of it. An asserted
   cause is worse than no cause (main.py:1766, CLAUDE.md §22).

2. NOTHING PACES THE CALLS. R-F469's response to a 429 storm was a circuit
   breaker — it stops the bleeding, but it treats the symptom: ARIA still fires
   as fast as it likes, earns a 429, and then disables sanctions screening for
   5-40 minutes. The documented free-tier limit is 1 req/sec and it is known in
   advance, so the root fix is to not exceed it.

Pacing is chosen over the breaker deliberately: a paced request SUCCEEDS, where a
breaker-skipped one returns no data. On a sanctions path, "slower" beats "absent".
"""
from __future__ import annotations

import asyncio
import time

import pytest

from aria_service.intel import sanctions


@pytest.fixture(autouse=True)
def _reset_pacer():
    sanctions._os_reset_pacing()
    yield
    sanctions._os_reset_pacing()


class TestClientSidePacing:

    @pytest.mark.asyncio
    async def test_calls_are_spaced_to_the_free_tier_limit(self, monkeypatch):
        """Unkeyed, consecutive calls must be >= the free-tier interval apart."""
        monkeypatch.setattr(sanctions, "OPENSANCTIONS_API_KEY", "")
        monkeypatch.setattr(sanctions, "_OS_FREE_INTERVAL_S", 0.20)
        monkeypatch.setattr(sanctions, "_OS_KEYED_INTERVAL_S", 0.0)

        stamps: list[float] = []
        for _ in range(3):
            await sanctions._opensanctions_pace()
            stamps.append(time.monotonic())

        gaps = [b - a for a, b in zip(stamps, stamps[1:])]
        assert all(g >= 0.18 for g in gaps), f"calls not paced: {gaps}"

    @pytest.mark.asyncio
    async def test_concurrent_callers_are_serialised(self, monkeypatch):
        """Pacing is worthless if 10 coroutines can all slip through together."""
        monkeypatch.setattr(sanctions, "OPENSANCTIONS_API_KEY", "")
        monkeypatch.setattr(sanctions, "_OS_FREE_INTERVAL_S", 0.10)
        monkeypatch.setattr(sanctions, "_OS_KEYED_INTERVAL_S", 0.0)

        t0 = time.monotonic()
        await asyncio.gather(*(sanctions._opensanctions_pace() for _ in range(5)))
        elapsed = time.monotonic() - t0
        # 5 calls at 0.1s spacing cannot complete in less than ~0.4s.
        assert elapsed >= 0.35, f"5 paced calls finished in {elapsed:.2f}s"

    @pytest.mark.asyncio
    async def test_a_configured_key_relaxes_the_interval(self, monkeypatch):
        """The paid plan is 100 req/sec — do not throttle it to free-tier speed."""
        monkeypatch.setattr(sanctions, "OPENSANCTIONS_API_KEY", "test-key")
        monkeypatch.setattr(sanctions, "_OS_FREE_INTERVAL_S", 5.0)
        monkeypatch.setattr(sanctions, "_OS_KEYED_INTERVAL_S", 0.0)

        t0 = time.monotonic()
        for _ in range(3):
            await sanctions._opensanctions_pace()
        assert (time.monotonic() - t0) < 1.0, "a keyed caller was throttled to free-tier speed"


class TestThe429MessageTellsTheTruth:

    def test_message_names_the_real_key_state_when_present(self):
        msg = sanctions._rate_limit_message(key_present=True, interval=0.05)
        assert "api_key_present=True" in msg
        assert "Set OPENSANCTIONS_API_KEY" not in msg, (
            "still telling the operator to set a key that is already set"
        )

    def test_message_asks_for_a_key_only_when_there_is_none(self):
        msg = sanctions._rate_limit_message(key_present=False, interval=1.05)
        assert "api_key_present=False" in msg
        assert "OPENSANCTIONS_API_KEY" in msg

    def test_message_never_asserts_an_unverified_cause(self):
        """Both branches must describe MEASURED state, not a guess."""
        for present in (True, False):
            msg = sanctions._rate_limit_message(key_present=present, interval=1.0)
            assert f"api_key_present={present}" in msg


class TestScreeningStaysHonestUnderRateLimit:
    """A rate-limited source must never read as 'clean'."""

    def test_rate_limit_is_reported_as_not_ok(self):
        q = sanctions._SourceQuery([], False, "rate_limit")
        assert q.ok is False, "a throttled query must not report ok"
        assert q.reason == "rate_limit"
