"""R-F3083 — append-only DD evidence persistence capability."""
from __future__ import annotations

import hashlib
import base64
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from aria_service.intel import dd_evidence_store
from aria_service.intel.dd_evidence_store import (
    DDEvidenceStore,
    EvidencePersistenceError,
    get_evidence_store,
)
from aria_service.tests.test_rf3069_dd_evidence_standard import _candidate
from aria_service.routes.aria import _router_auth_dep, router


@pytest.fixture
def store(tmp_path: Path) -> DDEvidenceStore:
    return DDEvidenceStore(tmp_path / "evidence.db", tmp_path / "artifacts")


def test_get_evidence_store_uses_configured_durable_paths(tmp_path, monkeypatch):
    db_path = tmp_path / "configured.db"
    artifact_dir = tmp_path / "configured-artifacts"
    monkeypatch.setenv("ARIA_DD_EVIDENCE_DB", str(db_path))
    monkeypatch.setenv("ARIA_DD_EVIDENCE_ARTIFACT_DIR", str(artifact_dir))
    monkeypatch.setattr(dd_evidence_store, "_STORE", None)

    configured = get_evidence_store()

    assert configured.db_path == db_path
    assert configured.artifact_dir == artifact_dir


def test_get_evidence_store_first_initialisation_is_thread_safe(
    tmp_path, monkeypatch,
):
    monkeypatch.setenv("ARIA_DD_EVIDENCE_DB", str(tmp_path / "singleton.db"))
    monkeypatch.setenv(
        "ARIA_DD_EVIDENCE_ARTIFACT_DIR", str(tmp_path / "singleton-artifacts"))
    monkeypatch.setattr(dd_evidence_store, "_STORE", None)
    with ThreadPoolExecutor(max_workers=16) as executor:
        stores = list(executor.map(lambda _: get_evidence_store(), range(64)))

    assert len({id(item) for item in stores}) == 1


def test_answered_evidence_is_hash_verified_persisted_and_restart_safe(store):
    raw = b'{"company_number":"12345678","company_status":"active"}'
    candidate = _candidate()
    result = store.append(candidate, raw)

    assert result.as_dict() == {
        "evidence_id": candidate["evidence_id"],
        "status": "stored",
        "content_hash_verified": True,
        "artifact_retained": True,
    }
    restarted = DDEvidenceStore(store.db_path, store.artifact_dir)
    stored = restarted.get(candidate["tenant_id"], candidate["evidence_id"])
    assert stored is not None
    assert stored["record"]["structured_payload"]["company_status"] == "active"
    assert stored["integrity"] == {
        "metadata_valid": True,
        "content_hash_verified": True,
        "artifact_retained": True,
        "artifact_valid": True,
    }


def test_hash_mismatch_is_rejected_before_database_insert(store):
    candidate = _candidate()
    with pytest.raises(EvidencePersistenceError, match="does not match"):
        store.append(candidate, b"tampered")
    assert store.get(candidate["tenant_id"], candidate["evidence_id"]) is None


def test_exact_replay_is_idempotent_but_mutation_is_rejected(store):
    raw = b'{"company_number":"12345678","company_status":"active"}'
    candidate = _candidate()
    assert store.append(candidate, raw).status == "stored"
    assert store.append(candidate, raw).status == "already_present"

    changed = dict(candidate)
    changed["source_attempt_id"] = str(uuid4())
    changed["structured_payload"] = {"company_status": "dissolved"}
    with pytest.raises(EvidencePersistenceError, match="different content"):
        store.append(changed, raw)


def test_concurrent_exact_replay_has_one_insert_and_no_lock_failures(store):
    """Drive real SQLite and artifact I/O under the production contention shape."""
    raw = b'{"company_number":"12345678","company_status":"active"}'
    candidate = _candidate()
    with ThreadPoolExecutor(max_workers=16) as executor:
        statuses = list(executor.map(
            lambda _: store.append(candidate, raw).status,
            range(64),
        ))

    assert statuses.count("stored") == 1
    assert statuses.count("already_present") == 63
    stored = store.get(candidate["tenant_id"], candidate["evidence_id"])
    assert stored is not None
    assert stored["integrity"]["metadata_valid"] is True
    assert stored["integrity"]["artifact_valid"] is True


def test_tenant_boundary_and_retained_artifact_tamper_are_detected(store):
    raw = b'{"company_number":"12345678","company_status":"active"}'
    candidate = _candidate()
    store.append(candidate, raw)
    assert store.get("different-tenant", candidate["evidence_id"]) is None

    artifact = store.artifact_dir / hashlib.sha256(raw).hexdigest()
    artifact.write_bytes(b"tampered")
    stored = store.get(candidate["tenant_id"], candidate["evidence_id"])
    assert stored is not None
    assert stored["integrity"]["artifact_valid"] is False


