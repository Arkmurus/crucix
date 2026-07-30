"""R-F3474 capability tests for the first DD gold-set case files."""

from __future__ import annotations

import json
from pathlib import Path


GOLD_DIR = Path(__file__).parents[2] / "data" / "eval" / "dd_gold_v1"
REQUIRED_CASES = {
    "dd-gold-babcock-v1",
    "dd-gold-tac-mikronglobal-v1",
    "dd-gold-synthetic-clean-v1",
}


def _cases() -> list[dict]:
    return [
        json.loads(path.read_text(encoding="utf-8"))
        for path in sorted(GOLD_DIR.glob("*.json"))
    ]


def test_first_three_named_cases_exist_and_pin_contract_versions() -> None:
    cases = _cases()
    assert {case["case_id"] for case in cases} == REQUIRED_CASES
    for case in cases:
        assert case["schema_version"] == "1.0.0"
        assert case["verdict_fn_version"] == "1.0.0"


def test_release_eligible_cases_have_frozen_observations_and_sourced_findings() -> None:
    for case in _cases():
        if not case["release_gate_eligible"]:
            continue
        observations = {
            item["observation_id"]: item for item in case["observations"]
        }
        assert observations
        assert all(item["frozen"] is True for item in observations.values())
        assert case["expected_findings"]
        for finding in case["expected_findings"]:
            assert finding["rationale"].strip()
            assert finding["evidence_refs"]
            assert set(finding["evidence_refs"]) <= observations.keys()


def test_missing_real_case_evidence_cannot_masquerade_as_a_release_gate() -> None:
    case = next(
        item for item in _cases()
        if item["case_id"] == "dd-gold-tac-mikronglobal-v1"
    )
    assert case["fixture_mode"] == "blocked_missing_frozen_evidence"
    assert case["release_gate_eligible"] is False
    assert case["observations"] == []
    assert case["expected_findings"] == []
    assert len(case["blocker"]["required_to_unblock"]) >= 3


def test_clean_case_is_explicitly_synthetic_not_a_claim_about_a_real_company() -> None:
    case = next(
        item for item in _cases()
        if item["case_id"] == "dd-gold-synthetic-clean-v1"
    )
    assert case["fixture_mode"] == "synthetic_negative_control"
    assert case["subject"]["entity_type"] == "synthetic_company"
    assert all(
        observation["source_ref"].startswith("synthetic://")
        for observation in case["observations"]
    )
