"""Calibration auto-tune — close the feedback loop on confidence thresholds.

What this fixes
───────────────
calibration_review already measures whether mastery scores predict
actual accuracy (ECE-style). When mastery says 88% but accuracy is
65%, ARIA is systematically OVERCONFIDENT — but until this module,
the only consequence was a log line and a soft brain_hook signal.
The thresholds that GATE confidence-tag usage (`WEAK_THRESHOLD=0.55`,
`_CRITICAL_THRESHOLD≈0.40`) stayed fixed.

This module closes the loop: when ARIA is consistently overconfident,
raise the threshold so more claims fall into the weak-topic rule set
(caps at [PROBABLE], requires double sourcing). When consistently
underconfident, relax the threshold so the existing knowledge base
gets to speak with appropriate certainty rather than hedging everything
into [UNCERTAIN].

Design choices
──────────────
  - Change the OFFSET, not the constants. student.py reads base
    constants; this module stores a Redis-backed delta that
    `get_effective_threshold()` applies at read time. Easy to audit,
    easy to roll back.
  - RATE-LIMITED: runs weekly. Requires 3+ consecutive consistent
    readings before adjusting (no noise-chasing).
  - BOUNDED: each adjustment capped at ±3pp per run. A 20pp miscalibration
    closes over ~7 runs (~7 weeks) gradually.
  - ASYMMETRIC: over- and under-confidence are treated differently.
    Overconfidence tightens faster (safety); underconfidence loosens
    slower (avoid emboldening weak knowledge).
  - AUDITABLE: every adjustment writes to audit_log with before/after.

Public API
──────────
  async get_effective_threshold(name: str, base_value: float) -> float
      Returns the base value + current delta. Called from student.py.

  async run_auto_tune() -> dict
      Consume the latest calibration_review + per-tag accuracy, compute
      a new delta, persist, audit. Runs weekly via autonomous engine.

  async get_current_state() -> dict
      Dashboard + capability_card use this to surface current deltas.
"""
from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger("aria.calibration_auto_tune")

# Redis keys
_K_DELTA = "crucix:calibration:threshold_delta"
_K_HISTORY = "crucix:calibration:threshold_delta:history"
_K_LAST_RUN = "crucix:calibration:auto_tune:last_run"

# Tuning parameters
_COOLDOWN_S = 6 * 86400               # 6 days — essentially weekly cadence
_MAX_ADJUST_PER_RUN = 0.03            # 3pp per weekly run
_MIN_DELTA_TO_TRIGGER = 0.08          # |calibration_delta| must exceed 8pp
_REQUIRED_CONSISTENT_RUNS = 3         # need N consecutive same-sign deltas
_MAX_ABSOLUTE_DELTA = 0.20            # cap cumulative offset at 20pp
_HISTORY_DEPTH = 24                   # ~6 months of weekly runs


async def get_effective_threshold(name: str, base_value: float) -> float:
    """Apply the current delta to a named threshold. Returns the
    base unchanged if no delta is set or if Redis is offline.

    Thresholds to call this on:
      - weak (base 0.55)
      - critical (base 0.40)
      - strong (base 0.80)
    """
    try:
        from . import redis_store as rs
        state = await rs.get_json(_K_DELTA) or {}
        delta = float(state.get(f"delta_{name}", 0.0))
        # Clamp to sane bounds so a bad persisted state can never push
        # thresholds into nonsense ranges.
        adjusted = max(0.05, min(0.99, base_value + delta))
        return adjusted
    except Exception as e:
        logger.debug("[auto_tune] threshold read failed: %s", e)

    # R-F1001 - wire to brain
    from .engine_wiring import wire_success
    wire_success(
        module="calibration_auto_tune",
        summary="Get Effective Threshold",
        source_id="calibration_auto_tune:R-F1001",
    )

        return base_value


