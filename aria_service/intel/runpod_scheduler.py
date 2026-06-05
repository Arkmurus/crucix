"""runpod_scheduler — ARIA runs her own GPU reasoning window (R-F1335).

Operator directive (2026-06-05): "it is ARIA's show — she is fully
independent from this point; her own reasoning from those timings (UK
base timing); when that is shut, DeepSeek is active."

What this does
--------------
Starts ARIA's RunPod pod (vLLM serving aria-llm) at the start of the
daily window and stops it at the end, on Europe/London wall-clock time
(DST-safe via zoneinfo). The LLM chain side is already env-driven:
`fallback.py` inserts ARIA-LLM as PRIMARY when ARIA_LLM_URL is set, and
the provider-cooldown chain hands over to DeepSeek automatically when
the pod is unreachable (off-hours). So the ONLY thing the schedule has
to manage is pod compute — no redeploys, no secret churn, no paying for
GPU + DeepSeek at the same time.

Config (fly secrets on aria-intel):
  RUNPOD_API_KEY                 RunPod account API key (operator-provided)
  ARIA_RUNPOD_POD_ID             pod id serving the model
  ARIA_RUNPOD_SCHEDULE_ENABLED   "1" to run (default "1"; module no-ops
                                 harmlessly when key/pod id are missing)
  ARIA_RUNPOD_START_HOUR         default 10  (local hour, inclusive)
  ARIA_RUNPOD_STOP_HOUR          default 18  (local hour, exclusive)
  ARIA_RUNPOD_TZ                 default Europe/London
  ARIA_RUNPOD_API_BASE           default https://rest.runpod.io/v1
                                 (RunPod pods REST API; overridable because
                                 the API surface was NOT live-verified at
                                 ship time — no key available. Verify on
                                 first enable and adjust if needed.)
  ARIA_RUNPOD_INTERVAL_S         loop period, default 120

Wiring (§21a): every state change AND every failure emits a brain
signal (wire_success / wire_failure — verified in engine_wiring.py:73/102).
The loop ticks tick_heartbeat("runpod_scheduler") so self_restart's
blackout detector watches it.
"""
from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime, timezone
from typing import Any, Optional
from zoneinfo import ZoneInfo

logger = logging.getLogger("aria.runpod_scheduler")

_DEFAULT_API_BASE = "https://rest.runpod.io/v1"

# Last action the loop took — surfaced via get_status() for diagnostics.
_last: dict[str, Any] = {
    "checked_at": None,
    "in_window": None,
    "pod_status": None,
    "action": None,
    "error": None,
}


def _cfg() -> dict[str, Any]:
    return {
        "api_key": (os.getenv("RUNPOD_API_KEY") or "").strip(),
        "pod_id": (os.getenv("ARIA_RUNPOD_POD_ID") or "").strip(),
        "enabled": (os.getenv("ARIA_RUNPOD_SCHEDULE_ENABLED") or "1").strip() == "1",
        "start_hour": int(os.getenv("ARIA_RUNPOD_START_HOUR", "10")),
        "stop_hour": int(os.getenv("ARIA_RUNPOD_STOP_HOUR", "18")),
        "tz": os.getenv("ARIA_RUNPOD_TZ", "Europe/London"),
        "api_base": (os.getenv("ARIA_RUNPOD_API_BASE") or _DEFAULT_API_BASE).rstrip("/"),
        "interval_s": float(os.getenv("ARIA_RUNPOD_INTERVAL_S", "120")),
    }


def configured() -> bool:
    c = _cfg()
    return bool(c["api_key"] and c["pod_id"] and c["enabled"])


def in_window(now: Optional[datetime] = None) -> bool:
    """True when local (Europe/London by default) hour is inside
    [start_hour, stop_hour). zoneinfo handles GMT/BST transitions."""
    c = _cfg()
    tz = ZoneInfo(c["tz"])
    local = (now.astimezone(tz) if now else datetime.now(tz))
    return c["start_hour"] <= local.hour < c["stop_hour"]


