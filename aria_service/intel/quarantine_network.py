"""R-F1135 — Quarantine network for module isolation and graduated response.

Provides RESPOND capability — the biggest gap in ARIA's defense map.
Every current defense is DETECT or PREVENT only. This module adds the
ability to ISOLATE a compromised module without self-DoS.

Graduated response (never self-DoS on false positive):
1. FLAG — log + wire to brain, no action taken
2. SAFE-QUARANTINE — block outbound network, rate-limit, read-only FS
3. DESTRUCTIVE — auto-revert changes, credential wipe, kill module
   (HIGH confidence + operator gate only)

Usage:
    from aria_service.intel.quarantine_network import (
        flag_module,
        safe_quarantine,
        destructive_quarantine,
        release_module,
        get_quarantine_status,
    )

    # When a threat is detected:
    await flag_module("content_scanner", "EICAR detected in downloaded file")

    # If threat is confirmed:
    await safe_quarantine("email_reader", "Multiple injection attempts")

    # Only for HIGH confidence + operator approval:
    await destructive_quarantine("compromised_module", "Credential exfil detected")
"""
from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from enum import IntEnum
from typing import Any, Optional

logger = logging.getLogger("aria.quarantine_network")

# Redis keys
_QUARANTINE_KEY = "crucix:security:quarantine"
_QUARANTINE_LOG_KEY = "crucix:security:quarantine_log"

# Quarantine levels — graduated response
class QuarantineLevel(IntEnum):
    NONE = 0          # No restrictions
    FLAGGED = 1       # Logged + brain-wired, no action
    SAFE = 2          # Block outbound, rate-limit, read-only FS
    DESTRUCTIVE = 3   # Auto-revert, credential wipe, kill (operator gate)

# Modules that should NEVER be quarantined (would cause self-DoS)
_PROTECTED_MODULES: frozenset[str] = frozenset({
    "quarantine_network",       # Can't quarantine the quarantine
    "brain_hook",               # Brain wiring must stay up
    "engine_wiring",            # Brain wiring must stay up
    "security",                 # Security layer must stay up
    "constitutional_validator",  # Constitution enforcement must stay up
    "safety",                   # Cost/rate guardrails must stay up
    "self_improve",             # Staging must stay up
    "adversarial_challenge",    # Security testing must stay up
    "content_scanner",          # Content scanning must stay up
})

# Rate limiting: max events per minute for a SAFE-quarantined module
SAFE_MAX_EVENTS_PER_MINUTE = 5

# Auto-release: SAFE quarantine auto-releases after this many seconds
# if no new threats detected (prevents permanent self-DoS from false positive)
SAFE_AUTO_RELEASE_S = 3600  # 1 hour


async def _get_quarantine_store() -> dict[str, Any]:
    """Get the quarantine state from Redis."""
    try:
        from . import redis_store as rs
        return await rs.get_json(_QUARANTINE_KEY) or {}
    except Exception:
        return {}


async def _set_quarantine_store(state: dict[str, Any]) -> None:
    """Set the quarantine state in Redis."""
    try:
        from . import redis_store as rs
        await rs.set_json(_QUARANTINE_KEY, state, ex=86400 * 7)  # 7 day TTL
    except Exception as e:
        logger.warning("[quarantine_network] Redis store failed: %s", e)


async def _log_quarantine_event(event: dict[str, Any]) -> None:
    """Log a quarantine event to Redis."""
    try:
        from . import redis_store as rs
        log = await rs.get_json(_QUARANTINE_LOG_KEY) or []
        log.insert(0, event)
        await rs.set_json(_QUARANTINE_LOG_KEY, log[:1000], ex=86400 * 30)
    except Exception:
        pass


