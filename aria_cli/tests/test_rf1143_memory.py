"""R-F1143 — tests for session memory persistence.

Tests the file-based memory store: remember, recall, stats, and archiving.
Uses a temp directory for the memory store to avoid polluting ~/.aria/memory/.
"""
from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest

from aria_cli import memory as _mem


@pytest.fixture(autouse=True)
def _isolate_memory(monkeypatch: pytest.MonkeyPatch) -> None:
    """Redirect memory storage to a temp dir for each test."""
    tmp = Path(tempfile.mkdtemp())
    monkeypatch.setattr(_mem, "_MEMORY_DIR", tmp)
    monkeypatch.setattr(_mem, "_ACTIVE_FILE", "learnings.jsonl")
    monkeypatch.setattr(_mem, "_COLD_STORAGE_DIR", "cold")
    # Ensure dirs exist
    tmp.mkdir(parents=True, exist_ok=True)
    (tmp / "cold").mkdir(parents=True, exist_ok=True)


# ── remember ────────────────────────────────────────────────────────────────

def test_remember_basic() -> None:
    """remember stores an entry and returns confirmation."""
    result = _mem.remember("lesson", "always verify before claiming done", r_number="R-F1143")
    assert "remembered" in result
    assert "lesson" in result


def test_remember_persists() -> None:
    """remember actually writes to disk."""
    _mem.remember("pattern", "use Popen not run", tags=["subprocess", "validator"])
    entries = _mem._load_all()
    assert len(entries) == 1
    assert entries[0].entry_type == "pattern"
    assert "Popen" in entries[0].content


def test_remember_multiple_types() -> None:
    """remember handles all entry types."""
    for t in ("pattern", "decision", "lesson", "fact", "gap"):
        _mem.remember(t, f"test {t}")
    entries = _mem._load_all()
    assert len(entries) == 5
    assert {e.entry_type for e in entries} == {"pattern", "decision", "lesson", "fact", "gap"}


# ── recall ──────────────────────────────────────────────────────────────────

def test_recall_all() -> None:
    """recall returns all entries when no filter is given."""
    _mem.remember("lesson", "first lesson")
    _mem.remember("pattern", "a pattern")
    results = _mem.recall()
    assert len(results) == 2


def test_recall_by_type() -> None:
    """recall filters by entry_type."""
    _mem.remember("lesson", "lesson one")
    _mem.remember("pattern", "pattern one")
    _mem.remember("lesson", "lesson two")
    results = _mem.recall(entry_type="lesson")
    assert len(results) == 2
    assert all(r["entry_type"] == "lesson" for r in results)


def test_recall_by_query() -> None:
    """recall filters by content query."""
    _mem.remember("lesson", "always use type hints")
    _mem.remember("lesson", "never use bare excepts")
    results = _mem.recall(query="type hints")
    assert len(results) == 1
    assert "type hints" in results[0]["content"]


def test_recall_empty() -> None:
    """recall returns empty list when no matches."""
    results = _mem.recall(entry_type="decision")
    assert results == []


def test_recall_limit() -> None:
    """recall respects the limit parameter."""
    for i in range(20):
        _mem.remember("lesson", f"lesson {i}")
    results = _mem.recall(limit=5)
    assert len(results) == 5


# ── stats ───────────────────────────────────────────────────────────────────

def test_stats_empty() -> None:
    """stats returns zeros when no entries."""
    s = _mem.stats()
    assert s["total"] == 0
    assert s["by_type"] == {}


def test_stats_with_entries() -> None:
    """stats returns correct counts."""
    _mem.remember("lesson", "l1")
    _mem.remember("lesson", "l2")
    _mem.remember("pattern", "p1")
    s = _mem.stats()
    assert s["total"] == 3
    assert s["by_type"]["lesson"] == 2
    assert s["by_type"]["pattern"] == 1


# ── forget / archive ────────────────────────────────────────────────────────

def test_forget_nothing_to_archive() -> None:
    """forget returns clean message when nothing is old."""
    _mem.remember("lesson", "fresh lesson")
    result = _mem.forget(older_than_days=90)
    assert "no entries" in result


def test_forget_archives_old() -> None:
    """forget archives entries older than the threshold."""
    _mem.remember("lesson", "old lesson")
    # Manually set timestamp to 100 days ago
    entries = _mem._load_all()
    import time
    entries[0].timestamp = time.time() - 100 * 86400
    _mem._save_all(entries)
    # Now forget entries older than 30 days
    result = _mem.forget(older_than_days=30)
    assert "archived" in result
    assert "1" in result
    # Active store should be empty
    assert _mem._load_all() == []


# ── Entry dataclass ─────────────────────────────────────────────────────────

def test_memory_entry_to_dict() -> None:
    """MemoryEntry serializes to dict."""
    entry = _mem.MemoryEntry(
        entry_type="lesson",
        content="test content",
        r_number="R-F1143",
        tags=["test"],
    )
    d = entry.to_dict()
    assert d["entry_type"] == "lesson"
    assert d["content"] == "test content"
    assert d["r_number"] == "R-F1143"
    assert d["tags"] == ["test"]
    assert "timestamp" in d


def test_memory_entry_from_dict() -> None:
    """MemoryEntry deserializes from dict."""
    d = {
        "entry_type": "pattern",
        "content": "use Popen",
        "r_number": "R-F999",
        "tags": ["subprocess"],
        "timestamp": 1234567890.0,
    }
    entry = _mem.MemoryEntry.from_dict(d)
    assert entry.entry_type == "pattern"
    assert entry.content == "use Popen"
    assert entry.r_number == "R-F999"
    assert entry.tags == ["subprocess"]
    assert entry.timestamp == 1234567890.0