async def _api(method: str, path: str) -> dict:
    """One RunPod REST call. Raises on transport error / non-2xx."""
    import httpx

    c = _cfg()
    url = f"{c['api_base']}{path}"
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.request(
            method, url,
            headers={"Authorization": f"Bearer {c['api_key']}"},
        )
        resp.raise_for_status()
        try:
            return resp.json()
        except Exception:
            return {"raw": resp.text[:500]}


async def get_pod_status() -> Optional[str]:
    """Return the pod's desiredStatus (e.g. RUNNING / EXITED) or None."""
    c = _cfg()
    data = await _api("GET", f"/pods/{c['pod_id']}")
    status = data.get("desiredStatus") or data.get("status")
    return str(status).upper() if status else None


async def start_pod() -> dict:
    c = _cfg()
    return await _api("POST", f"/pods/{c['pod_id']}/start")


async def stop_pod() -> dict:
    c = _cfg()
    return await _api("POST", f"/pods/{c['pod_id']}/stop")


async def ensure_state(now: Optional[datetime] = None) -> str:
    """One reconciliation tick. Returns the action taken:
    'started' | 'stopped' | 'noop' | 'disabled'."""
    from .engine_wiring import wire_failure, wire_success  # verified :73/:102

    if not configured():
        _last.update(action="disabled", error=None)
        return "disabled"

    _last["checked_at"] = datetime.now(timezone.utc).isoformat()

    try:
        # Inside the try so a bad ARIA_RUNPOD_TZ wires to the brain
        # instead of dying log-only (Pass-2 finding, §21a).
        want_on = in_window(now)
        _last["in_window"] = want_on
        status = await get_pod_status()
        _last["pod_status"] = status
        running = status == "RUNNING"

        if want_on and not running:
            await start_pod()
            _last.update(action="started", error=None)
            wire_success(
                module="runpod_scheduler",
                summary="ARIA-LLM pod STARTED for the daily reasoning window",
                detail=f"pod={_cfg()['pod_id']} window="
                       f"{_cfg()['start_hour']:02d}-{_cfg()['stop_hour']:02d} "
                       f"{_cfg()['tz']} (was {status})",
            )
            logger.info("[runpod_scheduler] pod started (window open)")
            return "started"

        if not want_on and running:
            await stop_pod()
            _last.update(action="stopped", error=None)
            wire_success(
                module="runpod_scheduler",
                summary="ARIA-LLM pod STOPPED — DeepSeek takes over off-hours",
                detail=f"pod={_cfg()['pod_id']} (was {status})",
            )
            logger.info("[runpod_scheduler] pod stopped (window closed)")
            return "stopped"

        _last.update(action="noop", error=None)
        return "noop"
    except Exception as exc:
        _last.update(action="error", error=str(exc)[:300])
        wire_failure(
            module="runpod_scheduler",
            detail=f"pod reconcile failed: {exc}",
            gap_type="engine_failure",
            source="runpod_scheduler",
        )
        logger.warning("[runpod_scheduler] reconcile failed: %s", exc)
        return "error"


def get_status() -> dict:
    """Diagnostics for health surfaces (no secrets)."""
    c = _cfg()
    return {
        "configured": configured(),
        "enabled": c["enabled"],
        "has_key": bool(c["api_key"]),
        "pod_id_set": bool(c["pod_id"]),
        "window": f"{c['start_hour']:02d}:00-{c['stop_hour']:02d}:00 {c['tz']}",
        "in_window_now": in_window(),
        "last": dict(_last),
    }


async def scheduler_loop() -> None:
    """Background task started from main.py lifespan. Harmless when
    unconfigured (checks config every tick, so setting fly secrets —
    which restarts the app — picks it up at next boot)."""
    from .self_restart import tick_heartbeat  # verified: self_restart.py

    logger.info(
        "[runpod_scheduler] loop started (configured=%s, %s)",
        configured(), get_status()["window"],
    )
    while True:
        try:
            tick_heartbeat("runpod_scheduler")
            await ensure_state()
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # belt-and-braces; ensure_state already guards
            logger.warning("[runpod_scheduler] tick error: %s", exc)
        await asyncio.sleep(_cfg()["interval_s"])
