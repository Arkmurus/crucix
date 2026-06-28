"""
ARIA Organism Audit Trail — R-F2121
====================================
Tracks every change, addition, or modification to each aria organism.
Each entry records: timestamp, organ, file, change type, R-number, summary.

Usage:
    from .audit_trail import audit
    audit(organ="intel", file="voice_transcribe.py", change_type="wiring",
          r_number="R-F2112", summary="Added wire_success/wire_failure to transcribe_audio")

Organs: wa, intel, web, searxng
Change types: wiring, breaker, auth, security, reliability, infrastructure, bugfix, refactor
"""
from __future__ import annotations

import json
import logging
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

logger = logging.getLogger("aria.audit_trail")

_AUDIT_DIR = Path(os.getenv("ARIA_AUDIT_DIR", "/data/audit"))
_AUDIT_FILE = _AUDIT_DIR / "organism_audit.jsonl"
_MAX_ENTRIES = 10000

# In-memory buffer for fast access
_audit_buffer: list[dict] = []
_dirty = False


def _ensure_dir() -> None:
    """Ensure the audit directory exists."""
    try:
        _AUDIT_DIR.mkdir(parents=True, exist_ok=True)
    except Exception as e:
        logger.debug("audit_trail: cannot create dir %s: %s", _AUDIT_DIR, e)


def _load() -> list[dict]:
    """Load audit entries from disk."""
    global _audit_buffer
    if _audit_buffer:
        return _audit_buffer
    try:
        _ensure_dir()
        if _AUDIT_FILE.exists():
            with open(_AUDIT_FILE, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line:
                        try:
                            _audit_buffer.append(json.loads(line))
                        except json.JSONDecodeError:
                            pass
    except Exception as e:
        logger.debug("audit_trail: load failed: %s", e)
    return _audit_buffer


def _flush() -> None:
    """Flush buffer to disk."""
    global _dirty
    if not _dirty:
        return
    try:
        _ensure_dir()
        with open(_AUDIT_FILE, "w", encoding="utf-8") as f:
            for entry in _audit_buffer[-_MAX_ENTRIES:]:
                f.write(json.dumps(entry, default=str) + "\n")
        _dirty = False
    except Exception as e:
        logger.debug("audit_trail: flush failed: %s", e)


def audit(
    organ: str,
    file: str,
    change_type: str,
    r_number: str,
    summary: str,
    detail: str = "",
    verified_by: str = "",
) -> None:
    """Record an audit entry for a change to an aria organism.

    Args:
        organ: One of 'wa', 'intel', 'web', 'searxng'
        file: The file that was changed
        change_type: Type of change (wiring, breaker, auth, security, etc.)
        r_number: The R-number that made the change
        summary: Short description of the change
        detail: Optional longer description
        verified_by: How the change was verified (e.g. 'py_compile + test')
    """
    global _dirty
    entry = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "organ": organ,
        "file": file,
        "change_type": change_type,
        "r_number": r_number,
        "summary": summary[:200],
        "detail": detail[:1000] if detail else "",
        "verified_by": verified_by[:200] if verified_by else "",
    }
    _load()
    _audit_buffer.append(entry)
    _dirty = True
    _flush()
    logger.info("[audit] %s %s %s: %s", organ, change_type, file, summary[:80])


def get_history(organ: Optional[str] = None,
                change_type: Optional[str] = None,
                limit: int = 50) -> list[dict]:
    """Get audit history, optionally filtered by organ or change type."""
    entries = _load()
    if organ:
        entries = [e for e in entries if e.get("organ") == organ]
    if change_type:
        entries = [e for e in entries if e.get("change_type") == change_type]
    return entries[-limit:]


def get_stats() -> dict:
    """Get audit statistics per organ."""
    entries = _load()
    stats = {"total": len(entries), "organs": {}, "change_types": {}}
    for e in entries:
        org = e.get("organ", "unknown")
        ct = e.get("change_type", "unknown")
        stats["organs"][org] = stats["organs"].get(org, 0) + 1
        stats["change_types"][ct] = stats["change_types"].get(ct, 0) + 1
    return stats
