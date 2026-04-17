"""Tests for memory replication — daily forensic snapshot + restore.

End-to-end roundtrip, safety invariants on restore, stats shape,
autonomous wiring.
"""
from __future__ import annotations

import asyncio
import gzip
import json
import os
from pathlib import Path

import pytest


# ═══════════════════════════════════════════════════════════════════════
# 1. Summary + static shape
# ═══════════════════════════════════════════════════════════════════════

def test_summary_has_required_fields():
    from aria_service.learning import memory_replication
    s = memory_replication.summary()
    assert s["critical_keys_tracked"] >= 20
    assert "backup_dir" in s
    assert s["retention_days"] >= 7


def test_critical_keys_registry_covers_core_state():
    """The registry must include mastery, quarantine, mem0, DD index,
    bright-lines, verification, and the learning loop state."""
    from aria_service.learning.memory_replication import _CRITICAL_KEYS
    keys = set(_CRITICAL_KEYS)
    required_tokens = (
        "mastery", "quarantine", "mem0", "dd:report_index",
        "bright_lines", "verification", "journal", "style",
    )
    combined = " ".join(keys)
    for token in required_tokens:
        assert token in combined, f"memory backup registry missing token {token!r}"


# ═══════════════════════════════════════════════════════════════════════
# 2. Snapshot → list → restore (dry-run) roundtrip
# ═══════════════════════════════════════════════════════════════════════

def test_backup_and_list_roundtrip(tmp_path, monkeypatch):
    """Write a backup file directly, confirm list_backups sees it,
    and restore_from_backup dry-run returns the expected shape."""
    from aria_service.learning import memory_replication
    # Point the module at a temp dir so we don't pollute /data
    monkeypatch.setattr(memory_replication, "_BACKUP_DIR", Path(tmp_path))

    # Fabricate a snapshot file
    snapshot = {
        "created_at": "2026-04-18T04:00:00+00:00",
        "keys": {
            "crucix:aria:student:mastery": {
                "type": "json",
                "value": {"procurement": {"score": 0.88}},
            },
            "crucix:aria:quarantined_runs": {
                "type": "json",
                "value": {"dd_abc123": {"reason": "test"}},
            },
        },
    }
    out_file = Path(tmp_path) / "2026-04-18.json.gz"
    with gzip.open(out_file, "wb") as gz:
        gz.write(json.dumps(snapshot).encode("utf-8"))

    async def do_list():
        return await memory_replication.list_backups()

    items = asyncio.run(do_list())
    assert len(items) == 1
    assert items[0]["date"] == "2026-04-18"
    assert items[0]["size_bytes"] > 0

    # Dry-run restore — must NOT touch Redis
    async def do_restore():
        return await memory_replication.restore_from_backup(
            date="2026-04-18", keys=None, dry_run=True,
        )

    r = asyncio.run(do_restore())
    assert r["dry_run"] is True
    assert r["would_restore"] == 2
    assert r["actually_restored"] == 0
    assert r["errors"] == []


def test_restore_rejects_missing_date(tmp_path, monkeypatch):
    from aria_service.learning import memory_replication
    monkeypatch.setattr(memory_replication, "_BACKUP_DIR", Path(tmp_path))

    async def do_restore():
        return await memory_replication.restore_from_backup(
            date="2026-01-01", keys=None, dry_run=True,
        )

    r = asyncio.run(do_restore())
    assert any("not found" in e.lower() for e in r["errors"])


def test_restore_key_subset_filters_correctly(tmp_path, monkeypatch):
    """Passing `keys=[...]` filters the snapshot to only that subset."""
    from aria_service.learning import memory_replication
    monkeypatch.setattr(memory_replication, "_BACKUP_DIR", Path(tmp_path))

    snapshot = {
        "keys": {
            "crucix:aria:student:mastery":    {"type": "json", "value": {"x": 1}},
            "crucix:aria:quarantined_runs":   {"type": "json", "value": {"y": 2}},
            "crucix:learning:style:exemplars": {"type": "json", "value": {"z": 3}},
        },
    }
    out_file = Path(tmp_path) / "2026-04-18.json.gz"
    with gzip.open(out_file, "wb") as gz:
        gz.write(json.dumps(snapshot).encode("utf-8"))

    async def do_restore():
        return await memory_replication.restore_from_backup(
            date="2026-04-18",
            keys=["crucix:aria:student:mastery"],
            dry_run=True,
        )

    r = asyncio.run(do_restore())
    assert r["would_restore"] == 1  # Only mastery restored, not the other two


