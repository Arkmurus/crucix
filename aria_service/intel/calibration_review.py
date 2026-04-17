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


async def run_calibration_review() -> dict:
    """Run a full calibration review comparing mastery to ground truth."""

    # 1. Get mastery scores
    mastery_scores = {}
    try:
        from . import student
        report = await student.get_mastery_report()
        for topic, data in report.get("topics", {}).items():
            mastery_scores[topic] = data.get("score", 0.5)
        overall_mastery = report.get("overall_mastery", 0.5)
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

    # 4. Get mistake count (ground truth for error rate)
    mistake_rate = None
    try:
        from . import mistake_ledger as ml
        from . import redis_store as rs
        total_mistakes = await rs.llen("crucix:mistake_ledger:log") or 0
        # Approximate accuracy = 1 - (mistakes / total_interactions)
        # Use mastery samples as proxy for total interactions
        total_samples = sum(
            d.get("samples", 0)
            for d in (await student.get_mastery_report()).get("topics", {}).values()
        )
        if total_samples > 0:
            mistake_rate = total_mistakes / max(total_samples, 1)
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
        "recommendation": _get_recommendation(calibration_delta, calibration_status),
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

    return review


def _get_recommendation(delta: float | None, status: str) -> str:
    if delta is None or status == "insufficient_data":
        return ("Insufficient ground truth data for calibration. Run adversarial "
                "audit and accumulate honesty judge verdicts before calibrating.")
    if status == "well_calibrated":
        return "Mastery scores are well-calibrated. No threshold changes needed."
    if status == "acceptable":
        return (f"Mastery is {'over' if delta > 0 else 'under'}confident by "
                f"{abs(delta):.0%}. Monitor — no immediate action needed.")
    if status == "overconfident":
        return (f"MASTERY IS OVERCONFIDENT by {delta:.0%}. A mastery score of "
                f"{0.88:.0%} is predicting only {0.88 - delta:.0%} actual accuracy. "
                f"Consider lowering WEAK_THRESHOLD or increasing source requirements "
                f"at current mastery levels.")
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