def test_prohibited_snapshot_is_verified_but_not_retained(store):
    raw = b'{"company_number":"12345678","company_status":"active"}'
    candidate = _candidate()
    candidate["snapshot_policy"] = "prohibited"
    candidate["raw_artifact_uri"] = None
    result = store.append(candidate, raw)
    assert result.content_hash_verified is True
    assert result.artifact_retained is False
    stored = store.get(candidate["tenant_id"], candidate["evidence_id"])
    assert stored is not None
    assert stored["integrity"]["artifact_valid"] is None


def test_timeout_is_persisted_as_failure_without_artifact(store):
    candidate = _candidate("timeout")
    candidate.update({
        "content_hash": None,
        "raw_artifact_uri": None,
        "snapshot_policy": "not_applicable",
        "structured_payload": {},
    })
    result = store.append(candidate, None)
    assert result.content_hash_verified is False
    assert result.artifact_retained is False


def test_capability_route_persists_real_evidence_and_fails_closed(
    store, monkeypatch,
):
    """Drive the actual API and storage function through both integrity branches."""
    monkeypatch.setattr(dd_evidence_store, "_STORE", store)
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[_router_auth_dep] = lambda: None
    raw = b'{"company_number":"12345678","company_status":"active"}'
    candidate = _candidate()

    with TestClient(app) as client:
        accepted = client.post("/api/aria/dd/evidence", json={
            "record": candidate,
            "artifact_base64": base64.b64encode(raw).decode("ascii"),
        })
        assert accepted.status_code == 200
        assert accepted.json()["content_hash_verified"] is True

        retrieved = client.get(
            f"/api/aria/dd/evidence/{candidate['evidence_id']}",
            params={"tenant_id": candidate["tenant_id"]},
        )
        assert retrieved.status_code == 200
        assert retrieved.json()["integrity"]["artifact_valid"] is True

        cross_tenant = client.get(
            f"/api/aria/dd/evidence/{candidate['evidence_id']}",
            params={"tenant_id": "wrong-tenant"},
        )
        assert cross_tenant.status_code == 404

        rejected_candidate = _candidate()
        rejected = client.post("/api/aria/dd/evidence", json={
            "record": rejected_candidate,
            "artifact_base64": base64.b64encode(b"tampered").decode("ascii"),
        })
        assert rejected.status_code == 422
        assert rejected.json()["detail"]["code"] == "evidence_not_persisted"
        assert store.get(
            rejected_candidate["tenant_id"],
            rejected_candidate["evidence_id"],
        ) is None


def test_capability_route_refuses_tampered_retained_artifact(store, monkeypatch):
    monkeypatch.setattr(dd_evidence_store, "_STORE", store)
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[_router_auth_dep] = lambda: None
    raw = b'{"company_number":"12345678","company_status":"active"}'
    candidate = _candidate()
    store.append(candidate, raw)
    (store.artifact_dir / hashlib.sha256(raw).hexdigest()).write_bytes(b"tampered")

    with TestClient(app) as client:
        response = client.get(
            f"/api/aria/dd/evidence/{candidate['evidence_id']}",
            params={"tenant_id": candidate["tenant_id"]},
        )
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "evidence_integrity_failure"


def test_capability_route_rejects_malformed_and_oversized_input(
    store, monkeypatch,
):
    monkeypatch.setattr(dd_evidence_store, "_STORE", store)
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[_router_auth_dep] = lambda: None

    with TestClient(app) as client:
        malformed = client.post(
            "/api/aria/dd/evidence",
            content=b"{not-json",
            headers={"content-type": "application/json"},
        )
        oversized = client.post(
            "/api/aria/dd/evidence",
            json={
                "record": _candidate(),
                "artifact_base64": base64.b64encode(
                    b"x" * (16 * 1024 * 1024 + 1)
                ).decode("ascii"),
            },
        )
    assert malformed.status_code == 422
    assert malformed.json()["detail"]["code"] == "invalid_evidence_request"
    assert oversized.status_code == 413
    assert oversized.json()["detail"]["code"] in {
        "evidence_request_too_large",
        "evidence_artifact_too_large",
    }


def test_evidence_routes_require_operator_tier_when_scoping_is_configured(
    monkeypatch,
):
    monkeypatch.setenv("ARIA_API_TOKEN", "api-token")
    monkeypatch.setenv("ARIA_SERVICE_TOKEN", "service-token")
    monkeypatch.setenv("ARIA_OPERATOR_TOKEN", "operator-token")
    monkeypatch.delenv("ARIA_TOKEN_SCOPING", raising=False)
    app = FastAPI()
    app.include_router(router)
    evidence_id = str(uuid4())

    with TestClient(app) as client:
        service_response = client.get(
            f"/api/aria/dd/evidence/{evidence_id}",
            params={"tenant_id": "tenant-test"},
            headers={"Authorization": "Bearer service-token"},
        )
        operator_response = client.get(
            f"/api/aria/dd/evidence/{evidence_id}",
            params={"tenant_id": "tenant-test"},
            headers={"Authorization": "Bearer operator-token"},
        )
    assert service_response.status_code == 403
    assert operator_response.status_code == 404
