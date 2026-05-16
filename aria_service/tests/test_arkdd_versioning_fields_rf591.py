"""R-F591 — ARKDDReport dataclass versioning-field surface.

R-F573 (DD case file) added canonical_entity_id, version_number,
previous_run_id, and version_diff to the PERSISTED body (the Redis
copy that /api/aria/dd/report/{run_id} returns), but not to the
ARKDDReport dataclass itself. The synchronous /api/aria/dd/orchestrate
response is built from `report.as_dict()` which serialises the
dataclass — so the chat / WhatsApp / web client received None for
these fields on the immediate response, only seeing the real values
on a follow-up read.

R-F591 promotes the 4 fields to first-class dataclass attributes so
as_dict() exports them on every code path (orchestrate response, Redis
body, case-file endpoint, markdown render). _persist_report sets them
on both the dataclass AND the body so the in-flight orchestrate caller
and the Redis re-reader see consistent state.

These tests are structural — they verify the dataclass has the fields
with sensible defaults and that as_dict() exposes them.
"""
from __future__ import annotations

import pytest


def test_rf591_arkdd_report_has_canonical_entity_id():
    """The dataclass must declare canonical_entity_id as a settable
    attribute with None default (un-set state)."""
    from aria_service.intel.dd_schema import ARKDDReport
    r = ARKDDReport()
    assert hasattr(r, "canonical_entity_id"), (
        "R-F591 REGRESSION: ARKDDReport missing canonical_entity_id field"
    )
    assert r.canonical_entity_id is None
    r.canonical_entity_id = "company:BR:0768900200018"
    assert r.canonical_entity_id == "company:BR:0768900200018"


def test_rf591_arkdd_report_has_version_number_default_1():
    """version_number must default to 1 — every new report is v1 of
    its case file unless _persist_report bumps it via resolve_version_chain."""
    from aria_service.intel.dd_schema import ARKDDReport
    r = ARKDDReport()
    assert hasattr(r, "version_number")
    assert r.version_number == 1


def test_rf591_arkdd_report_has_previous_run_id():
    from aria_service.intel.dd_schema import ARKDDReport
    r = ARKDDReport()
    assert hasattr(r, "previous_run_id")
    assert r.previous_run_id is None
    r.previous_run_id = "dd_aaa111"
    assert r.previous_run_id == "dd_aaa111"


def test_rf591_arkdd_report_has_version_diff():
    from aria_service.intel.dd_schema import ARKDDReport
    r = ARKDDReport()
    assert hasattr(r, "version_diff")
    assert r.version_diff is None
    r.version_diff = {
        "summary": "Risk: GREEN → AMBER. 1 new finding.",
        "previous_run_id": "dd_aaa111",
        "risk_changed": True,
        "new_findings": [{"title": "PEP exposure", "severity": "amber"}],
        "resolved_findings": [],
        "unchanged_count": 5,
    }
    assert r.version_diff["risk_changed"] is True


def test_rf591_as_dict_exports_versioning_fields():
    """The core integration test: as_dict() (which the /dd/orchestrate
    response uses) MUST include all 4 versioning fields."""
    from aria_service.intel.dd_schema import ARKDDReport
    r = ARKDDReport()
    r.canonical_entity_id = "company:TR:aselsan-h0"
    r.version_number = 3
    r.previous_run_id = "dd_bbb222"
    r.version_diff = {"summary": "no changes"}
    d = r.as_dict()
    assert d["canonical_entity_id"] == "company:TR:aselsan-h0"
    assert d["version_number"] == 3
    assert d["previous_run_id"] == "dd_bbb222"
    assert d["version_diff"] == {"summary": "no changes"}


def test_rf591_as_dict_defaults_when_unset():
    """When _persist_report hasn't fired yet (e.g. orchestrator failed
    early), as_dict() must still export the fields — with their default
    None / 1 values — so the consumer's JSON parser never gets KeyError."""
    from aria_service.intel.dd_schema import ARKDDReport
    r = ARKDDReport()
    d = r.as_dict()
    assert "canonical_entity_id" in d and d["canonical_entity_id"] is None
    assert "version_number" in d and d["version_number"] == 1
    assert "previous_run_id" in d and d["previous_run_id"] is None
    assert "version_diff" in d and d["version_diff"] is None


def test_rf591_default_dataclass_does_not_regress_existing_fields():
    """R-F591 ADDS 4 attributes; must not rename or break the existing
    surface that R-F573, R-F575, R-F583, etc. depend on."""
    from aria_service.intel.dd_schema import ARKDDReport
    r = ARKDDReport()
    d = r.as_dict()
    for required in (
        "run_id", "schema_version", "generated_at", "generator",
        "target", "orchestrator_mode", "layers_run", "layers_skipped",
        "identity", "network", "verification", "compliance", "digital",
        "commercial_coherence", "synthesis", "bottom_line",
        "recommendation", "risk_classification", "confidence_tag",
        "next_actions", "data_gaps_summary", "total_cost_usd",
        "total_duration_ms", "layer_costs_usd", "verification_gate",
        "confidence_gate_triggered", "confidence_gate_reasons",
        "ecosystem_status", "discipline_coverage", "adverse_media",
    ):
        assert required in d, (
            f"R-F591 REGRESSION: existing field {required!r} lost from "
            f"ARKDDReport.as_dict()"
        )
