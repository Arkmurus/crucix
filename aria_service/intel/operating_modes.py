"""ARIA Operating Modes — graduated response to quality degradation.

Four modes, auto-triggered by quality metric thresholds:

  NORMAL      → full autonomy, all delivery channels
  DEGRADED    → tasks run but no external delivery (WhatsApp suppressed)
  SUPERVISED  → all outputs queued for human review before delivery
  EMERGENCY   → only compliance/sanctions tasks run, everything else paused

Mode transitions:
  grounded_rate < 30% for 24h           → DEGRADED
  adversarial score < 50%               → SUPERVISED
  predictor blocks > 5 tasks in 24h     → EMERGENCY
  all metrics recover above thresholds  → NORMAL

Mode is stored in Redis and checked by the autonomous engine + delivery.
Manual override available via /api/aria/operating-mode/set.
"""
from __future__ import annotations

import logging
import time
from enum import IntEnum

logger = logging.getLogger("aria.operating_modes")

_K_MODE = "crucix:aria:operating_mode"
_K_MODE_HISTORY = "crucix:aria:operating_mode:history"
_K_PREDICTOR_BLOCKS_24H = "crucix:predictor:blocks:24h"


class Mode(IntEnum):
    NORMAL = 0
    DEGRADED = 1
    SUPERVISED = 2
    EMERGENCY = 3


# Thresholds for auto-transition
DEGRADED_GROUNDED_RATE = 0.30
SUPERVISED_ADVERSARIAL_SCORE = 0.50
EMERGENCY_BLOCK_COUNT = 5

# Tasks allowed in EMERGENCY mode
EMERGENCY_ALLOWED = {
    "DAILY-SANCTIONS-SCREENING",
    "DAILY-FACT-REFRESH",
    "METACOG-DAILY",
    "ADVERSARIAL-AUDIT",
    "WEEKLY-CONSTITUTION-AUDIT",
}


async def get_mode() -> Mode:
    """Current operating mode from Redis."""
    try:
        from . import redis_store as rs
        val = await rs.get(_K_MODE)
        if val is not None:
            return Mode(int(val))
    except Exception:
        pass
    return Mode.NORMAL


async def set_mode(mode: Mode, reason: str = "manual") -> dict:
    """Set operating mode. Records transition in history."""
    from . import redis_store as rs
    current = await get_mode()
    if current == mode:
        return {"mode": mode.name, "changed": False}
    await rs.set(_K_MODE, str(mode.value))
    entry = {
        "from": current.name,
        "to": mode.name,
        "reason": reason,
        "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    history = await rs.get_json(_K_MODE_HISTORY) or []
    history.insert(0, entry)
    await rs.set_json(_K_MODE_HISTORY, history[:100], ex=90 * 86400)
    logger.warning("[operating_mode] %s → %s (reason: %s)", current.name, mode.name, reason)
    return {"mode": mode.name, "changed": True, "reason": reason}


async def evaluate_auto_transition() -> dict | None:
    """Check quality metrics and auto-transition if thresholds breached.
    Called by the hourly ecosystem_reassess task."""
    current = await get_mode()

    # Check predictor blocks in last 24h
    try:
        from . import redis_store as rs
        blocks_24h = int(await rs.get(_K_PREDICTOR_BLOCKS_24H) or 0)
    except Exception:
        blocks_24h = 0

    # Check adversarial score
    try:
        from .adversarial_challenge import stats as adv_stats
        adv = await adv_stats()
        last_run = adv.get("last_run") or {}
        adversarial_score = last_run.get("overall_score", 1.0)
    except Exception:
        adversarial_score = 1.0

    # Check grounded rate
    try:
        from . import source_verifier
        recent = await source_verifier.get_verification_stats()
        grounded_rate = recent.get("avg_grounded_rate", 1.0)
    except Exception:
        grounded_rate = 1.0

    # Determine target mode (highest severity wins)
    target = Mode.NORMAL
    reason = "all metrics healthy"

    if blocks_24h >= EMERGENCY_BLOCK_COUNT:
        target = Mode.EMERGENCY
        reason = f"predictor blocked {blocks_24h} tasks in 24h (threshold: {EMERGENCY_BLOCK_COUNT})"
    elif adversarial_score < SUPERVISED_ADVERSARIAL_SCORE:
        target = Mode.SUPERVISED
        reason = f"adversarial score {adversarial_score:.0%} < {SUPERVISED_ADVERSARIAL_SCORE:.0%}"
    elif grounded_rate < DEGRADED_GROUNDED_RATE:
        target = Mode.DEGRADED
        reason = f"grounded rate {grounded_rate:.0%} < {DEGRADED_GROUNDED_RATE:.0%}"

    if target != current:
        return await set_mode(target, reason)
    return None


def should_task_run(task_id: str, mode: Mode) -> bool:
    """Check if a task should run in the current operating mode."""
    if mode == Mode.NORMAL:
        return True
    if mode == Mode.EMERGENCY:
        return task_id in EMERGENCY_ALLOWED
    # DEGRADED and SUPERVISED: tasks run, delivery is gated
    return True


def should_deliver_external(mode: Mode) -> bool:
    """Check if external delivery (WhatsApp) should happen."""
    return mode == Mode.NORMAL
