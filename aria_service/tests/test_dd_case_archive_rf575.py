"""R-F575 — DD case-file deferred items unit tests.

Three concerns from R-F573's deferred list:
  1. SQLite cold-tier (aria_service/intel/dd_case_archive.py)
  2. Read-time canonical_entity_id backfill in list_reports()
  3. Split / merge operator-override endpoints + functions

This file covers (1) and (3) end-to-end with a per-test SQLite DB.
The backfill (2) is exercised by an integration test that exercises
list_reports() against a stub Redis.
"""
from __future__ import annotations

import os
import tempfile
import pytest


@pytest.fixture
def isolated_archive(monkeypatch, tmp_path):
    """Each test gets a fresh SQLite db under tmp_path."""
    db_path = str(tmp_path / "case_archive_rf575.db")
    monkeypatch.setenv("ARIA_DD_CASE_ARCHIVE_DB", db_path)
    # Force re-resolve in case of module-level caching (none today, but
    # be defensive).
    import importlib
    import aria_service.intel.dd_case_archive as mod
    importlib.reload(mod)
    yield mod
    # Cleanup
    try:
        os.remove(db_path)
    except OSError:
        pass


# ── Cold-tier SQLite ────────────────────────────────────────────────────────


def test_rf575_archive_entry_round_trip(isolated_archive):
    """A persisted entry must come back identical from lookup."""
    arc = isolated_archive
    entry = {
        "run_id": "dd_aaa111",
        "canonical_entity_id": "company:BR:07689002000189",
        "version_number": 1,
        "previous_run_id": None,
        "entity_name": "Embraer S.A.",
        "jurisdiction": "Brazil",
        "risk_classification": "GREEN",
        "generated_at": "2026-05-16T10:00:00Z",
    }
    arc.archive_entry(entry)
    rows = arc.lookup_by_canonical_id("company:BR:07689002000189")
    assert len(rows) == 1
    assert rows[0]["run_id"] == "dd_aaa111"
    assert rows[0]["entity_name"] == "Embraer S.A."
    assert rows[0]["version_number"] == 1


def test_rf575_archive_upsert_replaces_existing(isolated_archive):
    """Writing the same run_id twice with new fields must overwrite
    (upsert), not duplicate."""
    arc = isolated_archive
    entry_v1 = {
        "run_id": "dd_bbb222", "canonical_entity_id": "company:US:X",
        "version_number": 1, "entity_name": "Foo", "jurisdiction": "US",
        "risk_classification": "AMBER", "generated_at": "2026-05-16T10:00:00Z",
    }
    entry_v2 = {
        "run_id": "dd_bbb222", "canonical_entity_id": "company:US:X",
        "version_number": 2, "entity_name": "Foo Inc",
        "jurisdiction": "US", "risk_classification": "RED",
        "generated_at": "2026-05-16T11:00:00Z",
    }
    arc.archive_entry(entry_v1)
    arc.archive_entry(entry_v2)
    rows = arc.lookup_by_canonical_id("company:US:X")
    assert len(rows) == 1
    assert rows[0]["version_number"] == 2
    assert rows[0]["risk_classification"] == "RED"


def test_rf575_archive_multiple_canonical_ids(isolated_archive):
    arc = isolated_archive
    for i, cid in enumerate(["company:BR:A", "company:BR:A", "company:US:B"]):
        arc.archive_entry({
            "run_id": f"dd_{i}",
            "canonical_entity_id": cid,
            "version_number": 1,
            "entity_name": "x",
            "jurisdiction": cid.split(":")[1],
            "risk_classification": "GREEN",
            "generated_at": f"2026-05-1{i}T10:00:00Z",
        })
    a_rows = arc.lookup_by_canonical_id("company:BR:A")
    b_rows = arc.lookup_by_canonical_id("company:US:B")
    assert len(a_rows) == 2
    assert len(b_rows) == 1


def test_rf575_archive_lookup_sorts_newest_first(isolated_archive):
    arc = isolated_archive
    arc.archive_entry({"run_id": "old", "canonical_entity_id": "x",
                       "generated_at": "2026-01-01T00:00:00Z",
                       "version_number": 1, "entity_name": "n",
                       "jurisdiction": "j", "risk_classification": "GREEN"})
    arc.archive_entry({"run_id": "new", "canonical_entity_id": "x",
                       "generated_at": "2026-05-01T00:00:00Z",
                       "version_number": 2, "entity_name": "n",
                       "jurisdiction": "j", "risk_classification": "RED"})
    rows = arc.lookup_by_canonical_id("x")
    assert [r["run_id"] for r in rows] == ["new", "old"]


