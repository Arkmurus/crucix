"""R-F3474 checks that the erasure audit is evidence-bearing and honest."""

from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).parents[2]
REGISTER = ROOT / "data" / "audit_reports" / "rf3474_memory_erasure_controls.json"
ALLOWED_STATUSES = {"pass", "partial", "fail", "unverified", "not_implemented"}


def test_each_erasure_control_has_a_status_finding_and_resolvable_evidence() -> None:
    audit = json.loads(REGISTER.read_text(encoding="utf-8"))
    assert audit["conclusion"] == "fail_pre_phase_2"
    assert len(audit["controls"]) >= 7
    for control in audit["controls"]:
        assert control["status"] in ALLOWED_STATUSES
        assert control["finding"].strip()
        assert control["evidence"]
        for reference in control["evidence"]:
            relative_path, line_text = reference.rsplit(":", 1)
            path = ROOT / relative_path
            assert path.is_file(), reference
            line = int(line_text)
            assert 1 <= line <= len(path.read_text(encoding="utf-8").splitlines())


def test_audit_does_not_claim_unverified_live_residency_or_backup_erasure() -> None:
    audit = json.loads(REGISTER.read_text(encoding="utf-8"))
    controls = {
        item["control_id"]: item["status"] for item in audit["controls"]
    }
    assert controls["RESIDENCY-CONFIG"] == "partial"
    assert controls["BACKUP-ERASURE"] == "unverified"
    assert controls["ERASURE-ALL-RAG-COLLECTIONS"] == "fail"