# ═══════════════════════════════════════════════════════════════════════
# 3. Stats
# ═══════════════════════════════════════════════════════════════════════

def test_get_stats_returns_expected_keys():
    from aria_service.learning import memory_replication

    async def run():
        return await memory_replication.get_stats()

    s = asyncio.run(run())
    for k in ("runs_24h", "keys_saved_24h", "bytes_24h", "last_run_at",
              "total_backups_on_disk", "total_bytes_on_disk",
              "total_kb_on_disk", "oldest_backup", "newest_backup",
              "retention_days", "backup_dir"):
        assert k in s


# ═══════════════════════════════════════════════════════════════════════
# 4. Wiring — tasks.py + tasks.yaml
# ═══════════════════════════════════════════════════════════════════════

def test_tasks_py_dispatches_memory_replication():
    from pathlib import Path
    src = Path(__file__).resolve().parents[1] / "autonomous" / "tasks.py"
    text = src.read_text(encoding="utf-8")
    assert '"memory_replication"' in text


def test_tasks_yaml_has_memory_backup_daily_enabled():
    import yaml
    from pathlib import Path
    p = Path(__file__).resolve().parents[1] / "autonomous" / "tasks.yaml"
    data = yaml.safe_load(p.read_text(encoding="utf-8"))
    task_ids = {t["id"]: t for t in data.get("tasks", [])}
    assert "MEMORY-BACKUP-DAILY" in task_ids
    t = task_ids["MEMORY-BACKUP-DAILY"]
    assert t["enabled"] is True
    assert t.get("cost_cap_usd", 0.0) == 0.0


# ═══════════════════════════════════════════════════════════════════════
# 5. Aggregator + dashboard wiring
# ═══════════════════════════════════════════════════════════════════════

def test_learning_stats_aggregator_includes_memory_backup():
    from pathlib import Path
    src = (Path(__file__).resolve().parents[1] / "routes" / "aria.py").read_text(encoding="utf-8")
    marker = "async def learning_stats_ep"
    idx = src.find(marker)
    snippet = src[idx:idx + 5000] if idx >= 0 else ""
    assert "memory_backup" in snippet


def test_dashboard_renders_memory_backup_row():
    from pathlib import Path
    html_path = Path(__file__).resolve().parents[2] / "public" / "aria-brain.html"
    html = html_path.read_text(encoding="utf-8")
    assert "d.memory_backup" in html or "Memory backups on disk" in html


def test_endpoints_registered():
    """The three backup endpoints must be registered."""
    from pathlib import Path
    src = (Path(__file__).resolve().parents[1] / "routes" / "aria.py").read_text(encoding="utf-8")
    assert '/memory/backup/run' in src
    assert '/memory/backup/list' in src
    assert '/memory/backup/restore' in src


# ═══════════════════════════════════════════════════════════════════════
# 6. Email shipping (Phase 2a)
# ═══════════════════════════════════════════════════════════════════════

def test_email_shipping_skipped_when_env_missing(tmp_path, monkeypatch):
    """No SMTP env → skipped cleanly with clear reason, never raises."""
    from aria_service.learning import memory_replication as mr

    # Strip every relevant env var
    for var in ("ARIA_SMTP_HOST", "ARIA_EMAIL_HOST",
                "ARIA_SMTP_USER", "EMAIL_USER", "ARIA_EMAIL_USER",
                "ARIA_SMTP_PASS", "EMAIL_PASS", "ARIA_EMAIL_PASS",
                "ARIA_BACKUP_EMAIL_TO", "ARIA_OPERATOR_EMAIL"):
        monkeypatch.delenv(var, raising=False)

    backup = Path(tmp_path) / "2026-04-18.json.gz"
    backup.write_bytes(b"fake-gzipped-content")

    async def run():
        return await mr._ship_backup_email(
            backup, backup.stat().st_size, "2026-04-18T04:00:00+00:00"
        )

    r = asyncio.run(run())
    assert r["sent"] is False
    assert "SMTP env not configured" in r["skipped_reason"]