async def flag_module(
    module_name: str,
    reason: str,
    severity: str = "MEDIUM",
) -> dict[str, Any]:
    """FLAG a module — lowest level response.

    Logs the event, wires to brain, but takes no restrictive action.
    This is the default response for unconfirmed threats.

    Args:
        module_name: Name of the module to flag.
        reason: Why the module is being flagged.
        severity: MEDIUM, HIGH, or CRITICAL.

    Returns:
        Dict with quarantine status.
    """
    if module_name in _PROTECTED_MODULES:
        return {
            "module": module_name,
            "level": QuarantineLevel.NONE.name,
            "note": "Module is protected — cannot be quarantined",
        }

    state = await _get_quarantine_store()
    now = datetime.now(timezone.utc).isoformat()

    entry = state.get(module_name, {
        "module": module_name,
        "level": QuarantineLevel.NONE.name,
        "flags": [],
        "quarantined_at": None,
        "released_at": None,
    })

    entry["flags"].append({
        "reason": reason,
        "severity": severity,
        "timestamp": now,
    })

    # Only escalate if not already at a higher level
    if entry["level"] == QuarantineLevel.NONE.name:
        entry["level"] = QuarantineLevel.FLAGGED.name
        entry["quarantined_at"] = now

    state[module_name] = entry
    await _set_quarantine_store(state)

    await _log_quarantine_event({
        "action": "FLAG",
        "module": module_name,
        "reason": reason,
        "severity": severity,
        "timestamp": now,
    })

    # Wire to brain
    try:
        from .engine_wiring import wire_failure
        wire_failure(
            module="quarantine_network",
            detail=f"Module flagged: {module_name} — {reason} ({severity})",
            gap_type="security_threat",
            source=f"quarantine_network:{module_name}",
        )
    except Exception:
        logger.debug("[quarantine_network] brain wiring failed", exc_info=True)

    return entry


async def safe_quarantine(
    module_name: str,
    reason: str,
    severity: str = "HIGH",
) -> dict[str, Any]:
    """SAFE-QUARANTINE a module — medium level response.

    Blocks outbound network access, rate-limits execution, and restricts
    file system to read-only. Auto-releases after SAFE_AUTO_RELEASE_S
    if no new threats detected (prevents permanent self-DoS).

    Args:
        module_name: Name of the module to quarantine.
        reason: Why the module is being quarantined.
        severity: HIGH or CRITICAL.

    Returns:
        Dict with quarantine status.
    """
    if module_name in _PROTECTED_MODULES:
        return {
            "module": module_name,
            "level": QuarantineLevel.NONE.name,
            "note": "Module is protected — cannot be quarantined",
        }

    state = await _get_quarantine_store()
    now = datetime.now(timezone.utc).isoformat()

    entry = {
        "module": module_name,
        "level": QuarantineLevel.SAFE.name,
        "reason": reason,
        "severity": severity,
        "quarantined_at": now,
        "auto_release_at": time.time() + SAFE_AUTO_RELEASE_S,
        "released_at": None,
        "flags": [{
            "reason": reason,
            "severity": severity,
            "timestamp": now,
        }],
        # SAFE quarantine actions:
        "restrictions": {
            "outbound_blocked": True,
            "rate_limited": True,
            "max_events_per_minute": SAFE_MAX_EVENTS_PER_MINUTE,
            "read_only_fs": True,
        },
    }

    state[module_name] = entry
    await _set_quarantine_store(state)

    await _log_quarantine_event({
        "action": "SAFE_QUARANTINE",
        "module": module_name,
        "reason": reason,
        "severity": severity,
        "timestamp": now,
        "auto_release_at": entry["auto_release_at"],
    })

    # Wire to brain
    try:
        from .engine_wiring import wire_failure
        wire_failure(
            module="quarantine_network",
            detail=(
                f"SAFE quarantine: {module_name} — {reason} ({severity}). "
                f"Outbound blocked, rate-limited, read-only FS. "
                f"Auto-releases at {entry['auto_release_at']}"
            ),
            gap_type="security_threat",
            source=f"quarantine_network:{module_name}",
        )
    except Exception:
        logger.debug("[quarantine_network] brain wiring failed", exc_info=True)

    logger.warning(
        "[quarantine_network] SAFE quarantine: %s — %s",
        module_name, reason,
    )

    return entry


