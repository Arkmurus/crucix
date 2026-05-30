"""R-F1143 — session memory persistence for the ARIA Coder CLI.

ARIA learns from every session. This module provides a simple file-based
memory store that persists key learnings, patterns, and decisions between
sessions. When the brain is reachable, learnings are also synced to the
live brain via /api/aria/brain/signal.

Design:
- File-based (per CLAUDE.md §6: files + LLM only, no paid persistence)
- Append-only JSONL in ~/.aria/memory/
- Each entry has a type (pattern, decision, lesson, fact, gap)
- Entries are tagged with the R-number and session context
- Auto-prune: entries older than 90 days are archived to cold storage
- Brain sync: best-effort POST to /api/aria/brain/signal when reachable
"""
from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path

# ── Constants ────────────────────────────────────────────────────────────────

_MEMORY_DIR = Path.home() / ".aria" / "memory"
_ACTIVE_FILE = "learnings.jsonl"
_COLD_STORAGE_DIR = "cold"
_MAX_ACTIVE_ENTRIES = 1000
_MAX_ENTRY_AGE_DAYS = 90
_MAX_OUTPUT_ENTRIES = 50


@dataclass
class MemoryEntry:
    """A single learning or observation from a coding session."""

    entry_type: str  # pattern, decision, lesson, fact, gap
    content: str
    r_number: str = ""
    session_id: str = ""
    tags: list[str] = field(default_factory=list)
    timestamp: float = field(default_factory=time.time)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "MemoryEntry":
        return cls(
            entry_type=d.get("entry_type", "lesson"),
            content=d.get("content", ""),
            r_number=d.get("r_number", ""),
            session_id=d.get("session_id", ""),
            tags=d.get("tags", []),
            timestamp=d.get("timestamp", time.time()),
        )


# ── Storage ──────────────────────────────────────────────────────────────────

def _ensure_dirs() -> Path:
    _MEMORY_DIR.mkdir(parents=True, exist_ok=True)
    (_MEMORY_DIR / _COLD_STORAGE_DIR).mkdir(parents=True, exist_ok=True)
    return _MEMORY_DIR


def _active_path() -> Path:
    return _ensure_dirs() / _ACTIVE_FILE


def _load_all() -> list[MemoryEntry]:
    p = _active_path()
    if not p.exists():
        return []
    entries: list[MemoryEntry] = []
    for line in p.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            entries.append(MemoryEntry.from_dict(json.loads(line)))
        except Exception:
            continue
    return entries


def _save_all(entries: list[MemoryEntry]) -> None:
    p = _active_path()
    p.write_text(
        "\n".join(json.dumps(e.to_dict(), ensure_ascii=False) for e in entries),
        encoding="utf-8",
    )


def _archive_old(entries: list[MemoryEntry]) -> list[MemoryEntry]:
    """Move entries older than _MAX_ENTRY_AGE_DAYS to cold storage."""
    cutoff = time.time() - _MAX_ENTRY_AGE_DAYS * 86400
    active = [e for e in entries if e.timestamp >= cutoff]
    old = [e for e in entries if e.timestamp < cutoff]
    if old:
        ts = time.strftime("%Y%m%d_%H%M%S")
        cold_path = _MEMORY_DIR / _COLD_STORAGE_DIR / f"archive_{ts}.jsonl"
        cold_path.write_text(
            "\n".join(json.dumps(e.to_dict(), ensure_ascii=False) for e in old),
            encoding="utf-8",
        )
    return active


# ── Brain sync (best-effort) ─────────────────────────────────────────────────

def _brain_enabled() -> bool:
    """Check if brain sync is configured."""
    token = (os.getenv("ARIA_INTERNAL_TOKEN") or "").strip()
    return bool(token)


def _sync_to_brain(entry: MemoryEntry) -> None:
    """Best-effort POST to the live brain."""
    if not _brain_enabled():
        return
    try:
        import httpx

        url = (
            os.getenv("ARIA_SERVICE_URL") or "https://aria-intel.fly.dev"
        ).rstrip("/") + "/api/aria/brain/signal"
        token = (os.getenv("ARIA_INTERNAL_TOKEN") or "").strip()
        payload = {
            "content": f"[memory] {entry.entry_type}: {entry.content[:400]}",
            "source": "aria_cli_memory",
            "signal_type": f"memory_{entry.entry_type}",
            "metadata": {
                "r_number": entry.r_number,
                "tags": entry.tags[:20],
                "entry_type": entry.entry_type,
            },
        }
        httpx.post(
            url, json=payload,
            headers={"Authorization": f"Bearer {token}"},
            timeout=8.0,
        )
    except Exception:
        pass  # best-effort, never block


# ── Public API ───────────────────────────────────────────────────────────────

def remember(entry_type: str, content: str, r_number: str = "",
             session_id: str = "", tags: list[str] | None = None) -> str:
    """Store a learning and return a short confirmation.

    entry_type: pattern, decision, lesson, fact, gap
    """
    entry = MemoryEntry(
        entry_type=entry_type,
        content=content,
        r_number=r_number,
        session_id=session_id,
        tags=tags or [],
    )
    entries = _load_all()
    entries.append(entry)
    # Prune if over limit
    if len(entries) > _MAX_ACTIVE_ENTRIES:
        entries = entries[-_MAX_ACTIVE_ENTRIES:]
    # Archive old entries
    entries = _archive_old(entries)
    _save_all(entries)
    # Best-effort brain sync
    _sync_to_brain(entry)
    return f"remembered: {entry_type} — {content[:80]}"


def recall(entry_type: str = "", query: str = "", limit: int = 10) -> list[dict]:
    """Retrieve recent memory entries, optionally filtered by type or content."""
    entries = _load_all()
    if entry_type:
        entries = [e for e in entries if e.entry_type == entry_type]
    if query:
        q = query.lower()
        entries = [e for e in entries if q in e.content.lower()]
    entries = entries[-min(limit, _MAX_OUTPUT_ENTRIES):]
    return [e.to_dict() for e in entries]


def forget(older_than_days: int = 90) -> str:
    """Archive entries older than the given number of days."""
    entries = _load_all()
    cutoff = time.time() - older_than_days * 86400
    old = [e for e in entries if e.timestamp < cutoff]
    if not old:
        return "no entries to archive"
    entries = _archive_old(entries)
    _save_all(entries)
    return f"archived {len(old)} entries older than {older_than_days} days"


def stats() -> dict:
    """Return memory statistics."""
    entries = _load_all()
    by_type: dict[str, int] = {}
    for e in entries:
        by_type[e.entry_type] = by_type.get(e.entry_type, 0) + 1
    return {
        "total": len(entries),
        "by_type": by_type,
        "oldest": min((e.timestamp for e in entries), default=0),
        "newest": max((e.timestamp for e in entries), default=0),
    }
