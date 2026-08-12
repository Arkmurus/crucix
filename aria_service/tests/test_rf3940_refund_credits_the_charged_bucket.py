"""R-F3940 — the refund credited whatever hour it was WHEN IT RAN.

R-F3919 gave the coder its slot back when the R-F1460 reproduce gate discards a gap
as a false positive. It re-derived the bucket key from `time.time()` at REFUND time:

    key = key_fmt.format(hour=int(time.time() // 3600))

But the slot was charged back at `can_task_run`, and the thing sitting between the
two is the reproduce gate, which RUNS A TEST. Straddle an hour boundary and the
refund lands on the WRONG bucket — twice wrong, in opposite directions:

  * the hour that was charged stays over-counted, so the budget loss R-F3919 exists
    to end still happens for that hour; and
  * the new hour is handed a slot nobody paid for.

That second half is the same manufactured-budget failure the zero-guard inside
`release_rate_slot` was written to prevent — arriving through the KEY instead of
through the count, which is why the guard could not see it.

ROOT CAUSE, NOT THE SYMPTOM (§1). `int(time.time() // 3600)` was open-coded in FIVE
places in safety.py. Two of them running at different moments is all it takes; the
refund bug is one instance of a derivation that was never shared. There is now one
`current_hour_bucket()` / `rate_bucket_key()` pair, and callers that may refund pass
the bucket they charged to BOTH sides — so charge and refund provably name one
bucket, with no window for the clock to move.
"""
from __future__ import annotations

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


# ── the defect, reproduced ─────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_refund_credits_the_charged_hour_not_the_current_one(monkeypatch):
    """THE REGRESSION TEST. Charge in hour H, refund while the clock reads H+1."""
    charged_hour = 100_000
    charged_key = safety.rate_bucket_key(coder=True, hour_bucket=charged_hour)
    later_key = safety.rate_bucket_key(coder=True, hour_bucket=charged_hour + 1)

    # hour H holds the slot this attempt charged; hour H+1 already has an
    # unrelated firing, so a misdirected refund is silently plausible there.
    bucket = _Bucket({charged_key: 1, later_key: 1})
    monkeypatch.setattr(safety, "rs", bucket)
    # the clock has moved on while the reproduce gate ran a test
    monkeypatch.setattr(safety, "current_hour_bucket", lambda: charged_hour + 1)

    assert await safety.release_rate_slot(coder=True, bucket_key=charged_key) is True

    assert bucket.data[charged_key] == 0, (
        "the refund must land on the bucket that was CHARGED (R-F3940)")
    assert bucket.data[later_key] == 1, (
        "the current hour must not be handed a slot nobody paid for (R-F3940)")


@pytest.mark.asyncio
async def test_without_an_explicit_bucket_it_still_uses_now(monkeypatch):
    """The default path is unchanged — every existing caller keeps working."""
    now_hour = 200_000
    now_key = safety.rate_bucket_key(coder=True, hour_bucket=now_hour)
    bucket = _Bucket({now_key: 2})
    monkeypatch.setattr(safety, "rs", bucket)
    monkeypatch.setattr(safety, "current_hour_bucket", lambda: now_hour)

    assert await safety.release_rate_slot(coder=True) is True
    assert bucket.data[now_key] == 1


# ── the guard must still be able to FAIL (R-F3858) ─────────────────────────────

@pytest.mark.asyncio
async def test_an_empty_bucket_is_never_driven_negative(monkeypatch):
    """A refund against a bucket with nothing in it must not manufacture budget."""
    key = safety.rate_bucket_key(coder=True, hour_bucket=300_000)
    bucket = _Bucket({})
    monkeypatch.setattr(safety, "rs", bucket)

    assert await safety.release_rate_slot(coder=True, bucket_key=key) is False
    assert bucket.data.get(key) in (None, 0), "must not go negative"


@pytest.mark.asyncio
async def test_a_refund_that_does_not_land_reaches_the_brain(monkeypatch):
    """§21a — a lost slot is the P0 R-F3919 exists to end, so it must not be silent.

    Without this the failure branch returns a bare False and nothing anywhere knows
    a slot was permanently lost, which is how the original leak stayed invisible.
    """
    seen: list[dict] = []
    monkeypatch.setattr(safety, "wire_failure",
                        lambda **kw: seen.append(kw))
    monkeypatch.setattr(safety, "rs", _Bucket({}))


    key = safety.rate_bucket_key(coder=True, hour_bucket=400_000)
    assert await safety.release_rate_slot(coder=True, bucket_key=key) is False

    assert seen, "a refund that did not land must be wired to the brain (R-F3940)"
    assert seen[0].get("module") == "safety"
    assert key in seen[0].get("detail", ""), "the signal must name the lost bucket"


# ── the root cause: one derivation, not five ───────────────────────────────────

def test_the_hour_bucket_is_derived_in_exactly_one_place():
    """The duplication IS the defect — pin it, or it grows back.

    Uses the shared code-only reader (R-F3937) so this cannot match the very
    explanation above that quotes the offending expression.
    """
    from aria_service.tests._source_probe import module_code

    src = module_code(safety)
    open_coded = src.count("time.time() // 3600")
    assert open_coded == 1, (
        f"the hour bucket is derived {open_coded}x in safety.py; it must be derived "
        "once, in current_hour_bucket(), or a refund will again address a bucket it "
        "never charged (R-F3940)")


def test_that_guard_can_actually_fail():
    """R-F3858 — prove the counter above is reading real source, not an empty string."""
    from aria_service.tests._source_probe import module_code

    src = module_code(safety)
    assert "def current_hour_bucket" in src, "module_code returned nothing usable"
    assert src.count("time.time() // 3600") == 1


@pytest.mark.asyncio
async def test_charge_and_refund_agree_on_the_bucket_end_to_end(monkeypatch):
    """The whole point: charge via the limiter, refund via the key, net zero."""
    hour = 500_000
    bucket = _Bucket({})
    monkeypatch.setattr(safety, "rs", bucket)
    monkeypatch.setattr(safety, "CODER_MAX_FIXES_PER_HOUR", 6)

    allowed, count = await safety.check_and_increment_rate(
        key_fmt=safety._CODER_RATE_KEY_FMT, cap=6, hour_bucket=hour)
    assert (allowed, count) == (True, 1)

    key = safety.rate_bucket_key(coder=True, hour_bucket=hour)
    # the clock ticks over before the gate finishes
    monkeypatch.setattr(safety, "current_hour_bucket", lambda: hour + 1)
    assert await safety.release_rate_slot(coder=True, bucket_key=key) is True

    assert bucket.data[key] == 0, "the charged bucket must be back to zero"
