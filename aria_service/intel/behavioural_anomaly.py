"""R-F1136 — Behavioural anomaly detection.

Learns ARIA's normal operation patterns and detects anomalies that could
indicate a compromised module. Combines a LEARNED baseline (what modules
normally do) with a HARD allowlist (what modules are ALLOWED to do).

The learned baseline is never trusted alone — an attacker who acts slowly
can poison it. The hard allowlist is the ground truth for critical actions.

Detection dimensions:
1. File access patterns — what files does each module normally read/write?
2. Network call patterns — what hosts does each module normally contact?
3. Import patterns — what modules does each module normally import?
4. Execution cadence — how often does each module normally run?

Response: FLAG only (never auto-kill). Feeds into quarantine_network for
graduated response.

Usage:
    from aria_service.intel.behavioural_anomaly import (
        record_observation,
        check_anomaly,
        get_baseline,
        get_anomaly_log,
    )

    # Record normal behaviour:
    await record_observation("document_reader", "file_read", "/data/doc.pdf")

    # Check for anomaly:
    result = await check_anomaly("document_reader", "file_write", "/etc/passwd")
    if result.anomaly:
        await flag_module("document_reader", result.reason)
"""
from __future__ import annotations

import hashlib
import logging
import os
import time
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any, Optional

logger = logging.getLogger("aria.behavioural_anomaly")

# Redis keys
_BASELINE_KEY = "crucix:security:behavioural_baseline"
_ANOMALY_LOG_KEY = "crucix:security:anomaly_log"

# Hard allowlist: modules and the files/actions they are ALLOWED to access.
# This is the ground truth — never trust learned baseline alone.
# Format: {module_name: {action_type: [allowed_patterns]}}
HARD_ALLOWLIST: dict[str, dict[str, list[str]]] = {
    "document_reader": {
        "file_read": ["/data/*", "/tmp/*", "*.pdf", "*.docx", "*.xlsx", "*.txt"],
        "file_write": ["/data/*", "/tmp/*"],
    },
    "content_scanner": {
        "file_read": ["/data/*", "/tmp/*"],
        "file_write": ["/data/quarantine/*", "/tmp/*"],
    },
    "self_coder": {
        "file_read": ["aria_service/**/*.py", "scripts/**/*.py", "/data/*"],
        "file_write": ["aria_service/**/*.py", "scripts/**/*.py"],
    },
    "email_reader": {
        "network_call": ["imap.gmail.com", "outlook.office365.com", "mail.*"],
        "file_read": ["/data/*", "/tmp/*"],
    },
    "web_search": {
        "network_call": ["*.google.com", "*.bing.com", "*.duckduckgo.com",
                         "api.search.*", "serp.*"],
    },
    "researcher": {
        "network_call": ["*.gov", "*.mil", "*.int", "*.org", "*.com",
                         "api.*", "www.*"],
    },
}

# Anomaly thresholds
MIN_OBSERVATIONS_FOR_BASELINE = 5  # Need at least this many to establish baseline
MAX_BASELINE_AGE_S = 86400 * 7      # 7 days — baseline expires
ANOMALY_LOG_MAX = 1000


class AnomalyResult:
    """Result of an anomaly check."""

    def __init__(
        self,
        anomaly: bool,
        reason: str = "",
        confidence: str = "LOW",
        details: Optional[dict[str, Any]] = None,
    ):
        self.anomaly = anomaly
        self.reason = reason
        self.confidence = confidence
        self.details = details or {}

    def to_dict(self) -> dict[str, Any]:
        return {
            "anomaly": self.anomaly,
            "reason": self.reason,
            "confidence": self.confidence,
            "details": self.details,
        }


def _normalize_path(path: str) -> str:
    """Normalize a file path for pattern matching."""
    return path.replace("\\", "/")


def _matches_pattern(path: str, pattern: str) -> bool:
    """Check if a path matches a glob-like pattern."""
    path = _normalize_path(path)
    pattern = _normalize_path(pattern)

    if pattern.endswith("/*"):
        # Directory wildcard — match any file in directory
        return path.startswith(pattern[:-1])
    if "**" in pattern:
        # Recursive wildcard — match any path with the prefix and suffix.
        # The ** matches any number of directory levels.
        parts = pattern.split("**")
        prefix = parts[0]
        suffix = parts[1] if len(parts) > 1 else ""
        if suffix:
            # suffix is like "/*.py" — extract the actual suffix pattern
            suffix = suffix.lstrip("/*")
            if suffix.startswith("."):
                # Extension match: path must start with prefix and end with suffix
                return path.startswith(prefix) and path.endswith(suffix)
            return path.startswith(prefix) and path.endswith(suffix)
        return path.startswith(prefix)
    if pattern.startswith("*."):
        # Extension wildcard — match any file with extension
        return path.endswith(pattern[1:])
    # Exact match or prefix match
    return path == pattern or path.startswith(pattern)


def _check_hard_allowlist(
    module_name: str,
    action_type: str,
    target: str,
) -> Optional[str]:
    """Check if an action is allowed by the hard allowlist.

    Returns None if allowed, or a reason string if blocked.
    """
    module_rules = HARD_ALLOWLIST.get(module_name, {})
    allowed_patterns = module_rules.get(action_type, [])

    if not allowed_patterns:
        # No rules defined for this module+action — check learned baseline only
        return None

    for pattern in allowed_patterns:
        if _matches_pattern(target, pattern):
            return None  # Allowed

    return f"Action '{action_type}' on '{target}' not in hard allowlist for '{module_name}'"


