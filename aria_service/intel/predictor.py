"""ARIA Predictor — forecast likely failure axes BEFORE a task runs.

A predicted gap is a closed gap. Before every DD run (or any other major
autonomous task), ARIA asks the predictor:

  "Given task_type=dd, domain=PL, entity_type=company — where am I most
   likely to be wrong, blind, or slow? What should I pre-fetch?"

The answer draws from three data sources that already exist:

  1. self_metrics rollup — historical scores per axis×domain. If coverage
     on PL registry lookups has averaged 0.6 over the last week, expect
     coverage to be the weak axis again.
  2. mistake_ledger.lookup_similar — past mistakes on task_type×domain.
     If the last 3 DD runs on PL companies surfaced user corrections on
     sanctions coverage, that's a strong signal to pre-fetch more.
  3. capability_manifest — does she even have an adapter / source for
     this domain? If not, coverage is guaranteed to be bad; surface it
     early so the caller can fall back to LLM research.

This module is deliberately a pure-ish function (async only because its
data sources are async). No LLM calls. No network. Bounded-time. Safe to
call on every task without cost.

Output shape
────────────
  {
    task_type, domain,
    overall_confidence: 0..1,       # composite forecast — high = clean run expected
    likely_failures: [
      {axis, reason, forecast_score, remediation, source}
    ],
    past_mistakes: [                # top 3 from mistake_ledger
      {mistake_id, what_class, severity, what, fix, prevented_count}
    ],
    recommendations: [              # concrete actions the caller can take
      "Pre-fetch OpenSanctions for the entity before adapter call",
      ...
    ],
    generated_at: iso,
  }

Governed by the autonomy doctrine: the predictor emits advice, the
caller decides. It never takes an action itself.

Public API
──────────
  await forecast(task_type, domain, *, entity_type=None, context=None) -> dict
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Optional

logger = logging.getLogger("aria.predictor")

_WEAK_SCORE = 0.7         # below this → likely failure axis
_STRONG_SCORE = 0.85      # above this → confidence boost
_MISTAKES_LOOKBACK = 5


def _remediation_for(axis: str, domain: str, score: float | None) -> str:
    """Pick a concrete remediation hint per axis. Deliberately specific —
    vague hints don't help the caller, specific ones do."""
    if axis == "coverage":
        return (f"Pre-fetch OpenSanctions + OpenCorporates for {domain} before "
                f"adapter call; have LLM research fallback ready if registry returns empty.")
    if axis == "accuracy":
        return (f"Run weakest-confidence gate; require ≥2 independent sources "
                f"before any CONFIRMED finding in {domain}.")
    if axis == "timeliness":
        return (f"Increase timeout for {domain} network calls; fetch in "
                f"parallel where possible; fail soft to cached data.")
    if axis == "calibration":
        return (f"Downgrade declared confidence by one tier in {domain} "
                f"(CONFIRMED→PROBABLE, etc.) until calibration stabilises.")
    if axis == "utility":
        return (f"Surface findings in the briefing with explicit next-action — "
                f"past {domain} outputs had low team pickup.")
    return "Review rollup and adjust before proceeding."


