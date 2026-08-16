"""R-F4066 (C-112) — calibration's ground-truth mean must exclude signals it
cannot measure, and the READ path must not mutate mastery.

Measured on aria-intel 2026-08-16 17:21Z:

    signals: {honesty_accuracy: 0.0,   adversarial_accuracy: 0.802,
              mistake_rate: 2.3907,    eval_pass_rate: 0.333}
    estimated_accuracy = 0.2838      calibration_status = "overconfident"
    recommendation: "MASTERY IS OVERCONFIDENT by 54%"

Two of those four "ground truth" signals are zeros for structural reasons, not
for accuracy reasons:

  * `mistake_rate` = 2888 mistake-ledger rows / 1208 chat-audit rows = 2.3907.
    Those are DIFFERENT POPULATIONS — the ledger counts autonomous, source and
    engine mistakes across all of time, the denominator counts chat turns from a
    log that has itself lost 37% of its entries (C-111). A rate above 1.0 is
    proof the two do not describe the same thing. `1.0 - min(rate, 1.0)` then
    converts "unmeasurable" into a measured **0.0**.
  * `honesty_accuracy` = 0.0 from `scored_sample_size: 1`. `autonomy_scorer`
    refuses that sample (`_MIN_SIGNAL_SAMPLES`, R-F1907) and `operating_modes`
    refuses it too (`GROUNDED_MIN_SAMPLES`, R-F3764). calibration_review is the
    only consumer that accepts it — and the only one with WRITE AUTHORITY over
    mastery.

The write is the reason this is a defect and not a display nit. R-F166's
overconfident branch calls `student.lift_all_topics(-drop)` — up to -3pp on
EVERY topic. Live evidence that it fires: `crucix:calibration:last_correction`
was written 24.4 minutes before the measurement above, with status
`overconfident`, so the downward branch had just run. Three of the ten
CORE_MASTERY_TAGS sit at exactly their `HARD_FLOORS` value despite 68 / 76 / 281
graded samples at ~90% correct — arithmetically impossible under
`MASTERY_LR_POSITIVE = 0.18` unless something is pushing them down.

And the correction had no scheduled home. A repo-wide search finds NO periodic
caller of `run_calibration_review()`: the production callers are
`GET /api/aria/calibration/review` (which is in the brain-dashboard aggregate
registry) and `save_baseline()`. **Opening the operator's command centre is what
marked mastery down.** The module comment even says the hourly rate-limit exists
"so dashboard polling doesn't compound" — acknowledging the driver instead of
removing it.

These tests pin the fix:
  1. an out-of-range mistake_rate is EXCLUDED, never contributed as 0.0;
  2. an under-sampled honesty score is EXCLUDED, matching its two sibling
     consumers;
  3. a well-sampled honesty score is still USED (the guard must not blind the
     signal — a guard that cannot pass is as useless as one that cannot fail);
  4. the default (read) path does not call `lift_all_topics`;
  5. `apply_correction=True` still corrects, so the capability is relocated, not
     deleted;
  6. `correction_applied` survives persistence, so the durable record can say
     whether mastery was mutated.
"""
from __future__ import annotations

import contextlib
from unittest.mock import AsyncMock, patch

import pytest


# ── harness ────────────────────────────────────────────────────────────────

