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
from .engine_wiring import wire_failure

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

# ── R-F4066 (C-112) — a signal we cannot measure must be EXCLUDED, never
# contributed to the mean as a zero. Two sibling consumers of the same inputs
# already apply this floor and this module — the only one with WRITE AUTHORITY
# over mastery — did not:
#     autonomy_scorer._MIN_SIGNAL_SAMPLES = 5   (R-F1907)
#     operating_modes.GROUNDED_MIN_SAMPLES      (R-F3764)
# Kept numerically identical to autonomy_scorer's so the two cannot drift into
# disagreeing about whether the same reading is evidence.
_MIN_SIGNAL_SAMPLES = 5


async def run_calibration_review(apply_correction: bool = False) -> dict:
    """Run a full calibration review comparing mastery to ground truth.

    ``apply_correction`` — R-F4066 (C-112). This function both COMPUTES the
    review and, historically, MUTATED mastery via ``student.lift_all_topics``.
    Its only production callers were ``GET /api/aria/calibration/review`` (which
    the brain-dashboard aggregate polls) and ``save_baseline()``; a repo-wide
    search found no scheduled caller at all. So the operator opening the command
    centre was what drove the hourly mastery correction — the module comment
    above even rate-limits "so rapid dashboard refreshes don't compound",
    acknowledging the driver rather than removing it.

    The correction is a wanted capability, so it is relocated rather than
    deleted: the scheduled ``ecosystem_reassess`` task passes ``True`` (it
    already owns the other periodic evaluations — operating mode, composite
    score), and every read path keeps the default. **A GET must not move a
    score.**

    R-F4085 (C-132): that task runs every 6 HOURS, not hourly — its id says
    HOURLY and its cron says ``0 */6 * * *``. So ``_CORRECT_COOLDOWN`` (3600s)
    is no longer the binding constraint; the schedule is. That is safe (fewer
    corrections, never more) and the cooldown stays as the floor if a second
    scheduled caller is ever added — but do not read 'hourly' here as a
    statement about how often mastery actually moves.
    """

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
        pass
        overall_mastery = 0.5

    # R-F4066 (C-112) — signals we could not measure, and why. This is the
    # difference between "measured zero" and "no measurement", and it is
    # load-bearing: everything in here is kept OUT of the ground-truth mean and
    # is still reported, so an operator can see what was dropped instead of
    # inferring it from a number that moved.
    excluded_signals: dict[str, str] = {}

    # 2. Get honesty judge accuracy (ground truth)
    #
    # Two names on purpose: `*_observed` is WHAT WE READ and is always reported;
    # `honesty_accuracy` is what enters the ground-truth mean and is None when
    # the reading is not evidence. Collapsing them is how a value that was
    # deliberately discarded reappears as a measurement.
    honesty_accuracy = None
    honesty_observed = None
    try:
        from . import honesty_judge
        stats = await honesty_judge.get_honesty_stats()
        honesty_accuracy = stats.get("avg_honesty_score")
        # R-F4066 — `scored_sample_size` is co-computed with `avg_honesty_score`
        # in get_honesty_stats (both are the 24h "ok"-with-a-score population),
        # so it describes the SAME window as the value — the R-F3696 property
        # that made the equivalent guard safe in autonomy_scorer. Live
        # 2026-08-16: avg 0.0 from scored_sample_size 1, against a lifetime
        # 0.236. One judged turn was setting 25% of ARIA's "ground truth".
        _h_n = stats.get("scored_sample_size")
        _h_n = int(_h_n or 0)
        honesty_observed = honesty_accuracy
        if honesty_accuracy is not None and _h_n < _MIN_SIGNAL_SAMPLES:
            excluded_signals["honesty_accuracy"] = (
                f"insufficient_samples_n{_h_n} (floor {_MIN_SIGNAL_SAMPLES})")
            honesty_accuracy = None
    except Exception:
        pass
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
            # R-F1165 — fall back to the most recent NON-degraded run
            # so calibration doesn't go stale during transient LLM blips.
            runs = adv.get("runs") or []
            fallback = None
            for r in runs:
                if not r.get("degraded") and not r.get("invalid"):
                    fallback = r
                    break
            if fallback is not None:
                adversarial_accuracy = fallback.get("overall_score")
                logger.info(
                    "[calibration] R-F199 — latest adversarial run degraded, "
                    "falling back to run from %s (score=%s)",
                    fallback.get("run_at", "?")[:19],
                    adversarial_accuracy,
                )
            else:
                logger.info(
                    "[calibration] R-F199 — skipping adversarial signal: "
                    "last run is degraded (%s) and no fallback available",
                    last.get("invalid_reason") or "empty-response cluster",
                )
        else:
            adversarial_accuracy = last.get("overall_score")
    except Exception:
        pass
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
    #
    # ── R-F4066 (C-112) — a ratio above 1.0 is not a rate, it is proof the two
    # populations do not match, and it must not become a measured zero.
    #
    # Measured live 2026-08-16: 2888 mistake-ledger rows / 1208 chat-audit rows
    # = 2.3907. The numerator spans every module the ledger serves (autonomous
    # tasks, source_validator, verified_intel, web_atlas — see the reason codes
    # at the top of mistake_ledger.py), not just chat; and the denominator is a
    # log that has itself lost 37% of its entries (C-111). `1.0 - min(rate, 1.0)`
    # then clamped that to a flat **0.0** and averaged it in as if ARIA had been
    # measured at zero accuracy — one quarter of the "ground truth" headline,
    # manufactured.
    #
    # The honest denominator for this ledger does not exist today, so the fix is
    # NOT to invent one: an out-of-range ratio is reported and excluded. Do not
    # "simplify" this to `min(rate, 1.0)` — that is the original defect, and it
    # asserts a measurement nobody made.
    mistake_rate = None
    mistake_rate_observed = None
    try:
        from . import chat_audit_log as cal
        from . import redis_store as rs
        total_mistakes = await rs.llen("crucix:mistake_ledger:log") or 0
        cal_stats = await cal.get_stats()
        total_interactions = (cal_stats or {}).get("total_entries", 0)
        if total_interactions > 0:
            mistake_rate_observed = total_mistakes / total_interactions
            if mistake_rate_observed > 1.0:
                excluded_signals["mistake_rate"] = (
                    f"population_mismatch_rate_{mistake_rate_observed:.2f}"
                    f" ({total_mistakes} ledger entries over "
                    f"{total_interactions} chat-audit entries — a rate above 1.0"
                    " means the numerator and denominator do not describe the"
                    " same population)"
                )
            else:
                mistake_rate = mistake_rate_observed
        else:
            excluded_signals["mistake_rate"] = "no_denominator_zero_interactions"
    except Exception:
        pass
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
            # R-F4066 — these report WHAT WAS READ, including readings that were
            # excluded from the mean. `excluded_signals` below says which ones
            # and why. Reporting only the surviving signals would hide the
            # artifact that motivated this fix.
            "honesty_accuracy": round(honesty_observed, 4) if honesty_observed is not None else None,
            "adversarial_accuracy": round(adversarial_accuracy, 4) if adversarial_accuracy is not None else None,
            "mistake_rate": round(mistake_rate_observed, 4) if mistake_rate_observed is not None else None,
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
        # R-F4066 — {signal: reason} for every reading kept out of the mean.
        # Empty dict means "everything read was used", which is different from
        # the key being absent (an older payload that predates this field).
        "excluded_signals": excluded_signals,
        # R-F4066 — always present, and always decided BEFORE the correction
        # block runs, so the shape does not depend on which branch fires.
        # `applied: False` on a read is a statement, not an omission.
        "correction_applied": {"applied": False, "reason": (
            "read_only_call" if not apply_correction
            else "no_correction_warranted")},
    }

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
    # down.
    #
    # R-F4066 (C-112) — the hourly rate-limit used to read "so dashboard polling
    # doesn't compound", which conceded that DASHBOARD POLLING WAS THE CLOCK.
    # It is not any more: `apply_correction` is False on every read path, and
    # the hourly ecosystem_reassess task is the caller that passes True. The
    # cooldown stays as a belt-and-braces guard against a second scheduled
    # caller ever being added.
    if (
        apply_correction
        and calibration_status == "underconfident"
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
                    "applied": True,
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
                    "applied": False,
                    "bump_pp": 0.0,
                    "skipped": "cooldown",
                    "minutes_until_next": int((_CORRECT_COOLDOWN - (now_ts - last_ts)) / 60),
                }
        except Exception as exc:
            logger.warning("self-calibration correction failed: %s", exc)
            review["correction_applied"] = {"applied": False,
                                            "error": str(exc)[:160]}

    # ── R-F166 (2026-05-11) — overconfident downward correction ──
    elif (
        apply_correction
        and calibration_status == "overconfident"
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
                    "applied": True,
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
                    "applied": False,
                    "drop_pp": 0.0,
                    "skipped": "cooldown",
                    "minutes_until_next": int((_CORRECT_COOLDOWN - (now_ts - last_ts)) / 60),
                    "direction": "down",
                }
        except Exception as exc:
            logger.warning("R-F166 overconfident correction failed: %s", exc)
            review["correction_applied"] = {"applied": False,
                                            "error": str(exc)[:160],
                                            "direction": "down"}

    # ── Persist ──────────────────────────────────────────────────────────────
    # R-F4066 (C-112) — this used to run BEFORE the correction block, so
    # `correction_applied` was only ever on the returned object and never on the
    # durable record. The stored review therefore could not answer the one
    # question an audit of this module asks: did this run move mastery? Live
    # 2026-08-16 the persisted record read `correction_applied: None` at the
    # same instant the API response carried a real correction verdict.
    # Persisting here means the record includes the outcome of the write it
    # describes. Keep it last.
    try:
        from . import redis_store as rs
        await rs.set_json(_K_REVIEW, review, ex=30 * 86400)
    except Exception:
        pass
        pass

    # R-F1304 — wire to brain (§21a)
    #
    # R-F4066 — `{estimated_accuracy:.0%}` raises TypeError when the estimate is
    # None, and the whole block is wrapped in `except Exception: pass`, so on the
    # `insufficient_data` path this module's ONLY success wire silently never
    # fired. That is a dark branch by §21a, and this change makes the path it
    # affects more common: excluding an unmeasurable signal is exactly what
    # produces insufficient_data. Format defensively and report the reason, so
    # "could not calibrate" reaches the brain as an observation rather than as
    # nothing at all.
    try:
        from .engine_wiring import wire_success, wire_failure
        _acc = (f"{estimated_accuracy:.0%}" if estimated_accuracy is not None
                else "unmeasured")
        _excl = (f"; excluded: {', '.join(sorted(excluded_signals))}"
                 if excluded_signals else "")
        wire_success(
            module="calibration_review",
            summary=(f"Calibration review: {calibration_status} "
                     f"(mastery {overall_mastery:.0%}, accuracy {_acc}"
                     f"{_excl})"),
            source_id="calibration_review:run_calibration_review",
        )
    except Exception:
        pass
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
        pass
    return review


async def get_baseline() -> dict | None:
    """Return saved baseline."""
    try:
        from . import redis_store as rs
        return await rs.get_json(_K_BASELINE)
    except Exception:
        pass


        return None

# R-F2538: R-F2119 import-time wire_failure("module shutdown") block removed — it fired a FALSE engine_failure gap on every import (not at shutdown); do not re-add.
