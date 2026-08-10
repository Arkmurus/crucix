"""R-F3823 — a duplicate must not consume a fix slot. ARIA-Coder was starved by it.

THE DEFECT, measured live 2026-08-09.

ARIA-Coder detected 96-100 actionable gaps per scan and fixed **zero**. Every attempt
came back `rate_limit_exceeded:6` (x5) or `duplicate_recent_run` (x4). Per §21c a loop
that can see gaps but cannot act is a P0, and this one was draining nothing.

`can_task_run` runs its guardrails cheapest-first and INCREMENTS the rate bucket at
step 4, then checks dedupe at step 5:

    4. Hourly rate limit hit?   (1 Redis incr + maybe expire)   <-- consumes a slot
    5. Duplicate of recent run? (1 Redis get + maybe set)       <-- rejects here

So an attempt that is about to be thrown away as a duplicate has ALREADY spent a
slot. The function's own docstring states the invariant it breaks: *"rate limit is the
LAST check that increments state, so a task blocked by the cost cap or pause does NOT
consume a rate bucket slot."* Dedupe simply sits on the wrong side of it.

That is survivable at the default cap of 500/hour and fatal at the live one. Measured
on the box: `ARIA_CODER_MAX_FIXES_PER_HOUR=6`, dedupe window 82,800s (23h). The coder
re-attempts the same gaps every 15-minute scan, so four no-op duplicates ate four of
six slots and the remaining attempts hit the cap. Real fixes: none.

WHY THE OBVIOUS FIX IS WRONG. Simply swapping steps 4 and 5 makes it worse:
`check_and_mark_dedupe` MARKS on the pass path, so a task that clears dedupe and is
then refused by the rate limit would be recorded as "already run" and locked out for
23h without ever executing. The check and the mark have to be separated — reject
early on a READ, and mark only once the slot is actually secured.
"""
from __future__ import annotations

import pytest

from aria_service.autonomous import safety


@pytest.fixture
def quiet_guards(monkeypatch):
    """Neutralise the guardrails this test is not about."""
    async def _no(*_a, **_k):
        return False

    async def _budget_ok(*_a, **_k):
        return True, 0.0

    monkeypatch.setattr(safety, "is_engine_paused", _no)
    monkeypatch.setattr(safety, "is_task_paused", _no)
    monkeypatch.setattr(safety, "check_cost_cap", _budget_ok)


@pytest.mark.asyncio
async def test_a_duplicate_does_not_consume_a_rate_slot(quiet_guards, monkeypatch):
    """THE DEFECT. With a live cap of 6/hour, four duplicates burned four slots."""
    rate_calls = []

    async def _rate(**kw):
        rate_calls.append(kw)
        return True, len(rate_calls)

    async def _already_seen(*_a, **_k):
        return True          # this task+entity IS a recent duplicate

    monkeypatch.setattr(safety, "check_and_increment_rate", _rate)
    monkeypatch.setattr(safety, "is_recent_duplicate", _already_seen)

    allowed, reason = await safety.can_task_run("GAP-1", "mod.py", coder=True)

    assert allowed is False
    assert reason == "duplicate_recent_run"
    assert rate_calls == [], (
        "a duplicate did no work, so it must not spend a fix slot — this is what "
        "starved ARIA-Coder to zero fixes an hour")


@pytest.mark.asyncio
async def test_a_fresh_task_still_takes_a_slot_and_is_marked(quiet_guards, monkeypatch):
    """The half that keeps the limiter real: non-duplicates DO consume budget, and
    the dedupe marker is written only once the slot is secured."""
    rate_calls, marks = [], []

    async def _rate(**kw):
        rate_calls.append(kw)
        return True, 1

    async def _not_dup(*_a, **_k):
        return False

    async def _mark(task_id, entity, *, slot=None):
        marks.append((task_id, entity, slot))
        return True

    monkeypatch.setattr(safety, "check_and_increment_rate", _rate)
    monkeypatch.setattr(safety, "is_recent_duplicate", _not_dup)
    monkeypatch.setattr(safety, "check_and_mark_dedupe", _mark)

    allowed, reason = await safety.can_task_run("GAP-2", "other.py", coder=True)

    assert (allowed, reason) == (True, "ok")
    assert len(rate_calls) == 1, "a real run must still spend a slot"
    assert marks == [("GAP-2", "other.py", None)], "the run must be marked as done"


@pytest.mark.asyncio
async def test_a_rate_limited_task_is_NOT_marked_as_run(quiet_guards, monkeypatch):
    """THE BUG THE NAIVE FIX WOULD INTRODUCE — the reason check and mark are split.

    If dedupe simply moved ahead of the rate limit, `check_and_mark_dedupe` would
    MARK a task that the rate limiter then refused, locking it out for 23h without it
    ever running. Rejected work must leave no trace in either budget.
    """
    marks = []

    async def _rate_exhausted(**_kw):
        return False, 6      # the live cap

    async def _not_dup(*_a, **_k):
        return False

    async def _mark(task_id, entity, *, slot=None):
        marks.append(task_id)
        return True

    monkeypatch.setattr(safety, "check_and_increment_rate", _rate_exhausted)
    monkeypatch.setattr(safety, "is_recent_duplicate", _not_dup)
    monkeypatch.setattr(safety, "check_and_mark_dedupe", _mark)

    allowed, reason = await safety.can_task_run("GAP-3", "x.py", coder=True)

    assert allowed is False
    assert reason.startswith("rate_limit_exceeded")
    assert marks == [], (
        "a task refused by the rate limiter must NOT be marked as run, or it is "
        "locked out for the whole 23h dedupe window having done nothing")


@pytest.mark.asyncio
async def test_pause_and_cost_cap_still_short_circuit_before_any_budget(monkeypatch):
    """The existing invariant must survive: a paused engine spends nothing."""
    rate_calls = []

    async def _paused(*_a, **_k):
        return True

    async def _rate(**kw):
        rate_calls.append(kw)
        return True, 1

    monkeypatch.setattr(safety, "is_engine_paused", _paused)
    monkeypatch.setattr(safety, "check_and_increment_rate", _rate)

    allowed, reason = await safety.can_task_run("GAP-4", "x.py", coder=True)
    assert (allowed, reason) == (False, "engine_paused")
    assert rate_calls == []


def test_is_recent_duplicate_is_read_only():
    """It must not write, or it becomes the marker it is meant to precede."""
    from aria_service.tests._source_probe import function_source

    src = function_source(safety, "is_recent_duplicate")
    assert "rs.get" in src or "get(" in src
    assert "rs.set" not in src, (
        "is_recent_duplicate must READ only — marking is check_and_mark_dedupe's job")