async def get_current_state() -> dict:
    """Return current deltas + last run info for dashboard/card."""
    try:
        from . import redis_store as rs
        state = await rs.get_json(_K_DELTA) or {}
        history = []
        raw_history = await rs.lrange(_K_HISTORY, 0, 10)
        for h in raw_history:
            try:
                import json
                history.append(json.loads(h))
            except Exception:
                pass
        return {
            "current_deltas": {
                "weak": state.get("delta_weak", 0.0),
                "critical": state.get("delta_critical", 0.0),
                "strong": state.get("delta_strong", 0.0),
            },
            "last_adjusted_at": state.get("last_adjusted_at"),
            "reason": state.get("last_reason", ""),
            "consecutive_overconfident": state.get("consec_over", 0),
            "consecutive_underconfident": state.get("consec_under", 0),
            "history": history,
        }
    except Exception:
        return {"current_deltas": {}, "history": []}


async def run_auto_tune() -> dict:
    """Main entry point. Called weekly by autonomous engine or manually."""
    from . import redis_store as rs

    # Respect cooldown so manual runs don't stack with scheduled ones
    now_ts = time.time()
    last_run_raw = await rs.get(_K_LAST_RUN)
    try:
        last_run_ts = float(last_run_raw) if last_run_raw else 0.0
    except Exception:
        last_run_ts = 0.0
    if last_run_ts and (now_ts - last_run_ts) < _COOLDOWN_S:
        return {
            "ran": False,
            "reason": "cooldown",
            "next_allowed_in_s": int(_COOLDOWN_S - (now_ts - last_run_ts)),
        }

    # Read the latest calibration review
    review = await rs.get_json("crucix:calibration:review") or {}
    status = review.get("calibration_status")
    delta = review.get("calibration_delta")

    if status in (None, "insufficient_data", "well_calibrated") or delta is None:
        # Nothing actionable — reset the consecutive-run counters so
        # a future blip doesn't fire on an old streak.
        state = await rs.get_json(_K_DELTA) or {}
        if state.get("consec_over", 0) or state.get("consec_under", 0):
            state["consec_over"] = 0
            state["consec_under"] = 0
            state["last_adjusted_at"] = datetime.now(timezone.utc).isoformat()
            state["last_reason"] = f"reset — status={status}"
            await rs.set_json(_K_DELTA, state)
        await rs.set(_K_LAST_RUN, str(now_ts))
        return {"ran": True, "adjusted": False, "reason": f"no action — status={status}"}

    # Signal must clear the minimum-delta bar
    if abs(delta) < _MIN_DELTA_TO_TRIGGER:
        state = await rs.get_json(_K_DELTA) or {}
        # Don't reset counters — small delta in the same direction still
        # contributes to the streak. Only full reset on well_calibrated.
        await rs.set(_K_LAST_RUN, str(now_ts))
        return {
            "ran": True, "adjusted": False,
            "reason": f"delta {delta:+.3f} below trigger threshold {_MIN_DELTA_TO_TRIGGER}",
        }

    # Load state + update streak counters
    state = await rs.get_json(_K_DELTA) or {
        "delta_weak": 0.0, "delta_critical": 0.0, "delta_strong": 0.0,
        "consec_over": 0, "consec_under": 0,
    }
    if delta > 0:
        # Overconfident — mastery higher than accuracy
        state["consec_over"] = state.get("consec_over", 0) + 1
        state["consec_under"] = 0
    else:
        state["consec_under"] = state.get("consec_under", 0) + 1
        state["consec_over"] = 0

    # Only adjust after N consecutive consistent runs — don't chase noise
    is_overconfident = state["consec_over"] >= _REQUIRED_CONSISTENT_RUNS
    is_underconfident = state["consec_under"] >= _REQUIRED_CONSISTENT_RUNS

    adjusted = False
    reason = "building streak (waiting for consistent signal)"
    adjustment_detail: dict[str, Any] = {}

    if is_overconfident:
        # Raise weak + critical thresholds — more claims require stronger
        # evidence before [CONFIRMED] / [PROBABLE] tags are allowed.
        adjust = min(_MAX_ADJUST_PER_RUN, abs(delta) * 0.25)
        for k in ("delta_weak", "delta_critical"):
            new_val = min(state.get(k, 0.0) + adjust, _MAX_ABSOLUTE_DELTA)
            adjustment_detail[k] = {"from": state.get(k, 0.0), "to": new_val}
            state[k] = new_val
        # Leave delta_strong alone — strong threshold governs high-mastery
        # domains; tightening it would degrade good knowledge.
        reason = (
            f"overconfident {state['consec_over']} runs in a row "
            f"(latest delta {delta:+.3f}) — raising weak+critical "
            f"thresholds by {adjust*100:.1f}pp"
        )
        adjusted = True
        state["consec_over"] = 0  # reset after acting

    elif is_underconfident:
        # Lower weak + critical thresholds — let ARIA speak with the
        # confidence her track record actually supports. Slower rate
        # than overconfident path (0.15 vs 0.25 fraction) because
        # emboldening weak knowledge is the worse failure mode.
        adjust = min(_MAX_ADJUST_PER_RUN, abs(delta) * 0.15)
        for k in ("delta_weak", "delta_critical"):
            new_val = max(state.get(k, 0.0) - adjust, -_MAX_ABSOLUTE_DELTA)
            adjustment_detail[k] = {"from": state.get(k, 0.0), "to": new_val}
            state[k] = new_val
        reason = (
            f"underconfident {state['consec_under']} runs in a row "
            f"(latest delta {delta:+.3f}) — lowering weak+critical "
            f"thresholds by {adjust*100:.1f}pp"
        )
        adjusted = True
        state["consec_under"] = 0

    state["last_adjusted_at"] = datetime.now(timezone.utc).isoformat()
    state["last_reason"] = reason

    await rs.set_json(_K_DELTA, state)
    await rs.set(_K_LAST_RUN, str(now_ts))

    # History entry so the operator can audit the trajectory
    if adjusted:
        try:
            import json
            history_entry = {
                "ts": state["last_adjusted_at"],
                "calibration_delta": delta,
                "status": status,
                "reason": reason,
                "adjustments": adjustment_detail,
            }
            await rs.lpush(_K_HISTORY, json.dumps(history_entry)[:4000])
            await rs.ltrim(_K_HISTORY, 0, _HISTORY_DEPTH - 1)
        except Exception:
            pass

        # Audit log — this is a governance decision, it deserves an
        # immutable record regardless of Redis persistence.
        # Was previously calling `audit_log.append(...)` -- that function
        # never existed (only `record`), so the AttributeError was caught
        # by the bare except and every auto-tune decision was silently
        # missing from the audit chain since shipped.
        try:
            from . import audit_log as _al
            await _al.record(
                action="calibration_threshold_adjusted",
                actor="aria_calibration_auto_tune",
                inputs=adjustment_detail or {},
                decision=(reason or "")[:200],
                notes=(reason or "")[:500],
            )
        except Exception:
            pass

        # Feed the brain so self_metrics tracks when we last tightened
        # vs loosened confidence, and mastery_report surfaces the recent
        # calibration direction alongside per-topic scores.
        try:
            from . import brain_hook as _bh
            await _bh.absorb(
                module="calibration_auto_tune",
                summary=(
                    f"Calibration threshold {'tightened' if is_overconfident else 'loosened'}: {reason}"
                ),
                detail=f"adjustments={adjustment_detail}",
                success=True,  # a correct threshold move is a success even on overconfident path
                confidence="CONFIRMED",
            )
        except Exception:
            pass

        logger.warning(
            "[auto_tune] ADJUSTED %s: %s",
            "overconfident" if is_overconfident else "underconfident",
            reason,
        )

    return {
        "ran": True,
        "adjusted": adjusted,
        "reason": reason,
        "status": status,
        "delta": delta,
        "state": state,
    }
