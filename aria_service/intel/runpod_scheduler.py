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
import json
import logging
import os
import time
from datetime import datetime, timezone
from pathlib import Path
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
        # R-F1642: autostart gates the WINDOW-MODE start branch. Default "0" =
        # STOP-ONLY (pre-shadow per CLAUDE.md §24): the scheduler NEVER starts a
        # pod on its own — it only force-stops a pod left RUNNING with no active
        # work-claim. Flip to "1" for the shadow phase (auto-start in window).
        "autostart": (os.getenv("ARIA_RUNPOD_AUTOSTART") or "0").strip() == "1",
        "start_hour": int(os.getenv("ARIA_RUNPOD_START_HOUR", "10")),
        "stop_hour": int(os.getenv("ARIA_RUNPOD_STOP_HOUR", "18")),
        "tz": os.getenv("ARIA_RUNPOD_TZ", "Europe/London"),
        "api_base": (os.getenv("ARIA_RUNPOD_API_BASE") or _DEFAULT_API_BASE).rstrip("/"),
        "interval_s": float(os.getenv("ARIA_RUNPOD_INTERVAL_S", "120")),
    }


# ── work-claim (R-F1642) ─────────────────────────────────────────────────
# A short-TTL claim protects an actively-used pod (training/eval cycle) from
# the stop-only sweep. Cycle scripts claim before they start the pod and
# release when done; an expired claim no longer protects, so a forgotten pod
# survives at most one reconcile interval past its TTL. Stored as a small JSON
# file (files-only, §6 — no Redis), path overridable for tests.

def _claim_path() -> Path:
    p = os.getenv("ARIA_RUNPOD_CLAIM_PATH")
    if p:
        return Path(p)
    return Path(os.getenv("ARIA_DATA_DIR", "data")) / "runpod_workclaim.json"


def claim_pod(ttl_s: float = 7200.0, reason: str = "") -> dict:
    """Hold the pod for ttl_s seconds — the stop-only sweep will not stop it
    while the claim is live. Returns the claim record."""
    rec = {"until": time.time() + float(ttl_s), "reason": reason or "work in progress",
           "claimed_at": datetime.now(timezone.utc).isoformat()}
    path = _claim_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(rec), encoding="utf-8")
    return rec


def release_pod() -> bool:
    """Drop the work-claim (cycle finished). Idempotent."""
    try:
        _claim_path().unlink()
        return True
    except FileNotFoundError:
        return False
    except Exception:
        return False


def _claim_active(now: Optional[float] = None) -> tuple[bool, dict]:
    """(active, record). active iff a claim file exists and has not expired."""
    path = _claim_path()
    if not path.exists():
        return False, {}
    try:
        rec = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return False, {}
    return (float(rec.get("until", 0)) > (now if now is not None else time.time())), rec


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
    async with httpx.AsyncClient(timeout  # no-breaker: RunPod scheduler is best-effort; breaker would block GPU scheduling=30.0) as client:
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
    'started' | 'stopped' | 'stopped_stray' | 'claim_hold' | 'noop' |
    'disabled'.

    R-F1642 — STOP-ONLY by default (CLAUDE.md §24, pre-shadow). The scheduler
    never starts a pod unless ARIA_RUNPOD_AUTOSTART=1 (shadow phase). It DOES
    force-stop any pod left RUNNING with no active work-claim, so a forgotten
    pod cannot burn GPU. An active work-claim protects a pod in use by a
    training/eval cycle from being stopped mid-run."""
    from .engine_wiring import wire_failure, wire_success  # verified :73/:102

    if not configured():
        _last.update(action="disabled", error=None)
        return "disabled"

    cfg = _cfg()
    _last["checked_at"] = datetime.now(timezone.utc).isoformat()

    try:
        # Inside the try so a bad ARIA_RUNPOD_TZ wires to the brain
        # instead of dying log-only (Pass-2 finding, §21a).
        want_on = in_window(now)
        _last["in_window"] = want_on
        claim_active, claim = _claim_active()
        _last["claim_active"] = claim_active
        status = await get_pod_status()
        _last["pod_status"] = status
        running = status == "RUNNING"

        # A claimed pod is owned by an active cycle — never touch it.
        if claim_active:
            _last.update(action="claim_hold", error=None)
            return "claim_hold"

        # START only in window-mode (shadow phase). Stop-only never starts.
        if cfg["autostart"] and want_on and not running:
            await start_pod()
            _last.update(action="started", error=None)
            wire_success(
                module="runpod_scheduler",
                summary="ARIA-LLM pod STARTED for the daily reasoning window",
                detail=f"pod={cfg['pod_id']} window="
                       f"{cfg['start_hour']:02d}-{cfg['stop_hour']:02d} "
                       f"{cfg['tz']} (was {status})",
            )
            logger.info("[runpod_scheduler] pod started (window open)")
            return "started"

        # STOP an unclaimed RUNNING pod when: stop-only mode (always), or
        # window-mode and the window has closed.
        if running and (not cfg["autostart"] or not want_on):
            await stop_pod()
            stray = not cfg["autostart"]
            _last.update(action="stopped_stray" if stray else "stopped", error=None)
            wire_success(
                module="runpod_scheduler",
                summary=("ARIA-LLM pod FORCE-STOPPED — running with no work-claim (stop-only safety)"
                         if stray else
                         "ARIA-LLM pod STOPPED — DeepSeek takes over off-hours"),
                detail=f"pod={cfg['pod_id']} (was {status}) "
                       f"mode={'stop_only' if stray else 'window'}",
            )
            logger.info("[runpod_scheduler] pod stopped (%s)",
                        "stop-only: unclaimed pod" if stray else "window closed")
            return "stopped_stray" if stray else "stopped"

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
    claim_active, claim = _claim_active()
    return {
        "configured": configured(),
        "enabled": c["enabled"],
        "has_key": bool(c["api_key"]),
        "pod_id_set": bool(c["pod_id"]),
        "mode": "window" if c["autostart"] else "stop_only",  # R-F1642
        "autostart": c["autostart"],
        "window": f"{c['start_hour']:02d}:00-{c['stop_hour']:02d}:00 {c['tz']}",
        "in_window_now": in_window(),
        "claim_active": claim_active,
        "claim_until": claim.get("until"),
        "claim_reason": claim.get("reason"),
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
