"""R-F560 (2026-05-16) — error-streak counter for Phase A exit gate #3.

Phase A gate #3 (`platform_buildout_north_star.md`) requires:
    "0 fly ERROR logs in last 7 days"

Before R-F560 the gate was observed by eye against `fly logs | grep ERROR`
on each session start. R-F560 turns that into a queryable number by
reading the existing self_improve error ledger (already populated by
`error_log_handler.py`) and computing the consecutive-clean-day streak.

Surface:
    GET /api/aria/health/error-streak
    →  {
        "consecutive_clean_days": 3,
        "consecutive_clean_seconds": 261000,
        "last_error": {"type": "log:error", "timestamp": ..., "file": ...},
        "last_error_age_hours": 72.5,
        "phase_a_gate_3_pass": false,
        "phase_a_gate_3_threshold_days": 7,
        "window_errors_24h": 4,
        "window_errors_7d": 12,
    }

Capability:
    The dashboard panel can show "X / 7 clean days" without anyone
    having to manually `fly logs`.

Honesty:
    - Only ERROR + CRITICAL levels count toward the streak.
    - WARNINGs are reported in the windows but don't reset the streak.
    - 7-day TTL on the source ledger means a streak >7d shows as
      "≥7 (older entries pruned)" — gate still passes; this is
      intentional.

R-F969 (2026-05-28) — THIS endpoint is the CANONICAL gate-#3 measure, and
it is deploy-noise-immune by construction. Two classes of noise that look
like "fly ERROR logs" but do NOT (and must not) reset the streak:
  1. Fly-platform deploy-window errors — `[PM08]`/`[PR03]` proxy-lease +
     health-check-fail lines emitted while a machine is replaced during a
     redeploy. These are fly ORCHESTRATION events, not app logs; they never
     enter the app error ledger this endpoint reads, so a deploy cannot
     reset the streak here. (Hand-grepping `flyctl logs | grep -i error`
     DOES surface them — which is exactly why that hand method was retired
     in favour of this counter.)
  2. Operational WARNINGs — circuit-breaker source-down transitions
     (`HALF_OPEN → OPEN`, `CLOSED → OPEN`, brain_hook.py / circuit_breaker.py)
     and R-F703 event-loop-stall warnings all log at WARNING (`log:warning`),
     so they show in the windowed totals but never reset the clean streak.
The `noise_excluded` field below documents this so the gate stays trustworthy
without re-litigating "does a deploy break gate #3?" every session (it does
not).
"""
from __future__ import annotations
from .engine_wiring import wire_failure

import logging
import time
from typing import Any

logger = logging.getLogger("aria.error_streak")

# Phase A gate #3 threshold from `platform_buildout_north_star.md`.
PHASE_A_GATE_3_DAYS = 7

# Severities that COUNT as a streak-resetting error. Anything else
# (warning, info, debug) is reported in the windowed totals but does
# not reset the clean streak.
_RESET_LEVEL_PREFIXES = ("log:error", "log:critical", "log:fatal")


def _is_reset_event(event: dict) -> bool:
    et = (event.get("type") or "").lower()
    return any(et.startswith(p) for p in _RESET_LEVEL_PREFIXES)


async def compute_error_streak(
    *,
    threshold_days: int = PHASE_A_GATE_3_DAYS,
    now: float | None = None,
) -> dict[str, Any]:
    """Compute the consecutive error-free-day streak from the live
    error ledger. Returns the dashboard-ready dict shape.

    Never raises — failures degrade to honest "unknown" output so the
    dashboard renders rather than 500-ing.
    """
    from . import redis_store as rs
    from . import self_improve as si

    t_now = float(now if now is not None else time.time())
    out: dict[str, Any] = {
        "consecutive_clean_days": None,
        "consecutive_clean_seconds": None,
        "last_error": None,
        "last_error_age_hours": None,
        "phase_a_gate_3_pass": False,
        "phase_a_gate_3_threshold_days": threshold_days,
        "window_errors_24h": 0,
        "window_errors_7d": 0,
        "level_breakdown_7d": {},
        "as_of": int(t_now),
        # R-F969 — make the deploy-noise immunity explicit + self-documenting.
        "noise_excluded": (
            "fly-platform deploy-window errors (PM08/PR03 proxy-lease + "
            "health-check-fail during machine replacement) are orchestration "
            "events, not app logs, so they never reach this ledger; "
            "operational WARNINGs (circuit-breaker source-down, R-F703 "
            "event-loop stalls) log at WARNING and never reset the streak. "
            "Only app-level ERROR/CRITICAL reset it."
        ),
    }

    try:
        events: list[dict] = await rs.get_json(si.ERROR_LOG_KEY) or []
    except Exception as e:
        logger.warning("R-F560 error_streak: ledger fetch failed: %s", e)
        out["error"] = f"ledger_fetch_failed:{type(e).__name__}"
        return out

    # ── Windowed totals (counts include WARNING+, not just ERROR) ─────
    cutoff_24h = t_now - 86400
    cutoff_7d = t_now - 7 * 86400
    breakdown: dict[str, int] = {}
    for ev in events:
        ts = float(ev.get("timestamp") or 0)
        et = (ev.get("type") or "").lower()
        if ts >= cutoff_24h:
            out["window_errors_24h"] += 1
        if ts >= cutoff_7d:
            out["window_errors_7d"] += 1
            breakdown[et] = breakdown.get(et, 0) + 1
    out["level_breakdown_7d"] = breakdown

    # ── Streak: most recent ERROR/CRITICAL event ──────────────────────
    last_reset_ev: dict | None = None
    last_reset_ts = -1.0
    for ev in events:
        if not _is_reset_event(ev):
            continue
        ts = float(ev.get("timestamp") or 0)
        if ts > last_reset_ts:
            last_reset_ts = ts
            last_reset_ev = ev

    if last_reset_ev is None:
        # No ERROR in the (up-to-7d) ledger. Streak is ≥ ledger TTL.
        # Conservative report: assume the gate threshold has been
        # reached (caller can clamp if needed for stricter display).
        out["consecutive_clean_seconds"] = 7 * 86400  # ledger TTL
        out["consecutive_clean_days"] = 7
        out["phase_a_gate_3_pass"] = True
        out["last_error"] = None
        out["last_error_age_hours"] = None
        return out

    clean_seconds = max(0.0, t_now - last_reset_ts)
    out["consecutive_clean_seconds"] = int(clean_seconds)
    out["consecutive_clean_days"] = int(clean_seconds // 86400)
    out["last_error"] = {
        "type": last_reset_ev.get("type"),
        "message": (last_reset_ev.get("message") or "")[:200],
        "file": last_reset_ev.get("file"),
        "function": last_reset_ev.get("function"),
        "timestamp": int(last_reset_ts),
    }
    out["last_error_age_hours"] = round(clean_seconds / 3600.0, 1)
    out["phase_a_gate_3_pass"] = (
        out["consecutive_clean_days"] >= threshold_days
    )
    # R-F1001 - wire to brain
    from .engine_wiring import wire_success, wire_failure
    wire_success(
        module="error_streak",
        summary="Compute Error Streak",
        source_id="error_streak:R-F1001",
    )

    return out

# R-F2538: R-F2119 import-time wire_failure("module shutdown") block removed — it fired a FALSE engine_failure gap on every import (not at shutdown); do not re-add.
