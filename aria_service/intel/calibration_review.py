"""Calibration review — does mastery score actually predict accuracy?

Compares self-assessed mastery against ground truth outcomes:
  - If mastery says 88%, is ARIA actually right 88% of the time?
  - If mastery says 52% on a topic, is accuracy actually ~52%?

Data sources:
  - student.py mastery scores (self-assessed via EWMA)
  - honesty_judge verdicts (ground truth: was [CONFIRMED] actually supported?)
  - adversarial_challenge results (ground truth: did ARIA catch the attack?)
  - mistake_ledger entries (ground truth: user corrections)

Output: calibration delta per topic and overall
  - delta > 0: ARIA is overconfident (mastery higher than accuracy)
  - delta < 0: ARIA is underconfident (mastery lower than accuracy)
  - |delta| > 0.15: needs threshold recalibration

Redis key:
  crucix:calibration:review       — latest review
  crucix:calibration:baseline     — Week 4 baseline
"""
from __future__ import annotations

import logging
import time

logger = logging.getLogger("aria.calibration_review")

_K_REVIEW = "crucix:calibration:review"
_K_BASELINE = "crucix:calibration:baseline"
_K_LAST_CORRECTION = "crucix:calibration:last_correction"

# Self-calibration correction parameters (2026-04-17):
# When the calibration loop finds ARIA is UNDERCONFIDENT by more than
# _CORRECT_THRESHOLD, lift her stored mastery toward ground truth by
# _CORRECT_FRACTION of the gap, capped at _CORRECT_CAP per run.
# Runs at most once per _CORRECT_COOLDOWN so rapid dashboard refreshes
# don't compound into an overshoot.
_CORRECT_THRESHOLD = 0.10   # only act when |delta| > 10pp
_CORRECT_FRACTION  = 0.50   # close 50% of the gap per correction
_CORRECT_CAP       = 0.08   # never lift more than 8pp in one run
_CORRECT_COOLDOWN  = 3600   # seconds — once per hour max