async def forecast(
    task_type: str,
    domain: str,
    *,
    entity_type: Optional[str] = None,
    context: Optional[dict] = None,
) -> dict:
    """Pre-task forecast. Call at the top of any significant autonomous
    operation. Returns a structured forecast + recommendations.

    Never raises — if any data source is down, falls back to a degraded
    forecast (marked with `degraded: True`). The caller should still
    proceed; a missing predictor is never a reason to block work.
    """
    tt = (task_type or "").lower()
    dom = (domain or "unknown").lower()
    report: dict[str, Any] = {
        "task_type": tt,
        "domain": dom,
        "entity_type": entity_type,
        "likely_failures": [],
        "past_mistakes": [],
        "recommendations": [],
        "overall_confidence": 0.5,
        "degraded": False,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }

    # ── 1. self_metrics rollup — where has this domain been weak? ──
    axis_scores: dict[str, float] = {}
    try:
        from . import self_metrics
        roll = await self_metrics.rollup(window_days=14, domain=dom)
        for cell in roll.get("cells", []):
            if cell.get("current", {}).get("n", 0) >= 2:
                score = cell["current"].get("score")
                if score is not None:
                    axis_scores[cell["axis"]] = score
    except Exception as e:
        logger.debug("predictor: self_metrics rollup failed: %s", e)
        report["degraded"] = True

    # Axes with weak scores become likely_failures
    for axis, score in axis_scores.items():
        if score < _WEAK_SCORE:
            report["likely_failures"].append({
                "axis": axis,
                "reason": f"rolling 14d score {score:.2f} on {dom} is below {_WEAK_SCORE}",
                "forecast_score": round(score, 3),
                "remediation": _remediation_for(axis, dom, score),
                "source": "self_metrics",
            })
            report["recommendations"].append(_remediation_for(axis, dom, score))

    # ── 2. mistake_ledger — past mistakes on similar task/domain ──
    try:
        from . import mistake_ledger
        past = await mistake_ledger.lookup_similar(tt, dom, limit=_MISTAKES_LOOKBACK)
        for m in past:
            report["past_mistakes"].append({
                "mistake_id": m.get("mistake_id"),
                "what_class": m.get("what_class"),
                "severity": m.get("severity"),
                "what": (m.get("what") or "")[:200],
                "fix": (m.get("fix") or "")[:200],
                "prevented_count": m.get("prevented_count", 0),
            })
            # High-severity unprevented mistakes become extra failure signals
            if m.get("severity") in ("HIGH", "CRITICAL") and not m.get("prevented_count"):
                report["likely_failures"].append({
                    "axis": "calibration",
                    "reason": f"past {m.get('severity')} mistake class '{m.get('what_class')}' "
                              f"has not yet been prevented on a subsequent run",
                    "forecast_score": 0.4,
                    "remediation": f"Apply fix from mistake {m.get('mistake_id')}: "
                                   f"{(m.get('fix') or '')[:160]}",
                    "source": "mistake_ledger",
                })
                report["recommendations"].append(
                    f"Recheck mistake {m.get('mistake_id')} ({m.get('what_class')}) before finalising."
                )
    except Exception as e:
        logger.debug("predictor: mistake_ledger lookup failed: %s", e)
        report["degraded"] = True

    # ── 3. capability_manifest — do we even have coverage? ──
    try:
        from . import capability_manifest as cm
        manifest = await cm.latest()
        if manifest:
            # For jurisdiction-typed domains (2-letter ISO code), check adapter
            if len(dom) == 2 and dom.isalpha():
                jurisdictions = set(j.lower() for j in
                                    manifest.get("jurisdictions", {}).get("items", []))
                if dom not in jurisdictions:
                    report["likely_failures"].append({
                        "axis": "coverage",
                        "reason": f"no registry adapter wired for {dom.upper()} — "
                                  f"registry lookup will return None",
                        "forecast_score": 0.0,
                        "remediation": f"Skip registry lookup; go straight to LLM research + "
                                       f"OpenSanctions + OpenCorporates for {dom.upper()}.",
                        "source": "capability_manifest",
                    })
                    report["recommendations"].append(
                        f"Register {dom.upper()} in roadmap_nice_to_have — adapter gap."
                    )
    except Exception as e:
        logger.debug("predictor: capability_manifest lookup failed: %s", e)
        report["degraded"] = True

    # ── Composite confidence ──
    # Start at 0.75 baseline; each unique weak-axis knocks 0.1; each strong-axis
    # adds 0.05; high-sev unprevented mistakes knock 0.15. Clamp to [0.1, 0.95].
    score = 0.75
    seen_weak_axes = {f["axis"] for f in report["likely_failures"]}
    score -= 0.1 * len(seen_weak_axes)
    score += 0.05 * sum(1 for s in axis_scores.values() if s >= _STRONG_SCORE)
    score -= 0.15 * sum(1 for m in report["past_mistakes"]
                        if m.get("severity") in ("HIGH", "CRITICAL")
                        and not m.get("prevented_count"))
    report["overall_confidence"] = round(max(0.1, min(0.95, score)), 3)

    # Deduplicate recommendations while preserving order
    seen: set[str] = set()
    deduped: list[str] = []
    for r in report["recommendations"]:
        if r not in seen:
            seen.add(r)
            deduped.append(r)
    report["recommendations"] = deduped

    # Brain signal — every forecast is a learning event. Mastery on the
    # (task_type, domain) pair is calibrated by how well the forecast
    # matched the eventual run outcome (the orchestrator absorbs the
    # outcome separately). Predictor itself just emits the forecast.
    try:
        from . import brain_hook as _bh
        weak_axes = ", ".join(f["axis"] for f in report["likely_failures"][:3]) or "none"
        await _bh.absorb(
            module="predictor",
            summary=(
                f"Forecast {tt}/{dom}: confidence={report['overall_confidence']:.2f}, "
                f"weak_axes=[{weak_axes}], past_mistakes={len(report['past_mistakes'])}"
            ),
            detail=str(report["recommendations"][:5])[:1500],
            entity_name=dom,
            success=not report["degraded"],
            gap_type=("predictor_degraded" if report["degraded"] else None),
            gap_detail=("self_metrics or mistake_ledger unavailable"
                        if report["degraded"] else None),
            confidence="ASSESSED",
        )
    except Exception:
        pass

    # R-F1001 - wire to brain
    from .engine_wiring import wire_success, wire_failure
    wire_success(
        module="predictor",
        summary="Forecast",
        source_id="predictor:R-F1001",
    )

    return report
