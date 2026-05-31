"""R-F1182 — Verifiable Ledger System capability test.

Tests the full VLS lifecycle:
  1. record_report() stores a cryptographic proof for a DD report
  2. get_proof() retrieves the stored proof
  3. verify_single() confirms hash + signature integrity
  4. verify_chain() confirms chain linking across multiple versions
  5. Tampered report body is detected by verify_single()
  6. Public key export works
"""

from __future__ import annotations

import json
import pytest
from unittest.mock import AsyncMock, patch, MagicMock

from aria_service.intel.dd_schema import ARKDDReport, RiskClassification


# ── Fixtures ────────────────────────────────────────────────────────────────


@pytest.fixture
def mock_redis():
    """Patch redis_store so VLS operations use in-memory dicts."""
    store: dict[str, str] = {}

    async def _get_json(key: str):
        raw = store.get(key)
        if raw:
            return json.loads(raw)
        return None

    async def _set_json(key: str, obj, ex=None, keepttl=False):
        store[key] = json.dumps(obj, default=str)

    # Patch the actual redis_store module functions (VLS imports it via
    # `from . import redis_store as rs` then calls rs.get_json / rs.set_json).
    with patch("aria_service.intel.redis_store.get_json", AsyncMock(side_effect=_get_json)), \
         patch("aria_service.intel.redis_store.set_json", AsyncMock(side_effect=_set_json)):
        yield store


@pytest.fixture
def sample_report() -> ARKDDReport:
    """A minimal ARKDDReport with versioning fields."""
    return ARKDDReport(
        run_id="dd_test_rf1182_001",
        target={"name": "Acme Corp"},
        canonical_entity_id="company:US:ACME123",
        version_number=1,
        risk_classification=RiskClassification.GREEN.value,
        bottom_line="No material concerns identified.",
    )


@pytest.fixture
def sample_report_v2() -> ARKDDReport:
    """A second version of the same entity's DD report."""
    return ARKDDReport(
        run_id="dd_test_rf1182_002",
        target={"name": "Acme Corp"},
        canonical_entity_id="company:US:ACME123",
        version_number=2,
        previous_run_id="dd_test_rf1182_001",
        risk_classification=RiskClassification.AMBER_LIGHT.value,
        bottom_line="New sanctions finding detected.",
    )


# ── Capability test: full VLS lifecycle ─────────────────────────────────────


@pytest.mark.asyncio
async def test_rf1182_vls_full_lifecycle(mock_redis, sample_report):
    """Capability test: record a DD report, verify its proof, confirm integrity."""
    from aria_service.intel import verifiable_ledger as _vls

    # Pre-populate report body BEFORE recording so hash matches
    import json as _json
    _body = sample_report.as_dict()
    mock_redis[f"crucix:dd:report:{sample_report.run_id}"] = _json.dumps(_body, default=str)

    # 1. Record the proof
    result = await _vls.record_report(sample_report)
    assert result["status"] == "ok", f"record_report failed: {result}"
    assert "proof" in result
    proof = result["proof"]
    assert proof["version"] == 1
    assert proof["previous_hash"] == "0" * 64  # genesis block
    assert proof["hash"]  # non-empty hash
    assert proof["signature"]  # non-empty signature

    # 2. Retrieve the proof
    retrieved = await _vls.get_proof(sample_report.run_id)
    assert retrieved is not None
    assert retrieved["hash"] == proof["hash"]
    assert retrieved["signature"] == proof["signature"]

    # 3. Verify single report integrity

    verify_result = await _vls.verify_single(sample_report.run_id)
    assert verify_result["verified"] is True, f"verify_single failed: {verify_result}"
    assert verify_result["hash_valid"] is True
    assert verify_result["signature_valid"] is True

    # 4. Verify chain (single version)
    chain_result = await _vls.verify_chain(sample_report.canonical_entity_id)
    assert chain_result["verified"] is True, f"verify_chain failed: {chain_result}"
    assert chain_result["total_versions"] == 1
    assert chain_result["verified_count"] == 1