async def run_calibration_review() -> dict:
    """Run a full calibration review comparing mastery to ground truth."""

    # 1. Get mastery scores. Use `headline_mastery` (= min(overall, core) per
    # the 0150187 honest-rollup doctrine) instead of `overall_mastery`
    # (sample-weighted across all topics). The dashboard, /quality endpoint,
    # capability_card, and self_assess briefing all use headline -- calibration
    # was the outlier reading the inflated number, which was producing false
    # "overconfident by 42%" alerts (live 2026-04-27: overall=0.925 vs
    # headline=~0.60, because the core_mastery cluster has 6 tags stuck at
    # the 0.491 floor that pulls the honest headline down).
    mastery_scores = {}
    try:
        from . import student
        report = await student.get_mastery_report()
        for topic, data in report.get("topics", {}).items():
            mastery_scores[topic] = data.get("score", 0.5)
        overall_mastery = (
            report.get("headline_mastery")
            or report.get("overall_mastery", 0.5)
        )
    except Exception:
        overall_mastery = 0.5

    # 2. Get honesty judge accuracy (ground truth)
    honesty_accuracy = None
    try:
        from . import honesty_judge
        stats = await honesty_judge.get_honesty_stats()
        honesty_accuracy = stats.get("avg_honesty_score")
    except Exception:
        pass

    # 3. Get adversarial score (ground truth for manipulation resistance)
    adversarial_accuracy = None
    try:
        from . import adversarial_challenge as ac
        adv = await ac.stats()
        last = adv.get("last_run") or {}
        adversarial_accuracy = last.get("overall_score")
    except Exception:
        pass

    # 4. Get mistake count (ground truth for error rate).
    # The denominator is REAL chat interactions (chat_audit_log entries),
    # not "mastery quiz samples". The previous implementation used quiz
    # samples as a proxy -- but quiz samples come from ARIA quizzing
    # herself in autonomous loops, which can run thousands of times per
    # day. With ~5000 quiz samples and ~5 mistakes the rate became
    # 0.001, and `1 - mistake_rate ≈ 0.999` artificially pulled
    # estimated_accuracy upward (toward "ARIA is ~100% accurate").
    # The honest denominator is chat turns served, where user-facing
    # output mistakes actually matter.
    mistake_rate = None
    try:
        from . import chat_audit_log as cal
        from . import redis_store as rs
        total_mistakes = await rs.llen("crucix:mistake_ledger:log") or 0
        cal_stats = await cal.get_stats()
        total_interactions = (cal_stats or {}).get("total_entries", 0)
        if total_interactions > 0:
            mistake_rate = total_mistakes / total_interactions
    except Exception:
        pass

    # 5. Compute calibration deltas
    ground_truth_signals = []
    if honesty_accuracy is not None:
        ground_truth_signals.append(honesty_accuracy)
    if adversarial_accuracy is not None:
        ground_truth_signals.append(adversarial_accuracy)
    if mistake_rate is not None:
        ground_truth_signals.append(1.0 - min(mistake_rate, 1.0))

    estimated_accuracy = (
        sum(ground_truth_signals) / len(ground_truth_signals)
        if ground_truth_signals else None
    )

    calibration_delta = None
    calibration_status = "insufficient_data"
    if estimated_accuracy is not None:
        calibration_delta = round(overall_mastery - estimated_accuracy, 4)
        if abs(calibration_delta) <= 0.05:
            calibration_status = "well_calibrated"
        elif abs(calibration_delta) <= 0.15:
            calibration_status = "acceptable"
        elif calibration_delta > 0.15:
            calibration_status = "overconfident"
        else:
            calibration_status = "underconfident"

    review = {
        "overall_mastery": round(overall_mastery, 4),
        "estimated_accuracy": round(estimated_accuracy, 4) if estimated_accuracy else None,
        "calibration_delta": calibration_delta,
        "calibration_status": calibration_status,
        "signals": {
            "honesty_accuracy": round(honesty_accuracy, 4) if honesty_accuracy else None,
            "adversarial_accuracy": round(adversarial_accuracy, 4) if adversarial_accuracy else None,
            "mistake_rate": round(mistake_rate, 4) if mistake_rate else None,
        },
        "mastery_per_topic": {k: round(v, 4) for k, v in mastery_scores.items()},
        "recommendation": _get_recommendation(
            calibration_delta, calibration_status,
            mastery=overall_mastery,
            estimated=estimated_accuracy,
        ),
        "reviewed_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }

    # Persist
    try:
        from . import redis_store as rs
        await rs.set_json(_K_REVIEW, review, ex=30 * 86400)
    except Exception:
        pass

    # Signal brain if calibration is off
    if calibration_status in ("overconfident", "underconfident"):
        try:
            from . import brain_hook as _bh
            await _bh.absorb(
                module="calibration_review",
                summary=f"Calibration {calibration_status}: mastery {overall_mastery:.0%} vs accuracy {estimated_accuracy:.0%} (delta {calibration_delta:+.0%})",
                detail=review["recommendation"],
                success=False,
                gap_type="adversarial_critical_failure" if calibration_status == "overconfident" else None,
                gap_detail=f"Mastery scores {calibration_status} by {abs(calibration_delta):.0%}",
            )
        except Exception:
            pass

    # ── Self-calibration correction ──
    # When ground-truth accuracy is consistently higher than self-assessed
    # mastery, pull mastery toward reality. Only fires for UNDERCONFIDENT
    # (pulling mastery UP is safe — reality says she's better). Never
    # fires for overconfident because that would mask real gaps.
    # Rate-limited to once per hour so dashboard polling doesn't compound.
    if (
        calibration_status == "underconfident"
        and calibration_delta is not None
        and abs(calibration_delta) > _CORRECT_THRESHOLD
        and estimated_accuracy is not None
    ):
        try:
            from . import redis_store as rs
            now_ts = time.time()
            last = await rs.get(_K_LAST_CORRECTION)
            last_ts = float(last) if last is not None else 0.0
            if now_ts - last_ts >= _CORRECT_COOLDOWN:
                gap = abs(calibration_delta)
                bump = min(gap * _CORRECT_FRACTION, _CORRECT_CAP)

                from . import student
                new_scores = await student.lift_all_topics(bump)
                await rs.set(_K_LAST_CORRECTION, str(now_ts))
                review["correction_applied"] = {
                    "bump_pp": round(bump * 100, 2),
                    "gap_before_pp": round(gap * 100, 2),
                    "topics_lifted": len(new_scores or {}),
                    "reason": "underconfident — ground truth consistently higher than self-assessed",
                }
                logger.warning(
                    "[calibration] UNDERCONFIDENT by %.1fpp — lifted mastery on %d topics by +%.1fpp",
                    gap * 100, len(new_scores or {}), bump * 100,
                )
                # Feed the correction into brain_hook so it's visible
                try:
                    from . import brain_hook as _bh2
                    await _bh2.absorb(
                        module="calibration_review",
                        summary=f"Self-calibration: lifted mastery on {len(new_scores or {})} topics by +{bump*100:.1f}pp (was underconfident by {gap*100:.1f}pp)",
                        success=True,
                    )
                except Exception:
                    pass
            else:
                review["correction_applied"] = {
                    "bump_pp": 0.0,
                    "skipped": "cooldown",
                    "minutes_until_next": int((_CORRECT_COOLDOWN - (now_ts - last_ts)) / 60),
                }
        except Exception as exc:
            logger.warning("self-calibration correction failed: %s", exc)
            review["correction_applied"] = {"error": str(exc)[:160]}

    return review


def _get_recommendation(
    delta: float | None,
    status: str,
    mastery: float | None = None,
    estimated: float | None = None,
) -> str:
    if delta is None or status == "insufficient_data":
        return ("Insufficient ground truth data for calibration. Run adversarial "
                "audit and accumulate honesty judge verdicts before calibrating.")
    if status == "well_calibrated":
        return "Mastery scores are well-calibrated. No threshold changes needed."
    if status == "acceptable":
        return (f"Mastery is {'over' if delta > 0 else 'under'}confident by "
                f"{abs(delta):.0%}. Monitor — no immediate action needed.")
    if status == "overconfident":
        # Use the actual values rather than a hardcoded 0.88 placeholder.
        # The previous version printed "A mastery score of 88% is predicting
        # only 46%" REGARDLESS of what the real mastery / estimated_accuracy
        # were, making the recommendation non-actionable for triage.
        m = mastery if mastery is not None else 0.0
        e = estimated if estimated is not None else max(0.0, m - delta)
        return (f"MASTERY IS OVERCONFIDENT by {delta:.0%}. A headline mastery "
                f"of {m:.0%} is predicting only {e:.0%} actual accuracy. "
                f"Consider lowering WEAK_THRESHOLD or increasing source "
                f"requirements at current mastery levels.")
    if status == "underconfident":
        return (f"Mastery is underconfident by {abs(delta):.0%}. ARIA is better "
                f"than she thinks. Consider raising mastery weights on brain_hook "
                f"signals to give more credit for successful outputs.")
    return "Unknown calibration status."


async def save_baseline() -> dict:
    """Save current calibration as Week 4 baseline."""
    review = await run_calibration_review()
    try:
        from . import redis_store as rs
        baseline = {
            "review": review,
            "saved_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "description": "Week 4 calibration baseline — first honest measurement",
        }
        await rs.set_json(_K_BASELINE, baseline, ex=365 * 86400)
    except Exception:
        pass
    return review


async def get_baseline() -> dict | None:
    """Return saved baseline."""
    try:
        from . import redis_store as rs
        return await rs.get_json(_K_BASELINE)
    except Exception:
        return None
