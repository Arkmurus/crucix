"""R-F897 (P0-1) — autonomous rate-limiter rolls back blocked attempts.

Live 2026-05-26: after R-F884 reconnected the coder it saw 43 gaps but fixed 0
(rate_limit_exceeded). Root cause: check_and_increment_rate did an unconditional
INCR, so a backlog of N>cap gaps drove the hour bucket to N on one scan and it
never drained back under cap — the bucket inflated to 43 against a cap of 12.
Fix: roll the speculative INCR back when over-cap so the bucket only reflects
EXECUTED firings; the backlog drains at the cap (12/hr) instead of 0/hr.
"""
from __future__ import annotations

import asyncio

import aria_service.autonomous.safety as safety


class _MockRedis:
    def __init__(self):
        self.store = {}
    async def incr(self, key, amount=1):
        self.store[key] = self.store.get(key, 0) + amount
        return self.store[key]
    async def expire(self, key, ttl):
        pass


def test_blocked_attempts_do_not_inflate_bucket(monkeypatch):
    mr = _MockRedis()
    monkeypatch.setattr(safety, "rs", mr)
    monkeypatch.setattr(safety, "MAX_FIRINGS_PER_HOUR", 12)

    async def run():
        allowed_count = 0
        for _ in range(43):                  # 43-gap backlog, cap 12
            allowed, count = await safety.check_and_increment_rate()
            if allowed:
                allowed_count += 1
        return allowed_count

    allowed = asyncio.run(run())
    assert allowed == 12, f"expected exactly 12 firings allowed, got {allowed}"
    # the bucket must have settled AT the cap (12), not inflated to 43
    key = next(k for k in mr.store if "rate" in k.lower()) if any("rate" in k.lower() for k in mr.store) else list(mr.store)[0]
    assert mr.store[key] == 12, f"bucket inflated to {mr.store[key]} (should be 12)"


def test_rf901_coder_has_separate_budget(monkeypatch):
    """R-F901 — the coder's own bucket is independent of the shared task budget,
    so the 87 periodic tasks can't starve it (the live STAGED=0 cause)."""
    mr = _MockRedis()
    monkeypatch.setattr(safety, "rs", mr)
    monkeypatch.setattr(safety, "MAX_FIRINGS_PER_HOUR", 12)
    monkeypatch.setattr(safety, "CODER_MAX_FIXES_PER_HOUR", 6)

    async def run():
        # Exhaust the SHARED task bucket (12)
        for _ in range(12):
            await safety.check_and_increment_rate()
        task_blocked, _ = await safety.check_and_increment_rate()
        # The coder bucket must be UNTOUCHED — coder still gets its own 6
        coder_allowed = 0
        for _ in range(8):
            ok, _ = await safety.check_and_increment_rate(
                key_fmt=safety._CODER_RATE_KEY_FMT, cap=safety.CODER_MAX_FIXES_PER_HOUR,
            )
            if ok:
                coder_allowed += 1
        return task_blocked, coder_allowed

    task_blocked, coder_allowed = asyncio.run(run())
    assert task_blocked is False                 # shared bucket exhausted
    assert coder_allowed == 6, coder_allowed     # coder still got its own 6


def test_rf901_can_task_run_coder_uses_coder_bucket(monkeypatch):
    """can_task_run(coder=True) increments the coder bucket, not the task one."""
    mr = _MockRedis()
    monkeypatch.setattr(safety, "rs", mr)
    # neutralise the other guardrails so we isolate the rate check
    async def _no(*a, **k): return False
    async def _ok_cost(*a, **k): return (True, 0.0)
    async def _ok_dedupe(*a, **k): return True
    monkeypatch.setattr(safety, "is_engine_paused", _no)
    monkeypatch.setattr(safety, "is_task_paused", _no)
    monkeypatch.setattr(safety, "check_cost_cap", _ok_cost)
    monkeypatch.setattr(safety, "check_and_mark_dedupe", _ok_dedupe)

    async def run():
        allowed, _ = await safety.can_task_run("aria_coder_fix", "g1", coder=True)
        return allowed, dict(mr.store)
    allowed, store = asyncio.run(run())
    assert allowed is True
    assert any("coder_rate" in k for k in store), store   # the coder bucket got the incr


def test_under_cap_still_allowed(monkeypatch):
    mr = _MockRedis()
    monkeypatch.setattr(safety, "rs", mr)
    monkeypatch.setattr(safety, "MAX_FIRINGS_PER_HOUR", 12)

    async def run():
        return [await safety.check_and_increment_rate() for _ in range(5)]

    results = asyncio.run(run())
    assert all(a for a, _ in results)          # all 5 allowed
    assert results[-1][1] == 5                  # count reflects exactly 5
