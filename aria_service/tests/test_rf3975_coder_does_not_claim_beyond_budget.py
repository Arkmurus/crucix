"""R-F3975 / C-64 — the self-coder CLAIMED 20 gaps per cycle against a budget of
6 per hour, which is how it reached 19,097 attempts and 0 fixes.

Live scoreboard: **claimed 19,097 · blocked 19,129 · fixed 0 · staged 0 · gold 0**,
with 10,361 of the blocks reading `Safety guardrail: rate_limit_exceeded:6`.

The mechanism is in the claim order, not in the cap:

    self_coder.py:617
        for gap in actionable[:MAX_GAPS_PER_CYCLE]:     # 20
            await self.gap_detector.mark_attempted(gap.gap_id)
            await self._record_scoreboard("claimed", gap, ...)
            result = await self.fix_gap(gap)            # <- rate limit lives HERE

`MAX_GAPS_PER_CYCLE` is 20 and the live cap is
`ARIA_CODER_MAX_FIXES_PER_HOUR=6` (the CODE default is 500 — the 6 is an
explicit production override). So every cycle marked twenty gaps attempted and
recorded twenty claims, then had fourteen or more refused inside `fix_gap`.
The scoreboard counted work the loop was never permitted to do, and each refused
gap still burned a `mark_attempted`.

**The fix is NOT to raise the cap.** §1 is explicit — no band-aid without the
root, and the root is that the coder claims work it has no budget to perform.
Reading the remaining budget BEFORE claiming makes the loop attempt only what it
can finish, and because `actionable` is already sorted by severity descending
(`self_coder.py:610`), the six slots go to the six most severe gaps instead of
to whatever arrived first. That is the prioritisation the loop never had.

The budget read must not itself consume a slot, so it is a plain read of the
same hourly bucket `check_and_increment_rate` charges — never an increment.

**Unreadable budget fails OPEN.** A store blip must not silently stop the
autonomous loop; §21c calls a loop that can see gaps but cannot act a P0, and
trading a spend risk for a dead loop is the wrong way round. `None` means "could
not measure" and the cycle proceeds on the old behaviour, where `fix_gap`'s own
limiter is still the authority.
"""
from __future__ import annotations

import asyncio

import pytest

from aria_service.autonomous import safety as SAFETY


# ── the budget read ──────────────────────────────────────────────────────────

def test_remaining_budget_is_cap_minus_spent(monkeypatch):
    async def _get(key):
        return "2"

    monkeypatch.setattr(SAFETY.rs, "get", _get)
    monkeypatch.setattr(SAFETY, "CODER_MAX_FIXES_PER_HOUR", 6)
    assert asyncio.run(SAFETY.remaining_fix_budget(coder=True)) == 4


def test_remaining_budget_is_zero_when_spent(monkeypatch):
    async def _get(key):
        return "6"

    monkeypatch.setattr(SAFETY.rs, "get", _get)
    monkeypatch.setattr(SAFETY, "CODER_MAX_FIXES_PER_HOUR", 6)
    assert asyncio.run(SAFETY.remaining_fix_budget(coder=True)) == 0


def test_remaining_budget_never_goes_negative(monkeypatch):
    async def _get(key):
        return "99"

    monkeypatch.setattr(SAFETY.rs, "get", _get)
    monkeypatch.setattr(SAFETY, "CODER_MAX_FIXES_PER_HOUR", 6)
    assert asyncio.run(SAFETY.remaining_fix_budget(coder=True)) == 0


def test_an_empty_bucket_is_the_full_cap(monkeypatch):
    async def _get(key):
        return None

    monkeypatch.setattr(SAFETY.rs, "get", _get)
    monkeypatch.setattr(SAFETY, "CODER_MAX_FIXES_PER_HOUR", 6)
    assert asyncio.run(SAFETY.remaining_fix_budget(coder=True)) == 6


def test_the_read_does_not_consume_a_slot(monkeypatch):
    """A budget check that charges a slot would be the defect with extra steps."""
    incremented = []

    async def _get(key):
        return "1"

    async def _incr(key, *a):
        incremented.append(key)
        return 2

    monkeypatch.setattr(SAFETY.rs, "get", _get)
    monkeypatch.setattr(SAFETY.rs, "incr", _incr)
    asyncio.run(SAFETY.remaining_fix_budget(coder=True))
    assert incremented == []


def test_an_unreadable_budget_is_unknown_not_zero(monkeypatch):
    """A store blip must not silently stop the autonomous loop (§21c P0)."""
    async def _boom(key):
        raise RuntimeError("store down")

    monkeypatch.setattr(SAFETY.rs, "get", _boom)
    assert asyncio.run(SAFETY.remaining_fix_budget(coder=True)) is None


def test_it_reads_the_same_bucket_the_limiter_charges(monkeypatch):
    """If the read and the charge address different keys, the budget is fiction."""
    seen = []

    async def _get(key):
        seen.append(key)
        return "0"

    monkeypatch.setattr(SAFETY.rs, "get", _get)
    asyncio.run(SAFETY.remaining_fix_budget(coder=True, hour_bucket=12345))
    expected = SAFETY.rate_bucket_key(
        key_fmt=SAFETY._CODER_RATE_KEY_FMT, hour_bucket=12345)
    assert seen == [expected]


# ── the coder must consult it before claiming ────────────────────────────────

def test_the_cycle_reads_the_budget_before_marking_attempted():
    from aria_service.autonomous import self_coder as SC
    from ._source_probe import function_code

    # Scope to the cycle itself. A module-wide scan is too coarse: `mark_attempted`
    # appears in other functions, so the ordering assertion below would compare
    # positions in unrelated code.
    src = function_code(SC, "_one_cycle")
    assert "remaining_fix_budget" in src, (
        "the coder still claims MAX_GAPS_PER_CYCLE gaps regardless of budget — "
        "it will keep marking attempts it is not permitted to perform"
    )
    # and the claim must come AFTER the budget slice
    i_budget = src.find("remaining_fix_budget")
    i_claim = src.find("mark_attempted")
    assert i_budget < i_claim, (
        "the budget is read after the claim, which changes nothing"
    )
