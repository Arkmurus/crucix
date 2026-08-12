"""R-F3919 — a false-positive gap consumed a coder slot and never gave it back.

MEASURED LIVE 2026-08-12, 15 monitoring cycles, ARIA_CODER_MAX_FIXES_PER_HOUR=6:

     7x  [aria_coder] stage=reproducing_symptom
     4x  [aria_coder] not fixed: Reproduce-symptom gate: ...      <- 4 of 6 slots
    10x  [aria_coder] not fixed: Safety guardrail: rate_limit_exceeded:6
    gap_detector: 105 -> 110 -> 127 actionable gaps over the same window

Two thirds of the hourly budget was spent on gaps the coder then discarded as NOT
REAL, and the remaining attempts were refused. §21c names this exactly: "if it can
see gaps but can't act, that's a P0".

THE MECHANISM. `fix_gap` calls `can_task_run(coder=True)`, which CONSUMES a slot,
and only then runs the R-F1460 reproduce-symptom gate — whose whole purpose is to
throw the gap away when its symptom cannot be reproduced. No LLM tokens, no fix
attempted, slot gone.

THIS IS THE THIRD TIME THE SAME INVARIANT HAS BROKEN, and `can_task_run`'s docstring
states it: "rate limit is the LAST check that increments state".
  * R-F897  — an OVER-CAP attempt inflated the bucket, so a backlog of N>cap drove
    the counter to N on one scan and never drained: "the coder saw 43 gaps and
    fixed 0". Fixed by rolling the speculative incr back.
  * R-F3823 — the dedupe check sat BELOW the limiter, so an attempt about to be
    discarded as a duplicate had already consumed a slot. Fixed by moving the
    (cheap, read-only) dedupe check above it.
  * THIS ONE — the reproduce gate is the same shape, but it RUNS A TEST, so it
    cannot move above `can_task_run` without executing work before the engine-pause
    and cost-cap checks. Hence a refund instead of a reorder.

DELIBERATELY NARROW. Not a general undo: an attempt that reached the LLM or failed
validation DID consume what the budget meters and keeps its slot. Only a gap
discarded as not-real refunds.
"""
from __future__ import annotations

import time

import pytest

from aria_service.autonomous import safety


class _Bucket:
    """Minimal stand-in for the rate-bucket store."""

    def __init__(self, start: dict | None = None):
        self.data = dict(start or {})

    async def get(self, key):
        v = self.data.get(key)
        return None if v is None else str(v)

    async def incr(self, key, amount=1, **kw):
        self.data[key] = int(self.data.get(key, 0)) + amount
        return self.data[key]

    async def expire(self, key, seconds):
        return True


def _coder_key() -> str:
    return safety._CODER_RATE_KEY_FMT.format(hour=int(time.time() // 3600))


@pytest.mark.asyncio
async def test_a_refund_returns_the_slot(monkeypatch):
    """THE CAPABILITY: a consumed slot comes back."""
    key = _coder_key()
    bucket = _Bucket({key: 4})
    monkeypatch.setattr(safety, "rs", bucket)

    assert await safety.release_rate_slot(coder=True) is True
    assert bucket.data[key] == 3


@pytest.mark.asyncio
async def test_a_refund_never_manufactures_budget(monkeypatch):
    """An empty or absent bucket must NOT go negative — inventing budget is the
    opposite failure, and would let the coder exceed a cap the operator set."""
    key = _coder_key()

    bucket = _Bucket({key: 0})
    monkeypatch.setattr(safety, "rs", bucket)
    assert await safety.release_rate_slot(coder=True) is False
    assert bucket.data[key] == 0

    empty = _Bucket()
    monkeypatch.setattr(safety, "rs", empty)
    assert await safety.release_rate_slot(coder=True) is False
    assert empty.data.get(key) in (None, 0)


@pytest.mark.asyncio
async def test_the_refund_targets_the_coder_bucket_not_the_shared_one(monkeypatch):
    """R-F901 gave the coder its OWN bucket so the shared 87-task budget cannot
    starve it. Refunding the wrong one would hand the coder's slot to everything
    else — and leave the coder exactly as blocked."""
    coder_key = _coder_key()
    shared_key = safety._RATE_KEY_FMT.format(hour=int(time.time() // 3600))
    bucket = _Bucket({coder_key: 3, shared_key: 3})
    monkeypatch.setattr(safety, "rs", bucket)

    await safety.release_rate_slot(coder=True)
    assert bucket.data[coder_key] == 2, "the coder bucket must be refunded"
    assert bucket.data[shared_key] == 3, "the shared bucket must be untouched"

    await safety.release_rate_slot(coder=False)
    assert bucket.data[shared_key] == 2
    assert bucket.data[coder_key] == 2


@pytest.mark.asyncio
async def test_a_refund_never_raises_into_the_coder(monkeypatch):
    """Budget bookkeeping must never break the loop it meters."""
    class _Dead:
        async def get(self, *a, **k): raise RuntimeError("store down")
        async def incr(self, *a, **k): raise RuntimeError("store down")

    monkeypatch.setattr(safety, "rs", _Dead())
    assert await safety.release_rate_slot(coder=True) is False


# ── the call site: the gate that discards must be the gate that refunds ────────

def test_the_reproduce_gate_refunds_its_slot():
    """Pinned at the call site. Without this the budget is silently eaten by gaps
    that were never real — invisible, because the log line says only 'not fixed'."""
    from aria_service.tests._source_probe import function_source
    from aria_service.autonomous import self_coder

    src = function_source(self_coder.SelfCoder, "fix_gap") \
        if hasattr(self_coder, "SelfCoder") else ""
    if not src:
        from aria_service.tests._source_probe import module_source
        src = module_source(self_coder)

    gate = src[src.find("R-F1460"):]
    assert "release_rate_slot" in gate, (
        "the reproduce-symptom gate discards a gap as a false positive but does not "
        "refund the slot it consumed — R-F3919. At the live cap of 6/hour this "
        "starves the coder to zero fixes.")


def test_the_refund_is_not_applied_to_real_work():
    """The narrowness IS the design. If a refund appeared on the LLM/validation
    failure paths, the cap would stop metering anything and the operator's ceiling
    would become advisory."""
    from aria_service.tests._source_probe import module_source
    from aria_service.autonomous import self_coder

    src = module_source(self_coder)
    assert src.count("release_rate_slot") == 1, (
        f"expected exactly ONE refund site (the reproduce gate); found "
        f"{src.count('release_rate_slot')}. A refund on a path that did real work "
        f"turns the hourly cap into a suggestion.")


def test_the_invariant_is_documented_where_it_keeps_breaking():
    """Three fixes have now restored the same rule. The next reader must find it."""
    from aria_service.tests._source_probe import function_source

    doc = function_source(safety, "release_rate_slot")
    for prior in ("R-F897", "R-F3823"):
        assert prior in doc, (
            f"the refund must cite {prior} — it is the same invariant, and without "
            f"the history the next person re-derives it from scratch")