def test_email_shipping_skipped_when_disabled(tmp_path, monkeypatch):
    """ARIA_BACKUP_EMAIL_ENABLED=0 overrides SMTP env + skips send."""
    from aria_service.learning import memory_replication as mr
    monkeypatch.setenv("ARIA_BACKUP_EMAIL_ENABLED", "0")
    monkeypatch.setenv("ARIA_SMTP_HOST", "fake.example.com")
    monkeypatch.setenv("ARIA_SMTP_USER", "test@example.com")
    monkeypatch.setenv("ARIA_SMTP_PASS", "password")

    backup = Path(tmp_path) / "2026-04-18.json.gz"
    backup.write_bytes(b"fake")

    async def run():
        return await mr._ship_backup_email(
            backup, backup.stat().st_size, "2026-04-18T04:00:00+00:00"
        )

    r = asyncio.run(run())
    assert r["sent"] is False
    assert "ARIA_BACKUP_EMAIL_ENABLED=0" in r["skipped_reason"]


def test_email_shipping_refuses_oversized_attachment(tmp_path, monkeypatch):
    """Attachment >20 MB must skip (most SMTP providers cap at 25 MB)."""
    from aria_service.learning import memory_replication as mr
    monkeypatch.setenv("ARIA_SMTP_HOST", "fake.example.com")
    monkeypatch.setenv("ARIA_SMTP_USER", "test@example.com")
    monkeypatch.setenv("ARIA_SMTP_PASS", "password")
    monkeypatch.setenv("ARIA_BACKUP_EMAIL_ENABLED", "1")

    # Make a >20 MB fake file
    backup = Path(tmp_path) / "2026-04-18.json.gz"
    backup.write_bytes(b"x" * (21 * 1024 * 1024))

    async def run():
        return await mr._ship_backup_email(
            backup, backup.stat().st_size, "2026-04-18T04:00:00+00:00"
        )

    r = asyncio.run(run())
    assert r["sent"] is False
    assert "exceeds cap" in r["skipped_reason"]


def test_stats_reports_email_shipping_status(monkeypatch):
    """get_stats must include email_shipping_status field."""
    from aria_service.learning import memory_replication as mr

    for var in ("ARIA_SMTP_HOST", "ARIA_EMAIL_HOST",
                "ARIA_SMTP_USER", "EMAIL_USER", "ARIA_EMAIL_USER",
                "ARIA_SMTP_PASS", "EMAIL_PASS", "ARIA_EMAIL_PASS"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("ARIA_BACKUP_EMAIL_ENABLED", "1")

    async def run():
        return await mr.get_stats()

    s = asyncio.run(run())
    assert "email_shipping_status" in s
    assert s["email_shipping_status"] in ("LIVE", "GATED", "DISABLED")
    # With SMTP env removed but not disabled, status = GATED
    assert s["email_shipping_status"] == "GATED"


def test_stats_reports_live_when_smtp_configured(monkeypatch):
    from aria_service.learning import memory_replication as mr

    monkeypatch.setenv("ARIA_SMTP_HOST", "smtp.example.com")
    monkeypatch.setenv("ARIA_SMTP_USER", "test@example.com")
    monkeypatch.setenv("ARIA_SMTP_PASS", "password")
    monkeypatch.setenv("ARIA_BACKUP_EMAIL_ENABLED", "1")

    async def run():
        return await mr.get_stats()

    s = asyncio.run(run())
    assert s["email_shipping_status"] == "LIVE"
    assert "test@example.com" in s["email_destination"]


def test_stats_reports_disabled_when_flag_off(monkeypatch):
    from aria_service.learning import memory_replication as mr
    monkeypatch.setenv("ARIA_BACKUP_EMAIL_ENABLED", "0")
    # SMTP configured but still DISABLED because flag is off
    monkeypatch.setenv("ARIA_SMTP_HOST", "smtp.example.com")
    monkeypatch.setenv("ARIA_SMTP_USER", "x@example.com")
    monkeypatch.setenv("ARIA_SMTP_PASS", "y")

    async def run():
        return await mr.get_stats()

    s = asyncio.run(run())
    assert s["email_shipping_status"] == "DISABLED"