def _patches(
    *,
    headline: float,
    honesty: dict,
    adversarial: float | None,
    mistakes: int,
    audit_entries: int,
    eval_pass_rate: float | None,
    lift_spy: AsyncMock,
    set_json_spy: AsyncMock,
):
    """Patch every lazy import `run_calibration_review` reaches for.

    Mirrors test_cap_calibration_adversarial_fallback.py's approach (patch the
    source module, not a local alias) because calibration_review imports each
    dependency inside the function body.
    """
    adv_stats: dict = {"last_run": {}}
    if adversarial is not None:
        adv_stats = {
            "last_run": {
                "run_at": "2026-08-16T16:22:30+00:00",
                "overall_score": adversarial,
                "degraded": False,
                "invalid": None,
            },
            "runs": [],
        }

    eval_runs: list = []
    if eval_pass_rate is not None:
        eval_runs = [{"summary": {"pass_rate": eval_pass_rate, "degraded": False,
                                  "total": 500}}]

    stack = contextlib.ExitStack()
    stack.enter_context(patch(
        "aria_service.intel.student.get_mastery_report",
        new=AsyncMock(return_value={"headline_mastery": headline, "topics": {}})))
    stack.enter_context(patch(
        "aria_service.intel.student.lift_all_topics", new=lift_spy))
    stack.enter_context(patch(
        "aria_service.intel.honesty_judge.get_honesty_stats",
        new=AsyncMock(return_value=honesty)))
    stack.enter_context(patch(
        "aria_service.intel.adversarial_challenge.stats",
        new=AsyncMock(return_value=adv_stats)))
    stack.enter_context(patch(
        "aria_service.intel.chat_audit_log.get_stats",
        new=AsyncMock(return_value={"total_entries": audit_entries})))
    stack.enter_context(patch(
        "aria_service.intel.redis_store.llen", new=AsyncMock(return_value=mistakes)))
    stack.enter_context(patch(
        "aria_service.intel.eval_runner.get_recent_runs",
        new=AsyncMock(return_value=eval_runs)))
    stack.enter_context(patch(
        "aria_service.intel.redis_store.set_json", new=set_json_spy))
    # `rs.get(_K_LAST_CORRECTION)` returning None means "never corrected", so the
    # cooldown is always open — that is what we want when asserting the write.
    stack.enter_context(patch(
        "aria_service.intel.redis_store.get", new=AsyncMock(return_value=None)))
    stack.enter_context(patch(
        "aria_service.intel.redis_store.set", new=AsyncMock()))
    stack.enter_context(patch(
        "aria_service.intel.brain_hook.absorb", new=AsyncMock()))
    stack.enter_context(patch(
        "aria_service.intel.brain_hook.absorb_silent", new=AsyncMock()))
    return stack


# The live reading that produced the fabricated 24%.
_LIVE_HONESTY_N1 = {"avg_honesty_score": 0.0, "scored_sample_size": 1,
                    "lifetime_honesty_score": 0.236, "recent_24h": 1}
_HEALTHY_HONESTY = {"avg_honesty_score": 0.40, "scored_sample_size": 12,
                    "lifetime_honesty_score": 0.41, "recent_24h": 12}
_NO_HONESTY: dict = {}


async def _run(**kw):
    from aria_service.intel import calibration_review as cr
    lift = AsyncMock(return_value={})
    set_json = AsyncMock()
    apply_correction = kw.pop("apply_correction", None)
    with _patches(lift_spy=lift, set_json_spy=set_json, **kw):
        if apply_correction is None:
            review = await cr.run_calibration_review()
        else:
            review = await cr.run_calibration_review(
                apply_correction=apply_correction)
    return review, lift, set_json


# ── 1. an impossible rate is not a measured zero ───────────────────────────

@pytest.mark.asyncio
async def test_mistake_rate_above_one_is_excluded_not_zeroed():
    """2888/1208 = 2.39 is not "0% accurate", it is not a rate at all."""
    review, _lift, _sj = await _run(
        headline=0.822,
        honesty=_NO_HONESTY,
        adversarial=0.802,
        mistakes=2888,          # the live mistake-ledger length
        audit_entries=1208,     # the live (truncated) chat-audit length
        eval_pass_rate=0.333,
    )
    excluded = review.get("excluded_signals") or {}
    assert "mistake_rate" in excluded, (
        "a mistake_rate of 2.39 must be reported as unmeasurable, not folded "
        f"into the mean as 0.0. review={review.get('signals')} "
        f"excluded={excluded}"
    )
    # mean(0.802, 0.333) = 0.5675 — NOT mean(0.802, 0.0, 0.333) = 0.3783
    est = review["estimated_accuracy"]
    assert abs(est - 0.5675) < 0.002, (
        f"expected the clamped zero to be gone (0.5675), got {est}")
    # The raw observation is still reported — excluding it from the mean must
    # not hide it from the operator.
    assert review["signals"]["mistake_rate"] == pytest.approx(2.3907, abs=1e-3)


@pytest.mark.asyncio
async def test_a_real_mistake_rate_is_still_used():
    """The guard must not blind a legitimate in-range rate."""
    review, _lift, _sj = await _run(
        headline=0.822,
        honesty=_NO_HONESTY,
        adversarial=0.802,
        mistakes=5,
        audit_entries=100,      # 0.05 → contributes 0.95
        eval_pass_rate=0.333,
    )
    assert "mistake_rate" not in (review.get("excluded_signals") or {})
    est = review["estimated_accuracy"]
    assert abs(est - (0.802 + 0.95 + 0.333) / 3) < 0.002, est


# ── 2. the min-sample guard its two siblings already have ──────────────────

