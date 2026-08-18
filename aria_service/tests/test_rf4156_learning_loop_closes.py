"""R-F4156 (C-177) — the learning loop could not close.

`mistake_ledger.mark_prevented` is described by its own route as *"the
closed-loop proof that autonomy + learning works"*, and by its own docstring as
*"the single most important metric in the whole self-awareness stack"*. It had
**exactly one caller in the entire tree: an HTTP handler**. Nothing in ARIA's own
reasoning could ever call it.

Measured live 2026-08-18:

```
mistake_ledger   total_entries 2,924   prevented_total 0
capability_gaps  total 500 (AT CAP)    resolved 0
aria_coder       85 cycles, 0 failures — the loop is healthy and running
```

So `prevented: 0` was structural, not a performance problem.

**And the penalty was permanent.** `predictor.py:153` turns every HIGH/CRITICAL
mistake WITHOUT a `prevented_count` into an extra `likely_failure`, which drags
`overall_confidence` down; `tasks.py` BLOCKS a task outright below 0.2. Because
the counter could never rise, ARIA grew monotonically more pessimistic about any
task type she had ever erred on, with no way to earn it back — the loop
compounded in the wrong direction. (Measured mitigation: `predictor/block_rate`
was empty and operating mode NORMAL, so this was a live trap, not yet a live
outage.)

**What is credited, and why it is earned.** The predictor surfaced mistake M as
a risk before this run; the run went ahead; it completed cleanly. That is
exactly the condition `predictor.py` names when it says a mistake *"has not yet
been prevented on a subsequent run"* — and exactly what `mark_prevented`'s
docstring describes: *"When the predictor surfaces a past mistake and the new
task avoids it, increment the prevented_count."* The mechanism existed; only the
caller was missing.

The guards below are what stop this becoming self-congratulation — above all
that a `blocked_by_predictor` run credits nothing, so the predictor cannot clear
a warning by refusing to act on it.
"""
from __future__ import annotations

import pytest

from aria_service.autonomous import tasks as T


# ── which ids a forecast penalises ──────────────────────────────────────────

def test_only_unprevented_high_severity_ids_are_captured():
    pred = {"past_mistakes": [
        {"mistake_id": "hi", "severity": "HIGH", "prevented_count": 0},
        {"mistake_id": "crit", "severity": "CRITICAL", "prevented_count": 0},
        {"mistake_id": "low", "severity": "LOW", "prevented_count": 0},
        {"mistake_id": "already", "severity": "HIGH", "prevented_count": 3},
        {"severity": "HIGH", "prevented_count": 0},          # no id
    ]}
    assert T._unprevented_ids(pred) == ["hi", "crit"]


def test_the_capture_is_bounded_and_never_raises():
    big = {"past_mistakes": [
        {"mistake_id": f"m{i}", "severity": "HIGH", "prevented_count": 0}
        for i in range(50)
    ]}
    assert len(T._unprevented_ids(big)) == 10
    assert T._unprevented_ids(None) == []
    assert T._unprevented_ids({"past_mistakes": "not-a-list"}) == []


# ── the credit itself ───────────────────────────────────────────────────────

@pytest.fixture
def calls(monkeypatch):
    seen: list[dict] = []

    async def _fake(mistake_id, prevented_by, context=""):
        seen.append({"mistake_id": mistake_id, "prevented_by": prevented_by,
                     "context": context})
        return {"ok": True}

    from aria_service.intel import mistake_ledger as ml
    monkeypatch.setattr(ml, "mark_prevented", _fake, raising=True)
    return seen


def _record(status="ok", ids=("m1", "m2"), **extra):
    r = {"status": status, "task_id": "DAILY-PROC-ANGOLA",
         "predictor": {"unprevented_ids": list(ids)}}
    r.update(extra)
    return r


@pytest.mark.asyncio
async def test_a_clean_run_credits_every_warned_mistake(calls):
    rec = _record()
    await T._credit_preventions(rec)
    assert [c["mistake_id"] for c in calls] == ["m1", "m2"]
    assert all(c["prevented_by"] == "predictor:DAILY-PROC-ANGOLA" for c in calls)
    assert rec["predictor"]["preventions_credited"] == 2