async def _get_baseline_store() -> dict[str, Any]:
    """Get the behavioural baseline from Redis."""
    try:
        from . import redis_store as rs
        return await rs.get_json(_BASELINE_KEY) or {}
    except Exception:
        return {}


async def _set_baseline_store(state: dict[str, Any]) -> None:
    """Set the behavioural baseline in Redis."""
    try:
        from . import redis_store as rs
        await rs.set_json(_BASELINE_KEY, state, ex=MAX_BASELINE_AGE_S)
    except Exception as e:
        logger.warning("[behavioural_anomaly] Redis store failed: %s", e)


async def record_observation(
    module_name: str,
    action_type: str,
    target: str,
) -> None:
    """Record a normal behavioural observation.

    This builds the learned baseline over time. Called by modules during
    normal operation to establish what "normal" looks like.

    Args:
        module_name: Name of the module performing the action.
        action_type: Type of action (file_read, file_write, network_call, import).
        target: The target of the action (file path, hostname, module name).
    """
    state = await _get_baseline_store()

    module_key = f"{module_name}:{action_type}"
    if module_key not in state:
        state[module_key] = {
            "module": module_name,
            "action_type": action_type,
            "targets": {},
            "total_observations": 0,
            "first_seen": datetime.now(timezone.utc).isoformat(),
        }

    entry = state[module_key]
    entry["total_observations"] += 1
    entry["last_seen"] = datetime.now(timezone.utc).isoformat()

    # Track target frequency
    if target not in entry["targets"]:
        entry["targets"][target] = 0
    entry["targets"][target] += 1

    # Prune rarely-seen targets (keep baseline lean)
    if len(entry["targets"]) > 100:
        # Remove targets seen fewer than 2 times
        entry["targets"] = {
            k: v for k, v in entry["targets"].items() if v >= 2
        }

    await _set_baseline_store(state)


async def check_anomaly(
    module_name: str,
    action_type: str,
    target: str,
) -> AnomalyResult:
    """Check if an action is anomalous.

    Two-layer check:
    1. HARD ALLOWLIST — if the action is not in the allowlist, it's an anomaly
    2. LEARNED BASELINE — if the action is in the allowlist but never seen
       before in the baseline, it's a low-confidence anomaly

    Args:
        module_name: Name of the module performing the action.
        action_type: Type of action.
        target: The target of the action.

    Returns:
        AnomalyResult with anomaly=True if the action is suspicious.
    """
    # Layer 1: Hard allowlist check
    allowlist_reason = _check_hard_allowlist(module_name, action_type, target)
    if allowlist_reason:
        return AnomalyResult(
            anomaly=True,
            reason=allowlist_reason,
            confidence="HIGH",
            details={"check": "hard_allowlist", "module": module_name,
                     "action": action_type, "target": target},
        )

    # Layer 2: Learned baseline check
    state = await _get_baseline_store()
    module_key = f"{module_name}:{action_type}"
    entry = state.get(module_key)

    if entry is None:
        # No baseline yet — not anomalous, just unknown
        return AnomalyResult(
            anomaly=False,
            reason="No baseline established yet",
            confidence="LOW",
        )

    if entry["total_observations"] < MIN_OBSERVATIONS_FOR_BASELINE:
        # Not enough data yet
        return AnomalyResult(
            anomaly=False,
            reason=f"Baseline building ({entry['total_observations']}/{MIN_OBSERVATIONS_FOR_BASELINE})",
            confidence="LOW",
        )

    if target not in entry["targets"]:
        # Target never seen before — low-confidence anomaly
        return AnomalyResult(
            anomaly=True,
            reason=f"Novel target '{target}' for {module_name}.{action_type} "
                   f"(not in baseline of {len(entry['targets'])} known targets)",
            confidence="MEDIUM",
            details={"check": "learned_baseline", "module": module_name,
                     "action": action_type, "target": target,
                     "known_targets": len(entry["targets"])},
        )

    return AnomalyResult(
        anomaly=False,
        reason="Normal behaviour",
        confidence="HIGH",
    )


async def log_anomaly(anomaly: AnomalyResult) -> None:
    """Log an anomaly to Redis for audit."""
    if not anomaly.anomaly:
        return

    try:
        from . import redis_store as rs
        log = await rs.get_json(_ANOMALY_LOG_KEY) or []
        log.insert(0, {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "reason": anomaly.reason,
            "confidence": anomaly.confidence,
            "details": anomaly.details,
        })
        await rs.set_json(_ANOMALY_LOG_KEY, log[:ANOMALY_LOG_MAX], ex=86400 * 30)
    except Exception:
        pass

    # Wire to brain
    try:
        from .engine_wiring import wire_failure
        wire_failure(
            module="behavioural_anomaly",
            detail=f"Anomaly detected: {anomaly.reason} (confidence: {anomaly.confidence})",
            gap_type="security_threat",
            source=f"behavioural_anomaly:{anomaly.details.get('module', 'unknown')}",
        )
    except Exception:
        logger.debug("[behavioural_anomaly] brain wiring failed", exc_info=True)


async def get_baseline(module_name: Optional[str] = None) -> dict[str, Any]:
    """Get the behavioural baseline.

    Args:
        module_name: If provided, get baseline for this module only.

    Returns:
        Dict with baseline data.
    """
    state = await _get_baseline_store()

    if module_name:
        return {
            k: v for k, v in state.items()
            if v.get("module") == module_name
        }

    return state


async def get_anomaly_log(limit: int = 50) -> list[dict[str, Any]]:
    """Get recent anomaly log entries."""
    try:
        from . import redis_store as rs
        log = await rs.get_json(_ANOMALY_LOG_KEY) or []
        return log[:limit]
    except Exception:
        return []