def test_rf575_archive_update_canonical_id(isolated_archive):
    """Operator-override split / merge rewrites canonical_entity_id."""
    arc = isolated_archive
    arc.archive_entry({"run_id": "r1", "canonical_entity_id": "old:cid",
                       "generated_at": "2026-05-16T10:00:00Z",
                       "version_number": 1, "entity_name": "n",
                       "jurisdiction": "j", "risk_classification": "GREEN"})
    ok = arc.update_canonical_id("r1", "new:cid")
    assert ok is True
    assert arc.lookup_by_canonical_id("old:cid") == []
    moved = arc.lookup_by_canonical_id("new:cid")
    assert len(moved) == 1


def test_rf575_archive_purge_expired(isolated_archive, monkeypatch):
    """Entries older than retention_days drop out on purge."""
    arc = isolated_archive
    import time as _time
    # Bypass datetime by setting archived_at directly via raw INSERT
    with arc._connect() as conn:
        conn.execute(
            """INSERT INTO case_archive
               (run_id, canonical_entity_id, version_number,
                entity_name, jurisdiction, risk_classification,
                generated_at, archived_at)
               VALUES ('old_row', 'x', 1, 'n', 'j', 'GREEN',
                       '2025-01-01T00:00:00Z', ?)""",
            (_time.time() - 200 * 24 * 3600,),  # 200 days ago
        )
        conn.execute(
            """INSERT INTO case_archive
               (run_id, canonical_entity_id, version_number,
                entity_name, jurisdiction, risk_classification,
                generated_at, archived_at)
               VALUES ('fresh_row', 'x', 2, 'n', 'j', 'GREEN',
                       '2026-05-16T00:00:00Z', ?)""",
            (_time.time() - 1 * 24 * 3600,),  # 1 day ago
        )
    purged = arc.purge_expired(retention_days=90)
    assert purged == 1
    rows = arc.lookup_by_canonical_id("x")
    assert [r["run_id"] for r in rows] == ["fresh_row"]


def test_rf575_archive_stats(isolated_archive):
    arc = isolated_archive
    arc.archive_entry({"run_id": "r1", "canonical_entity_id": "a",
                       "generated_at": "2026-05-16T10:00:00Z",
                       "version_number": 1, "entity_name": "n",
                       "jurisdiction": "j", "risk_classification": "GREEN"})
    arc.archive_entry({"run_id": "r2", "canonical_entity_id": "b",
                       "generated_at": "2026-05-16T10:00:00Z",
                       "version_number": 1, "entity_name": "n",
                       "jurisdiction": "j", "risk_classification": "GREEN"})
    s = arc.stats()
    assert s["total_rows"] == 2
    assert s["distinct_cases"] == 2


def test_rf575_archive_entry_handles_missing_run_id(isolated_archive):
    """Defensive — bad input must not crash the caller."""
    arc = isolated_archive
    arc.archive_entry({})           # no run_id → silently skipped
    arc.archive_entry(None)         # None → silently skipped
    assert arc.stats()["total_rows"] == 0


def test_rf575_lookup_empty_canonical_returns_empty(isolated_archive):
    arc = isolated_archive
    assert arc.lookup_by_canonical_id("") == []
    assert arc.lookup_by_canonical_id(None) == []


# ── Split / merge — pure logic ──────────────────────────────────────────────


def test_rf575_split_and_merge_via_cold_tier_helpers(isolated_archive):
    """End-to-end split→merge using only the cold-tier update primitive."""
    arc = isolated_archive
    # Two runs both wrongly collapsed to person:john_smith
    for rid in ("dd_smith_a", "dd_smith_b"):
        arc.archive_entry({
            "run_id": rid,
            "canonical_entity_id": "person:john_smith:GB:????",
            "version_number": 1,
            "entity_name": "John Smith",
            "jurisdiction": "GB",
            "risk_classification": "GREEN",
            "generated_at": "2026-05-16T10:00:00Z",
        })
    assert len(arc.lookup_by_canonical_id("person:john_smith:GB:????")) == 2
    # Split: peel dd_smith_b off into a new id
    arc.update_canonical_id("dd_smith_b", "person:john_smith:GB:????:split:1")
    assert len(arc.lookup_by_canonical_id("person:john_smith:GB:????")) == 1
    assert len(arc.lookup_by_canonical_id("person:john_smith:GB:????:split:1")) == 1
    # Merge: reverse the split
    arc.update_canonical_id("dd_smith_b", "person:john_smith:GB:????")
    assert len(arc.lookup_by_canonical_id("person:john_smith:GB:????")) == 2
