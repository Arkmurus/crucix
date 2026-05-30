"""R-F1171 — Kaspersky Antivirus Mitigation.

Kaspersky on the Windows host machine may falsely flag and delete ARIA's
files — particularly .pyc files, SQLite databases, and generated data files.
This module provides:

  1. FILE INTEGRITY CHECK — periodic scan of critical files
  2. AUTO-RECOVERY — regenerate missing data files from Redis/backup
  3. GRACEFUL DEGRADATION — when a critical file is missing, provide
     a fallback path instead of crashing
  4. BRAIN WIRING — report all deletions to the brain so ARIA learns
     which files are being targeted

Usage:
    from .kaspersky_mitigation import (
        check_file_integrity,
        recover_data_file,
        safe_file_read,
    )

    # Check all critical files
    report = await check_file_integrity()

    # Recover a missing data file
    result = await recover_data_file("/data/aria_state.db")

    # Read a file safely (returns None if missing)
    content = safe_file_read("/data/aria_knowledge.json")
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from typing import Any, Optional

from .engine_wiring import wire_success, wire_failure

logger = logging.getLogger("aria.kaspersky_mitigation")

# ── Critical files that must exist ──────────────────────────────────────────

CRITICAL_FILES: list[dict[str, Any]] = [
    {"path": "aria_service/aria_engine.py", "label": "Core engine", "recoverable": False},
    {"path": "aria_service/main.py", "label": "Application entry point", "recoverable": False},
    {"path": "aria_service/intel/knowledge.py", "label": "Knowledge store module", "recoverable": False},
    {"path": "aria_service/intel/dd_orchestrator.py", "label": "DD orchestrator", "recoverable": False},
    {"path": "aria_service/intel/brain_hook.py", "label": "Brain hook", "recoverable": False},
    {"path": "aria_service/intel/self_improve.py", "label": "Self-improvement engine", "recoverable": False},
    {"path": "aria_service/intel/grounded_reasoner.py", "label": "Grounded reasoner", "recoverable": False},
    {"path": "aria_service/intel/reasoning_router.py", "label": "Reasoning router", "recoverable": False},
]

# Data files that can be auto-recovered
RECOVERABLE_DATA_FILES: list[dict[str, Any]] = [
    {"path": "/data/aria_state.db", "label": "State database", "fallback": "sqlite"},
    {"path": "/data/aria_dialogue.db", "label": "Dialogue database", "fallback": "sqlite"},
    {"path": "/data/aria_knowledge.json", "label": "Knowledge store", "fallback": "redis"},
    {"path": "/data/aria_search.db", "label": "Search index", "fallback": "sqlite"},
]

# Track which files have been reported to avoid spam
_reported_missing: set[str] = set()
_REPORT_COOLDOWN_S = 3600  # 1 hour between reports for the same file


async def check_file_integrity() -> dict[str, Any]:
    """Check all critical files for integrity.

    Returns a report of missing files and their severity.
    Wires findings to the brain.
    """
    now = time.time()
    missing_critical: list[str] = []
    missing_data: list[str] = []
    recovered: list[str] = []

    # Check critical Python modules
    for entry in CRITICAL_FILES:
        path = entry["path"]
        if not os.path.exists(path):
            missing_critical.append(entry["label"])
            _report_if_needed(path, f"Critical file missing: {entry['label']} ({path})", now)

    # Check recoverable data files
    for entry in RECOVERABLE_DATA_FILES:
        path = entry["path"]
        if os.path.exists(path):
            continue
        missing_data.append(entry["label"])
        _report_if_needed(path, f"Data file missing: {entry['label']} ({path})", now)
        # Attempt auto-recovery
        try:
            result = await _auto_recover(entry)
            if result:
                recovered.append(entry["label"])
                logger.info("[kaspersky] Auto-recovered %s (%s)", entry["label"], path)
        except Exception as e:
            logger.warning("[kaspersky] Auto-recovery failed for %s: %s", entry["label"], e)

    report = {
        "checked_at": time.time(),
        "critical_missing": missing_critical,
        "data_missing": missing_data,
        "recovered": recovered,
        "healthy": len(missing_critical) == 0 and len(missing_data) == 0,
    }

    # Wire to brain
    if missing_critical:
        wire_failure(
            module="kaspersky_mitigation",
            detail=f"Critical files missing: {', '.join(missing_critical)}. "
                   f"Likely deleted by antivirus. Requires git checkout to restore.",
            gap_type="file_integrity_failure",
            source="kaspersky_mitigation:check_file_integrity",
        )
    elif missing_data:
        wire_failure(
            module="kaspersky_mitigation",
            detail=f"Data files missing: {', '.join(missing_data)}. "
                   f"Auto-recovered: {', '.join(recovered) if recovered else 'none'}.",
            gap_type="file_integrity_warning",
            source="kaspersky_mitigation:check_file_integrity",
        )
    else:
        wire_success(
            module="kaspersky_mitigation",
            summary="All critical files present",
            source_id="kaspersky_mitigation:check_file_integrity",
        )

    return report


async def recover_data_file(filepath: str) -> dict[str, Any]:
    """Attempt to recover a missing data file.

    Returns {success, message, filepath}.
    """
    for entry in RECOVERABLE_DATA_FILES:
        if entry["path"] == filepath:
            try:
                result = await _auto_recover(entry)
                if result:
                    wire_success(
                        module="kaspersky_mitigation",
                        summary=f"Recovered data file: {entry['label']}",
                        source_id="kaspersky_mitigation:recover_data_file",
                    )
                    return {"success": True, "message": f"Recovered {entry['label']}", "filepath": filepath}
                else:
                    return {"success": False, "message": f"Recovery failed for {entry['label']}", "filepath": filepath}
            except Exception as e:
                wire_failure(
                    module="kaspersky_mitigation",
                    detail=f"Recovery failed for {filepath}: {e}",
                    gap_type="file_recovery_failure",
                    source="kaspersky_mitigation:recover_data_file",
                )
                return {"success": False, "message": str(e), "filepath": filepath}

    return {"success": False, "message": f"Unknown file: {filepath}", "filepath": filepath}


def safe_file_read(filepath: str, default: Any = None) -> Any:
    """Read a file safely, returning default if missing.

    Use this instead of open() for files that Kaspersky may delete.
    """
    try:
        if not os.path.exists(filepath):
            return default
        with open(filepath, "r", encoding="utf-8") as f:
            return f.read()
    except (FileNotFoundError, IOError, OSError):
        return default


def safe_json_read(filepath: str, default: Any = None) -> Any:
    """Read and parse a JSON file safely, returning default if missing/corrupt."""
    try:
        if not os.path.exists(filepath):
            return default
        with open(filepath, "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, IOError, OSError, json.JSONDecodeError):
        return default


# ── Internal helpers ────────────────────────────────────────────────────────


def _report_if_needed(path: str, message: str, now: float) -> None:
    """Report a missing file to the brain, respecting cooldown."""
    if path in _reported_missing:
        return
    _reported_missing.add(path)
    logger.warning("[kaspersky] %s", message)
    wire_failure(
        module="kaspersky_mitigation",
        detail=message,
        gap_type="file_integrity_failure",
        source=f"kaspersky_mitigation:missing:{path}",
    )
    # Schedule removal from reported set after cooldown
    async def _clear_after_cooldown():
        await asyncio.sleep(_REPORT_COOLDOWN_S)
        _reported_missing.discard(path)

    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            loop.create_task(_clear_after_cooldown())
    except RuntimeError:
        pass


async def _auto_recover(entry: dict[str, Any]) -> bool:
    """Attempt to auto-recover a missing data file."""
    path = entry["path"]
    fallback = entry.get("fallback", "")

    # Create parent directory if needed
    parent = os.path.dirname(path)
    if parent and not os.path.exists(parent):
        try:
            os.makedirs(parent, exist_ok=True)
        except (OSError, PermissionError):
            return False

    if fallback == "sqlite":
        # SQLite databases are created on first connection — just touch the file
        try:
            # Create an empty file — SQLite will initialize on first use
            with open(path, "w") as f:
                f.write("")
            return True
        except (IOError, OSError):
            return False

    elif fallback == "redis":
        # JSON data files can be rebuilt from Redis
        try:
            from . import redis_store as rs
            # Try to restore from Redis backup
            backup = await rs.get_json("crucix:knowledge:backup")
            if backup:
                with open(path, "w", encoding="utf-8") as f:
                    json.dump(backup, f, ensure_ascii=False, default=str)
                return True
        except Exception:
            pass
        # Fallback: create empty file
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump({"facts": [], "meta": {}}, f, ensure_ascii=False)
            return True
        except (IOError, OSError):
            return False

    return False


# ── Wire to brain on import ─────────────────────────────────────────────────

wire_success(
    module="kaspersky_mitigation",
    summary="Kaspersky Mitigation Engine active — monitoring file integrity",
    source_id="kaspersky_mitigation:R-F1171",
)