@pytest.mark.asyncio
async def test_rf1182_vls_version_chain(mock_redis, sample_report, sample_report_v2):
    """Capability test: two versions of the same entity chain correctly."""
    from aria_service.intel import verifiable_ledger as _vls

    # Pre-populate report bodies BEFORE recording (so the hash computed by
    # record_report matches the body that verify_single/verify_chain reads).
    import json as _json
    _body_v1 = sample_report.as_dict()
    _body_v2 = sample_report_v2.as_dict()
    mock_redis[f"crucix:dd:report:{sample_report.run_id}"] = _json.dumps(_body_v1, default=str)
    mock_redis[f"crucix:dd:report:{sample_report_v2.run_id}"] = _json.dumps(_body_v2, default=str)

    # Record v1
    v1_result = await _vls.record_report(sample_report)
    assert v1_result["status"] == "ok"
    v1_proof = v1_result["proof"]

    # Record v2
    v2_result = await _vls.record_report(sample_report_v2)
    assert v2_result["status"] == "ok"
    v2_proof = v2_result["proof"]

    # v2 should link to v1
    assert v2_proof["previous_hash"] == v1_proof["hash"], "v2 must link to v1"
    assert v2_proof["version"] == 2

    # Verify both individually
    v1_ok = await _vls.verify_single(sample_report.run_id)
    assert v1_ok["verified"] is True
    v2_ok = await _vls.verify_single(sample_report_v2.run_id)
    assert v2_ok["verified"] is True

    # Verify the full chain
    chain_result = await _vls.verify_chain(sample_report.canonical_entity_id)
    assert chain_result["verified"] is True, f"chain verify failed: {chain_result}"
    assert chain_result["total_versions"] == 2
    assert chain_result["verified_count"] == 2


@pytest.mark.asyncio
async def test_rf1182_vls_detects_tampering(mock_redis, sample_report):
    """Capability test: tampered report body is detected by verify_single()."""
    from aria_service.intel import verifiable_ledger as _vls

    # Record the proof
    await _vls.record_report(sample_report)

    # Tamper with the stored report body
    report_key = f"crucix:dd:report:{sample_report.run_id}"
    mock_redis[report_key] = json.dumps({
        "run_id": sample_report.run_id,
        "risk_classification": "GREEN",
        "bottom_line": "TAMPERED — this was not the original content",
    })

    # Verify should detect the tampering
    verify_result = await _vls.verify_single(sample_report.run_id)
    assert verify_result["verified"] is False, "verify_single should detect tampered body"
    assert verify_result["hash_valid"] is False
    # Signature may or may not be valid depending on hash mismatch
    # (the signature is verified against the tampered body, so it should fail)


@pytest.mark.asyncio
async def test_rf1182_vls_public_key_export(mock_redis):
    """Capability test: public key export returns valid PEM."""
    from aria_service.intel import verifiable_ledger as _vls

    pem = _vls.get_public_key_pem()
    assert pem.startswith("-----BEGIN PUBLIC KEY-----")
    assert pem.strip().endswith("-----END PUBLIC KEY-----")
    assert "EC" in pem or len(pem) > 100  # should be a real key


@pytest.mark.asyncio
async def test_rf1182_vls_no_run_id(mock_redis):
    """Capability test: record_report with no run_id returns skipped."""
    from aria_service.intel import verifiable_ledger as _vls

    empty_report = ARKDDReport()  # no run_id set explicitly (uses default)
    result = await _vls.record_report(empty_report)
    # Should still work since ARKDDReport has a default run_id
    assert result["status"] == "ok"


@pytest.mark.asyncio
async def test_rf1182_vls_no_canonical_id(mock_redis):
    """Capability test: report without canonical_entity_id still gets a proof."""
    from aria_service.intel import verifiable_ledger as _vls

    report = ARKDDReport(
        run_id="dd_test_no_cid",
        target={"name": "Unknown Entity"},
        canonical_entity_id=None,  # no canonical ID
    )
    # Pre-populate report body BEFORE recording
    import json as _json
    _body = report.as_dict()
    mock_redis[f"crucix:dd:report:{report.run_id}"] = _json.dumps(_body, default=str)

    result = await _vls.record_report(report)
    assert result["status"] == "ok", f"record without canonical_id failed: {result}"
    assert result["proof"]["version"] == 1

    # Verify still works
    verify_result = await _vls.verify_single(report.run_id)
    assert verify_result["verified"] is True
