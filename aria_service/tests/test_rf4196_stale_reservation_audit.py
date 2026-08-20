"""R-F4196 — stale R-number claims are visible but never auto-closed."""

from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

from aria_service.intel import r_number_registry


ROOT = Path(__file__).resolve().parents[2]


def test_rf4196_audit_separates_recent_stale_and_malformed_claims(tmp_path: Path) -> None:
    """Age classification is deterministic and malformed dates fail visible."""
    ledger = tmp_path / "data" / "r_number_reservations.json"
    ledger.parent.mkdir()
    ledger.write_text(json.dumps({
        "schema_version": 1,
        "next_available": 4,
        "reservations": [
            {"r_number": "R-F1", "title": "recent", "status": "in_progress",
             "claimed_at": "2026-08-19T00:00:00Z"},
            {"r_number": "R-F2", "title": "old", "status": "in_progress",
             "claimed_at": "2026-07-01T00:00:00Z"},
            {"r_number": "R-F3", "title": "bad clock", "status": "in_progress",
             "claimed_at": "unknown"},
        ],
    }), encoding="utf-8")

    stale = r_number_registry.stale_reservations(
        14,
        path=ledger,
        now=datetime(2026, 8, 20, tzinfo=timezone.utc),
    )

    assert [entry["r_number"] for entry in stale] == ["R-F3", "R-F2"]
    assert stale[0]["age_days"] is None
    assert stale[1]["age_days"] == 50
    assert all("status" not in entry for entry in stale)


def test_rf4196_real_cli_reports_without_mutating_the_ledger() -> None:
    """Drive the operator command against the real repository ledger."""
    ledger = ROOT / "data" / "r_number_reservations.json"
    before = ledger.read_bytes()
    result = subprocess.run(
        [sys.executable, "scripts/admin/reserve_r_number.py", "stale",
         "--days", "14", "--limit", "5"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 1
    assert "STALE" in result.stdout
    assert "age alone never closes work" in result.stdout
    assert "Traceback" not in result.stderr
    assert ledger.read_bytes() == before
