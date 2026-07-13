"""R-F2588 — VLS routes must be ownership-gated (confirmed cross-tenant IDOR).

Codex + a security audit CONFIRMED: GET /dd/vls/chain/{canonical_entity_id},
/dd/vls/verify/{run_id}, /dd/vls/proof/{run_id} read the verifiable ledger with
NO ownership gate, so any authenticated caller could probe report existence,
chain metadata, and the risk_classification (RED/AMBER/GREEN) of arbitrary
entities they don't own — enumerable via the deterministic canonical_entity_id.

These call the actual route functions directly (no TestClient — it hangs on
Win/3.14 importing sentence_transformers at shutdown) and assert:
  - a scoped caller who does NOT own the entity/run gets 404 and NEVER reaches
    the ledger (no existence/verdict leak),
  - the owner gets the data,
  - the internal/admin caller (user_id="") keeps see-all (server-to-server).

§23-discriminating: pre-R-F2588 the routes had no user_id param, so these calls
error/return-through on old code and only pass after the gate is added.
"""
from __future__ import annotations

import asyncio

import pytest
from fastapi import HTTPException

from aria_service.routes import aria as A
from aria_service.intel import verifiable_ledger as VL
from aria_service.intel import dd_orchestrator as DO


# ── chain route (keyed by canonical_entity_id — the enumerable, most severe leg)

def test_vls_chain_denies_cross_tenant(monkeypatch):
    async def _owned(uid, dom=""):
        return {"company:GB:11111111"}          # caller owns only this
    monkeypatch.setattr(A, "_dd_owned_entity_ids", _owned)
    reached = {"n": 0}

    async def _verify_chain(eid):
        reached["n"] += 1
        return {"verified": True}
    monkeypatch.setattr(VL, "verify_chain", _verify_chain)

    with pytest.raises(HTTPException) as ei:
        asyncio.run(A.dd_vls_chain_ep("company:GB:99999999", user_id="alice"))
    assert ei.value.status_code == 404
    assert reached["n"] == 0, "cross-tenant request must never reach the ledger"


def test_vls_chain_allows_owner(monkeypatch):
    async def _owned(uid, dom=""):
        return {"company:GB:99999999"}
    monkeypatch.setattr(A, "_dd_owned_entity_ids", _owned)

    async def _verify_chain(eid):
        return {"verified": True, "eid": eid}
    monkeypatch.setattr(VL, "verify_chain", _verify_chain)

    out = asyncio.run(A.dd_vls_chain_ep("company:GB:99999999", user_id="alice"))
    assert out["verified"] is True and out["eid"] == "company:GB:99999999"


def test_vls_chain_internal_seeall(monkeypatch):
    async def _owned(uid, dom=""):
        return None                              # internal/admin → unrestricted
    monkeypatch.setattr(A, "_dd_owned_entity_ids", _owned)

    async def _verify_chain(eid):
        return {"verified": True}
    monkeypatch.setattr(VL, "verify_chain", _verify_chain)

    out = asyncio.run(A.dd_vls_chain_ep("company:GB:99999999", user_id=""))
    assert out["verified"] is True


# ── run_id routes (proof / verify)

def test_vls_proof_denies_non_owner(monkeypatch):
    async def _get_report(rid):
        return {"user_id": "bob", "run_id": rid}   # owned by bob
    monkeypatch.setattr(DO, "get_report", _get_report)

    async def _acl_ctx(orch, rid, rep):
        return dict(rep)                            # owner stays bob
    monkeypatch.setattr(A, "_dd_report_acl_context", _acl_ctx)

    reached = {"n": 0}

    async def _get_proof(rid):
        reached["n"] += 1
        return {"hash": "x"}
    monkeypatch.setattr(VL, "get_proof", _get_proof)

    with pytest.raises(HTTPException) as ei:
        asyncio.run(A.dd_vls_proof_ep("dd_123", user_id="alice"))   # alice != bob
    assert ei.value.status_code == 404
    assert reached["n"] == 0


def test_vls_verify_allows_owner(monkeypatch):
    async def _get_report(rid):
        return {"user_id": "alice", "run_id": rid}
    monkeypatch.setattr(DO, "get_report", _get_report)

    async def _acl_ctx(orch, rid, rep):
        return dict(rep)
    monkeypatch.setattr(A, "_dd_report_acl_context", _acl_ctx)

    async def _verify_single(rid):
        return {"risk_classification": "AMBER", "run_id": rid}
    monkeypatch.setattr(VL, "verify_single", _verify_single)

    out = asyncio.run(A.dd_vls_verify_single_ep("dd_1", user_id="alice"))
    assert out["risk_classification"] == "AMBER"


def test_vls_unknown_run_scoped_denied(monkeypatch):
    async def _get_report(rid):
        return None                                 # unknown/aged-out run_id
    monkeypatch.setattr(DO, "get_report", _get_report)
    reached = {"n": 0}

    async def _get_proof(rid):
        reached["n"] += 1
        return {"hash": "y"}
    monkeypatch.setattr(VL, "get_proof", _get_proof)

    with pytest.raises(HTTPException) as ei:
        asyncio.run(A.dd_vls_proof_ep("dd_unknown", user_id="alice"))
    assert ei.value.status_code == 404
    assert reached["n"] == 0, "scoped caller must not confirm existence of an unknown run"


def test_vls_unknown_run_internal_allowed(monkeypatch):
    async def _get_report(rid):
        return None
    monkeypatch.setattr(DO, "get_report", _get_report)

    async def _get_proof(rid):
        return {"hash": "y"}
    monkeypatch.setattr(VL, "get_proof", _get_proof)

    out = asyncio.run(A.dd_vls_proof_ep("dd_unknown", user_id=""))   # internal
    assert out["proof"]["hash"] == "y"


if __name__ == "__main__":
    import sys
    sys.exit(pytest.main([__file__, "-v"]))
