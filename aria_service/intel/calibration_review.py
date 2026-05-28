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
# R-F166 (2026-05-11): bidirectional correction. Downward path is gated
# tighter so honest gaps stay surfaced for at least a few cycles before
# mastery follows reality down — but the gap eventually CAN close.
_CORRECT_DROP_THRESHOLD = 0.15  # only drop when |delta| > 15pp
_CORRECT_DROP_CAP       = 0.03  # never drop more than 3pp in one run


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
        # R-F199 (2026-05-11): skip degraded runs. When LLM was dead,
        # the run records degraded=True; feeding overall_score=0.0 into
        # calibration would collapse mastery via R-F166 in ~30h. The
        # signal isn't a real adversarial result — it's an outage echo.
        if last.get("degraded") or last.get("invalid"):
            # R-F706 (2026-05-18): demoted WARNING→INFO. Same pattern as
            # R-F681 (Anthropic billing cooldown): when a fallback is
            # working and the degraded signal is being correctly skipped,
            # this is operational, not degraded — fires every dashboard
            # poll (~every 60s) and was the loudest WARNING on the
            # operator surface. Skipping degraded runs is the *correct*
            # behavior; logging at INFO keeps the audit trail without
            # mirroring into the error ledger.
            logger.info(
                "[calibration] R-F199 — skipping adversarial signal: "
                "last run is degraded (%s)",
                last.get("invalid_reason") or "empty-response cluster",
            )
        else:
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

    # R-F169 (2026-05-11): pull eval_runner's most-recent pass_rate
    # into the ground-truth signal mean. Pre-R-F169, the 341-Q golden-
    # seed (R-F148) had zero feedback influence on calibration — the
    # biggest investment in measured accuracy was effectively decorative.
    # Now the most-recent pass_rate becomes a 4th signal alongside
    # honesty / adversarial / (1 - mistake_rate).
    eval_pass_rate = None
    try:
        from . import eval_runner as _er
        runs = await _er.get_recent_runs(limit=1)
        if runs:
            last_run = runs[0] if isinstance(runs[0], dict) else None
            if last_run:
                _summary = last_run.get("summary") or last_run
                _pr = _summary.get("pass_rate")
                # R-F199 (2026-05-11): skip degraded eval runs (R-F197).
                # Same reasoning as the adversarial-skip above — an empty-
                # response run produces pass_rate=0 which is an outage
                # signal, not a learning signal.
                if _summary.get("degraded"):
                    # R-F706 (2026-05-18): demoted WARNING→INFO — see
                    # adversarial-skip comment above.
                    logger.info(
                        "[calibration] R-F199 — skipping eval signal: "
                        "last run is degraded (%d/%d empty responses)",
                        _summary.get("empty_response_count", 0),
                        _summary.get("total", 0),
                    )
                elif isinstance(_pr, (int, float)) and 0.0 <= float(_pr) <= 1.0:
                    eval_pass_rate = float(_pr)
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
    if eval_pass_rate is not None:
        ground_truth_signals.append(eval_pass_rate)

    # R-F199 (2026-05-11): require ≥2 valid signals before computing
    # an accuracy estimate. With only 1 signal a single outage / data-
    # source failure can drive the entire calibration loop. Two signals
    # provide cross-check; if there aren't two we mark insufficient_data
    # rather than committing to a one-source estimate.
    estimated_accuracy = (
        sum(ground_truth_signals) / len(ground_truth_signals)
        if len(ground_truth_signals) >= 2 else None
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

    # R-F208 (2026-05-11) — `if x else None` collapses legitimate 0.0
    # values to None. A real 0% honesty score / 0% adversarial / 0
    # mistake rate would render as missing data on the dashboard.
    # Use `if x is not None` (the canonical form already used for
    # eval_pass_rate at R-F169) everywhere.
    review = {
        "overall_mastery": round(overall_mastery, 4),
        "estimated_accuracy": round(estimated_accuracy, 4) if estimated_accuracy is not None else None,
        "calibration_delta": calibration_delta,
        "calibration_status": calibration_status,
        "signals": {
            "honesty_accuracy": round(honesty_accuracy, 4) if honesty_accuracy is not None else None,
            "adversarial_accuracy": round(adversarial_accuracy, 4) if adversarial_accuracy is not None else None,
            "mistake_rate": round(mistake_rate, 4) if mistake_rate is not None else None,
            # R-F169 — surface the eval pass_rate so the dashboard /
            # operator can see which signal is pulling the average.
            "eval_pass_rate": round(eval_pass_rate, 4) if eval_pass_rate is not None else None,
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

    # ── Self-calibration correction (R-F166 2026-05-11 — bidirectional) ──
    # Original (pre-R-F166): only fired for UNDERCONFIDENT. Result: when
    # mastery sat at 88% while accuracy was 45% (the production case
    # observed for weeks), nothing happened. The 42pp gap was visible on
    # the dashboard but the loop had no mechanism to close it.
    #
    # R-F166 makes the correction symmetric:
    #   * Underconfident → lift up (existing behaviour, capped 8pp/run)
    #   * Overconfident  → drop down (new path, capped 3pp/run, gate
    #     |delta| > _CORRECT_DROP_THRESHOLD = 15pp, never below 10% floor)
    #
    # The downward cap is tighter (3pp vs 8pp) so honest gaps stay
    # surfaced for at least a few cycles before mastery follows reality
    # down. Rate-limited to once per hour so dashboard polling doesn't
    # compound.
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

    # ── R-F166 (2026-05-11) — overconfident downward correction ──
    elif (
        calibration_status == "overconfident"
        and calibration_delta is not None
        and calibration_delta > _CORRECT_DROP_THRESHOLD
        and estimated_accuracy is not None
    ):
        try:
            from . import redis_store as rs
            now_ts = time.time()
            last = await rs.get(_K_LAST_CORRECTION)
            last_ts = float(last) if last is not None else 0.0
            if now_ts - last_ts >= _CORRECT_COOLDOWN:
                gap = calibration_delta
                drop = min(gap * _CORRECT_FRACTION, _CORRECT_DROP_CAP)
                from . import student
                new_scores = await student.lift_all_topics(-drop)
                await rs.set(_K_LAST_CORRECTION, str(now_ts))
                review["correction_applied"] = {
                    "drop_pp": round(drop * 100, 2),
                    "gap_before_pp": round(gap * 100, 2),
                    "topics_lowered": len(new_scores or {}),
                    "reason": "overconfident — self-assessed mastery materially above ground-truth accuracy",
                    "direction": "down",
                }
                logger.warning(
                    "[calibration] OVERCONFIDENT by %.1fpp — lowered mastery on %d topics by -%.1fpp (R-F166)",
                    gap * 100, len(new_scores or {}), drop * 100,
                )
                try:
                    from . import brain_hook as _bh3
                    await _bh3.absorb(
                        module="calibration_review",
                        summary=(
                            f"Self-calibration: lowered mastery on {len(new_scores or {})} topics "
                            f"by -{drop * 100:.1f}pp (was overconfident by {gap * 100:.1f}pp). "
                            f"R-F166 — bidirectional correction now active."
                        ),
                        success=True,
                    )
                except Exception:
                    pass
            else:
                review["correction_applied"] = {
                    "drop_pp": 0.0,
                    "skipped": "cooldown",
                    "minutes_until_next": int((_CORRECT_COOLDOWN - (now_ts - last_ts)) / 60),
                    "direction": "down",
                }
        except Exception as exc:
            logger.warning("R-F166 overconfident correction failed: %s", exc)
            review["correction_applied"] = {"error": str(exc)[:160], "direction": "down"}

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
