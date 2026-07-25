"""R-F3069 — canonical DD evidence contract and live route capability."""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import math
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from aria_service.intel.dd_evidence_standard import (
    EVIDENCE_SCHEMA_VERSION,
    EvidenceContractError,
    EvidenceRecord,
    describe_standard,
)
from aria_service.routes.aria import _router_auth_dep, router


def _candidate(outcome: str = "success") -> dict:
    raw = b'{"company_number":"12345678","company_status":"active"}'
    return {
        "evidence_id": str(uuid4()),
        "tenant_id": "tenant-test",
        "case_id": str(uuid4()),
        "case_scope_version": 1,
        "subject_entity_id": str(uuid4()),
        "source_attempt_id": str(uuid4()),
        "source_id": "companies_house",
        "source_authority": "primary_official",
        "retrieval_outcome": outcome,
        "retrieved_at": datetime.now(timezone.utc).isoformat(),
        "request_fingerprint": hashlib.sha256(b"company:GB:12345678").hexdigest(),
        "content_hash": hashlib.sha256(raw).hexdigest(),
        "raw_artifact_uri": "evidence://tenant-test/object-1",
        "snapshot_policy": "permitted",
        "licence_policy_id": "companies-house-public-register-v1",
        "access_method": "api",
        "adapter_version": "1.0.0",
        "parser_version": "1.0.0",
        "schema_version": EVIDENCE_SCHEMA_VERSION,
        "query_identifiers": ["12345678", "Example Limited"],
        "structured_payload": {
            "company_number": "12345678",
            "company_status": "active",
        },
    }


def _app() -> FastAPI:
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[_router_auth_dep] = lambda: None
    return app


def test_valid_record_is_immutable_and_json_safe():
    record = EvidenceRecord.from_mapping(_candidate())
    assert record.retrieval_outcome.value == "success"
    assert record.as_dict()["source_authority"] == "primary_official"
    with pytest.raises(Exception):
        record.source_id = "changed"
    with pytest.raises(TypeError):
        record.structured_payload["company_status"] = "dissolved"
    detached = record.as_dict()
    detached["structured_payload"]["company_status"] = "dissolved"
    assert record.structured_payload["company_status"] == "active"


def test_describe_standard_exposes_the_enforced_contract():
    standard = describe_standard()
    assert standard["schema_id"] == "aria.dd.evidence"
    assert standard["schema_version"] == EVIDENCE_SCHEMA_VERSION
    assert standard["immutable"] is True
    assert "retrieval_outcome" in standard["required_fields"]
    assert "timeout" in standard["retrieval_outcomes"]


def test_unknown_fields_are_rejected_instead_of_silently_discarded():
    candidate = _candidate()
    candidate["llm_invented_field"] = "clean"
    with pytest.raises(EvidenceContractError, match="unknown fields are not allowed"):
        EvidenceRecord.from_mapping(candidate)


def test_payload_must_be_deterministic_json_and_dates_must_be_coherent():
    candidate = _candidate()
    candidate["structured_payload"] = {"score": math.nan}
    with pytest.raises(EvidenceContractError, match="NaN or infinity"):
        EvidenceRecord.from_mapping(candidate)

    candidate = _candidate()
    candidate["effective_from"] = "2026-07-26T00:00:00+00:00"
    candidate["effective_to"] = "2026-07-25T00:00:00+00:00"
    with pytest.raises(EvidenceContractError, match="effective_from must not be after"):
        EvidenceRecord.from_mapping(candidate)


@pytest.mark.parametrize(
    ("outcome", "changes", "expected"),
    [
        (
            "timeout",
            {"content_hash": None, "raw_artifact_uri": None,
             "snapshot_policy": "not_applicable", "structured_payload": {}},
            None,
        ),
        (
            "timeout",
            {"content_hash": None, "raw_artifact_uri": None,
             "snapshot_policy": "not_applicable",
             "structured_payload": {"result": "clean"}},
            "structured_payload must be empty",
        ),
        (
            "no_match",
            {"matching_policy_id": None},
            "matching_policy_id is required",
        ),
        (
            "zero_results",
            {"content_hash": None, "raw_artifact_uri": None,
             "snapshot_policy": "not_applicable"},
            "content_hash is required",
        ),
    ],
)
def test_outcomes_cannot_be_silently_upgraded(outcome, changes, expected):
    candidate = _candidate(outcome)
    candidate.update(changes)
    if expected is None:
        assert EvidenceRecord.from_mapping(candidate).retrieval_outcome.value == outcome
        return
    with pytest.raises(EvidenceContractError, match=expected):
        EvidenceRecord.from_mapping(candidate)


def test_no_match_requires_search_manifest_and_policy():
    candidate = _candidate("no_match")
    candidate["matching_policy_id"] = "sanctions-name-dob-v1"
    record = EvidenceRecord.from_mapping(candidate)
    assert record.query_identifiers == ("12345678", "Example Limited")

    missing_queries = deepcopy(candidate)
    missing_queries["query_identifiers"] = []
    with pytest.raises(EvidenceContractError, match="query_identifiers are required"):
        EvidenceRecord.from_mapping(missing_queries)


def test_capability_routes_publish_and_enforce_the_real_contract():
    """Drive the actual FastAPI routes used by an integration client."""
    with TestClient(_app()) as client:
        standard = client.get("/api/aria/dd/evidence-standard")
        assert standard.status_code == 200
        assert standard.json()["schema_version"] == EVIDENCE_SCHEMA_VERSION
        assert "timeout" in standard.json()["retrieval_outcomes"]

        accepted = client.post(
            "/api/aria/dd/evidence-standard/validate", json=_candidate())
        assert accepted.status_code == 200
        assert accepted.json()["valid"] is True

        fabricated_clean = _candidate("timeout")
        fabricated_clean.update({
            "content_hash": None,
            "raw_artifact_uri": None,
            "snapshot_policy": "not_applicable",
            "structured_payload": {"sanctions": "clean"},
        })
        rejected = client.post(
            "/api/aria/dd/evidence-standard/validate", json=fabricated_clean)
        assert rejected.status_code == 422
        assert any(
            "structured_payload must be empty" in error
            for error in rejected.json()["detail"]["errors"]
        )