@pytest.mark.asyncio
async def test_under_sampled_honesty_is_excluded():
    review, _lift, _sj = await _run(
        headline=0.822,
        honesty=_LIVE_HONESTY_N1,
        adversarial=0.802,
        mistakes=5,
        audit_entries=100,
        eval_pass_rate=0.333,
    )
    excluded = review.get("excluded_signals") or {}
    assert "honesty_accuracy" in excluded, (
        "an n=1 honesty score is refused by autonomy_scorer and "
        "operating_modes; the consumer that WRITES to mastery must refuse it "
        f"too. excluded={excluded}")
    assert "1" in str(excluded["honesty_accuracy"]), (
        "the exclusion reason must carry the sample size, so 'could not "
        f"measure' is distinguishable from 'measured zero': {excluded}")


@pytest.mark.asyncio
async def test_well_sampled_honesty_is_used():
    review, _lift, _sj = await _run(
        headline=0.822,
        honesty=_HEALTHY_HONESTY,
        adversarial=0.802,
        mistakes=5,
        audit_entries=100,
        eval_pass_rate=0.333,
    )
    assert "honesty_accuracy" not in (review.get("excluded_signals") or {})
    est = review["estimated_accuracy"]
    assert abs(est - (0.40 + 0.802 + 0.95 + 0.333) / 4) < 0.002, est


# ── 3. the read path must not write ────────────────────────────────────────

@pytest.mark.asyncio
async def test_read_path_does_not_mutate_mastery():
    """GET /api/aria/calibration/review runs this. It must not move mastery."""
    review, lift, _sj = await _run(
        headline=0.95,
        honesty=_HEALTHY_HONESTY,   # 0.40, well sampled
        adversarial=0.30,
        mistakes=5,
        audit_entries=100,
        eval_pass_rate=0.20,
    )
    assert review["calibration_status"] == "overconfident", review
    lift.assert_not_awaited()
    applied = review.get("correction_applied") or {}
    assert applied.get("applied") is False, (
        "the read path must say plainly that it did not correct, rather than "
        f"omitting the field: {applied}")


@pytest.mark.asyncio
async def test_explicit_correction_still_lowers_mastery():
    """The capability is relocated, not deleted."""
    review, lift, _sj = await _run(
        headline=0.95,
        honesty=_HEALTHY_HONESTY,
        adversarial=0.30,
        mistakes=5,
        audit_entries=100,
        eval_pass_rate=0.20,
        apply_correction=True,
    )
    lift.assert_awaited_once()
    bump = lift.await_args.args[0] if lift.await_args.args else None
    assert bump is not None and bump < 0, (
        f"overconfident must drop mastery, got bump={bump}")
    assert abs(bump) <= 0.03 + 1e-9, f"drop cap is 3pp per run, got {bump}"


@pytest.mark.asyncio
async def test_correction_is_not_applied_on_artifact_only_input():
    """The live shape: both artifact signals excluded leaves too little to act
    on, so nothing writes to mastery even when correction is requested."""
    review, lift, _sj = await _run(
        headline=0.822,
        honesty=_LIVE_HONESTY_N1,   # excluded (n=1)
        adversarial=None,           # no run
        mistakes=2888,
        audit_entries=1208,         # excluded (rate > 1)
        eval_pass_rate=None,        # no eval
        apply_correction=True,
    )
    assert review["calibration_status"] == "insufficient_data", review
    assert review["estimated_accuracy"] is None
    lift.assert_not_awaited()


# ── 4. the durable record must say whether it wrote ────────────────────────

@pytest.mark.asyncio
async def test_persisted_review_records_whether_mastery_was_mutated():
    review, lift, set_json = await _run(
        headline=0.95,
        honesty=_HEALTHY_HONESTY,
        adversarial=0.30,
        mistakes=5,
        audit_entries=100,
        eval_pass_rate=0.20,
        apply_correction=True,
    )
    lift.assert_awaited_once()
    persisted = None
    for call in set_json.await_args_list:
        key = call.args[0] if call.args else call.kwargs.get("key")
        if key == "crucix:calibration:review":
            persisted = call.args[1] if len(call.args) > 1 else call.kwargs.get("obj")
    assert persisted is not None, "the review was never persisted"
    assert "correction_applied" in persisted, (
        "rs.set_json ran BEFORE the correction block, so the durable record "
        "could never say whether mastery was mutated — the one field an audit "
        f"of C-112 would want. persisted keys: {sorted(persisted)}")
    assert persisted["correction_applied"].get("applied") is True