@pytest.mark.asyncio
async def test_a_BLOCKED_run_credits_nothing(calls):
    """The most important guard. If a blocked run counted, the predictor could
    clear its own warning by refusing to act on it — the metric would measure
    avoidance, not learning."""
    await T._credit_preventions(_record(status="blocked_by_predictor"))
    assert calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["error", "timeout", "skipped", ""])
async def test_a_run_that_did_not_succeed_credits_nothing(calls, status):
    await T._credit_preventions(_record(status=status))
    assert calls == [], f"{status!r} run credited a prevention"


@pytest.mark.asyncio
async def test_a_dry_run_credits_nothing(calls):
    """A dry run does not exercise the failure class, so it proves nothing."""
    await T._credit_preventions(_record(dry_run=True))
    assert calls == []


@pytest.mark.asyncio
async def test_a_run_with_no_warning_credits_nothing(calls):
    """No mistake was surfaced, so there is nothing to have prevented. This is
    what stops the counter drifting up on ordinary traffic."""
    await T._credit_preventions(_record(ids=()))
    assert calls == []
    await T._credit_preventions({"status": "ok", "task_id": "x"})
    assert calls == []


@pytest.mark.asyncio
async def test_a_failing_ledger_never_breaks_a_successful_task(monkeypatch, calls):
    """Bookkeeping must not be able to fail a task that already succeeded."""
    from aria_service.intel import mistake_ledger as ml

    async def _boom(**kw):
        raise RuntimeError("ledger down")
    monkeypatch.setattr(ml, "mark_prevented", _boom, raising=True)

    rec = _record()
    await T._credit_preventions(rec)          # must not raise
    assert "preventions_credited" not in rec.get("predictor", {})


@pytest.mark.asyncio
async def test_partial_failure_still_credits_the_rest(monkeypatch):
    """One bad id must not abort the others."""
    seen = []

    async def _flaky(mistake_id, prevented_by, context=""):
        if mistake_id == "m1":
            raise RuntimeError("nope")
        seen.append(mistake_id)
        return {"ok": True}

    from aria_service.intel import mistake_ledger as ml
    monkeypatch.setattr(ml, "mark_prevented", _flaky, raising=True)
    rec = _record(ids=("m1", "m2", "m3"))
    await T._credit_preventions(rec)
    assert seen == ["m2", "m3"]
    assert rec["predictor"]["preventions_credited"] == 2


# ── it must run from the ONE funnel every exit already uses ─────────────────

@pytest.mark.asyncio
async def test_record_run_is_the_credit_point(monkeypatch, calls):
    """Eight `record_run` call sites, one credit point. Crediting beside the two
    success returns instead would be whack-a-mole: the ninth exit would silently
    stop crediting."""
    async def _noop(*a, **k):
        return None
    from aria_service.intel import redis_store as rs
    monkeypatch.setattr(rs, "lpush", _noop, raising=False)
    monkeypatch.setattr(rs, "ltrim", _noop, raising=False)

    await T.record_run(_record())
    assert [c["mistake_id"] for c in calls] == ["m1", "m2"]


@pytest.mark.asyncio
async def test_record_run_still_persists_when_crediting_is_impossible(monkeypatch):
    """The credit is best-effort; persistence is not."""
    pushed = []

    async def _lpush(key, val, **k):
        pushed.append(key)

    async def _ltrim(*a, **k):
        return None

    from aria_service.intel import redis_store as rs
    from aria_service.intel import mistake_ledger as ml
    monkeypatch.setattr(rs, "lpush", _lpush, raising=False)
    monkeypatch.setattr(rs, "ltrim", _ltrim, raising=False)

    async def _boom(**kw):
        raise RuntimeError("ledger down")
    monkeypatch.setattr(ml, "mark_prevented", _boom, raising=True)

    await T.record_run(_record())
    assert pushed, "the run record was not persisted"