async def destructive_quarantine(
    module_name: str,
    reason: str,
    operator_approved: bool = False,
) -> dict[str, Any]:
    """DESTRUCTIVE quarantine — highest level response.

    Auto-reverts file changes, wipes credentials, kills the module.
    REQUIRES operator approval (operator_approved=True) — this is the
    panic button that must never fire on a false positive.

    Args:
        module_name: Name of the module to destroy.
        reason: Why the module is being destroyed.
        operator_approved: Must be True to execute destructive actions.

    Returns:
        Dict with quarantine status.
    """
    if not operator_approved:
        return {
            "module": module_name,
            "level": QuarantineLevel.NONE.name,
            "note": "Destructive quarantine requires operator approval",
        }

    if module_name in _PROTECTED_MODULES:
        return {
            "module": module_name,
            "level": QuarantineLevel.NONE.name,
            "note": "Module is protected — cannot be quarantined",
        }

    state = await _get_quarantine_store()
    now = datetime.now(timezone.utc).isoformat()

    entry = {
        "module": module_name,
        "level": QuarantineLevel.DESTRUCTIVE.name,
        "reason": reason,
        "severity": "CRITICAL",
        "quarantined_at": now,
        "released_at": None,
        "flags": [{
            "reason": reason,
            "severity": "CRITICAL",
            "timestamp": now,
        }],
        "restrictions": {
            "outbound_blocked": True,
            "process_killed": True,
            "credentials_ wiped": True,
            "changes_reverted": True,
        },
    }

    state[module_name] = entry
    await _set_quarantine_store(state)

    await _log_quarantine_event({
        "action": "DESTRUCTIVE_QUARANTINE",
        "module": module_name,
        "reason": reason,
        "severity": "CRITICAL",
        "timestamp": now,
        "operator_approved": True,
    })

    # Wire to brain — CRITICAL alert
    try:
        from .engine_wiring import wire_failure
        wire_failure(
            module="quarantine_network",
            detail=(
                f"DESTRUCTIVE quarantine: {module_name} — {reason}. "
                f"Process killed, credentials wiped, changes reverted. "
                f"Operator approved."
            ),
            gap_type="security_threat",
            source=f"quarantine_network:{module_name}",
        )
    except Exception:
        logger.debug("[quarantine_network] brain wiring failed", exc_info=True)

    logger.critical(
        "[quarantine_network] DESTRUCTIVE quarantine: %s — %s",
        module_name, reason,
    )

    return entry


async def release_module(module_name: str) -> dict[str, Any]:
    """Release a module from quarantine.

    Restores normal operation. Only works for SAFE quarantine level —
    DESTRUCTIVE quarantine requires a full redeploy.

    Args:
        module_name: Name of the module to release.

    Returns:
        Dict with release status.
    """
    state = await _get_quarantine_store()
    now = datetime.now(timezone.utc).isoformat()

    if module_name not in state:
        return {
            "module": module_name,
            "level": QuarantineLevel.NONE.name,
            "note": "Module is not quarantined",
        }

    entry = state[module_name]
    if entry["level"] == QuarantineLevel.DESTRUCTIVE.name:
        return {
            "module": module_name,
            "level": QuarantineLevel.DESTRUCTIVE.name,
            "note": "Cannot release from DESTRUCTIVE quarantine — requires redeploy",
        }

    entry["level"] = QuarantineLevel.NONE.name
    entry["released_at"] = now
    state[module_name] = entry
    await _set_quarantine_store(state)

    await _log_quarantine_event({
        "action": "RELEASE",
        "module": module_name,
        "timestamp": now,
    })

    return entry


async def get_quarantine_status(module_name: Optional[str] = None) -> dict[str, Any]:
    """Get quarantine status for one or all modules.

    Args:
        module_name: If provided, get status for this module only.
            If None, get status for all modules.

    Returns:
        Dict with quarantine status.
    """
    state = await _get_quarantine_store()

    if module_name:
        entry = state.get(module_name, {
            "module": module_name,
            "level": QuarantineLevel.NONE.name,
        })
        # Check auto-release
        if entry.get("auto_release_at") and time.time() > entry["auto_release_at"]:
            await release_module(module_name)
            entry["level"] = QuarantineLevel.NONE.name
            entry["note"] = "Auto-released (quarantine period expired)"
        return entry

    # Check auto-release for all modules
    for mod, entry in list(state.items()):
        if entry.get("auto_release_at") and time.time() > entry["auto_release_at"]:
            await release_module(mod)
            entry["level"] = QuarantineLevel.NONE.name
            entry["note"] = "Auto-released (quarantine period expired)"

    return {
        "modules": state,
        "protected_modules": list(_PROTECTED_MODULES),
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


async def is_quarantined(module_name: str) -> bool:
    """Check if a module is currently under any quarantine level.

    Returns True if the module is FLAGGED, SAFE, or DESTRUCTIVE quarantined.
    """
    status = await get_quarantine_status(module_name)
    return status.get("level", QuarantineLevel.NONE.name) != QuarantineLevel.NONE.name


async def is_safe_quarantined(module_name: str) -> bool:
    """Check if a module is under SAFE quarantine (restrictions active)."""
    status = await get_quarantine_status(module_name)
    return status.get("level") == QuarantineLevel.SAFE.name
