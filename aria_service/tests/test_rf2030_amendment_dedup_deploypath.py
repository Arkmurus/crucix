"""R-F2030 + R-F2031 — approved-amendments dedup + deploy-path coherence.

Live evidence 2026-06-27 (aria-intel): aria:adversarial:approved_amendments had
49 entries but only 13 unique (attack_id, anchor_clauses) — the approve flow was
append-only with no dedup, and re-approving a recurring attack minted a fresh
staged_improvement_id that then went stale (the source of the misleading "POST
/api/aria/self/deploy/<dead-id>" the operator was handed for f20b4b4b).

R-F2030: _collapse_approved_amendments dedups by (attack_id, anchor_clauses);
the approve flow is idempotent + collapses on write; the GET audit endpoint
dedups-on-read-and-persists (self-heals the live pile).
R-F2031: _resolve_amendment_deploy_status reports the honest live state
(staged/deployed/gone/none) so no dead deploy command is surfaced.

Run: python -m pytest aria_service/tests/test_rf2030_amendment_dedup_deploypath.py -v
"""
from __future__ import annotations

import asyncio

from aria_service.routes import aria as aria_routes


def test_rf2030_collapse_dedups_by_attack_and_clauses():
    # Mirror the live pattern: C1 ×3, P_GOV_1 ×2, one unique — newest first.
    approved = [
        {"attack_id": "C1", "anchor_clauses": [3, 4, 6], "approved_at": "2026-06-27T15:06", "fail_count": 78, "staged_improvement_id": "newC1"},
        {"attack_id": "C1", "anchor_clauses": [3, 4, 6], "approved_at": "2026-06-22T11:50", "fail_count": 14, "staged_improvement_id": "midC1"},
        {"attack_id": "P_GOV_1", "anchor_clauses": [1, 2, 14, 17], "approved_at": "2026-06-20T08:56", "fail_count": 68, "staged_improvement_id": "g1"},
        {"attack_id": "C1", "anchor_clauses": [3, 4, 6], "approved_at": "2026-05-30T07:41", "fail_count": 3, "staged_improvement_id": "oldC1"},
        {"attack_id": "P_GOV_1", "anchor_clauses": [1, 2, 14, 17], "approved_at": "2026-06-19T09:01", "fail_count": 46, "staged_improvement_id": "g0"},
        {"attack_id": "I1", "anchor_clauses": [1, 14, 17], "approved_at": "2026-05-24T15:52", "fail_count": 6, "staged_improvement_id": "i1"},
    ]
    out = aria_routes._collapse_approved_amendments(approved)
    keys = [(a["attack_id"], tuple(a["anchor_clauses"])) for a in out]
    assert len(out) == 3, f"expected 3 unique, got {len(out)}: {keys}"
    # order preserved (newest-first): C1, P_GOV_1, I1
    assert [a["attack_id"] for a in out] == ["C1", "P_GOV_1", "I1"]
    c1 = next(a for a in out if a["attack_id"] == "C1")
    assert c1["staged_improvement_id"] == "newC1", "must keep the MOST RECENT record"
    assert c1["reapproval_count"] == 3, "must count all collapsed duplicates"
    assert c1["fail_count"] == 78, "must keep the max fail_count"
    assert c1["first_approved_at"] == "2026-05-30T07:41", "must keep earliest timestamp"


def test_rf2030_collapse_is_idempotent():
    approved = [
        {"attack_id": "C1", "anchor_clauses": [3, 4, 6], "approved_at": "2026-06-27", "staged_improvement_id": "x"},
        {"attack_id": "C1", "anchor_clauses": [3, 4, 6], "approved_at": "2026-06-22", "staged_improvement_id": "y"},
    ]
    once = aria_routes._collapse_approved_amendments(approved)
    twice = aria_routes._collapse_approved_amendments(once)
    assert len(once) == 1 and len(twice) == 1
    assert twice[0]["staged_improvement_id"] == "x"


class _FakeRS:
    """In-memory async redis_store stand-in."""
    def __init__(self, data=None):
        self.data = dict(data or {})
        self.writes = []

    async def get_json(self, key):
        return self.data.get(key)

    async def set_json(self, key, val, ex=None):
        self.data[key] = val
        self.writes.append(key)


def _install_fake_rs(monkeypatch, fake):
    # Patch the REAL module's functions — `from ..intel import redis_store`
    # resolves the already-imported package attribute, so a sys.modules swap
    # would be bypassed (green in isolation, flaky in-suite).
    import aria_service.intel.redis_store as rs_mod
    monkeypatch.setattr(rs_mod, "get_json", fake.get_json)
    monkeypatch.setattr(rs_mod, "set_json", fake.set_json)


def test_rf2031_resolve_deploy_status(monkeypatch):
    fake = _FakeRS({
        "crucix:aria:staged_improvements": [{"id": "live1", "status": "staged"}],
        "crucix:aria:improvement_log": [{"id": "done1", "action": "deployed"}],
    })
    _install_fake_rs(monkeypatch, fake)
    run = lambda c: asyncio.run(c)
    assert run(aria_routes._resolve_amendment_deploy_status("live1")) == "staged"
    assert run(aria_routes._resolve_amendment_deploy_status("done1")) == "deployed"
    assert run(aria_routes._resolve_amendment_deploy_status("ghost")) == "gone"
    assert run(aria_routes._resolve_amendment_deploy_status(None)) == "none"


def test_rf2031_approved_endpoint_dedups_persists_and_tags_status(monkeypatch):
    approved = [
        {"attack_id": "C1", "anchor_clauses": [3, 4, 6], "approved_at": "2026-06-27", "staged_improvement_id": "gone1"},
        {"attack_id": "C1", "anchor_clauses": [3, 4, 6], "approved_at": "2026-06-22", "staged_improvement_id": "gone0"},
        {"attack_id": "I1", "anchor_clauses": [1, 14, 17], "approved_at": "2026-05-24", "staged_improvement_id": "live1"},
    ]
    fake = _FakeRS({
        "aria:adversarial:approved_amendments": approved,
        "crucix:aria:staged_improvements": [{"id": "live1", "status": "staged"}],
        "crucix:aria:improvement_log": [],
    })
    _install_fake_rs(monkeypatch, fake)
    res = asyncio.run(aria_routes.adversarial_amendments_approved_ep(limit=100))
    assert res["raw_count_before_dedup"] == 3
    assert res["count"] == 2, "C1 duplicate collapsed → 2 unique"
    assert "aria:adversarial:approved_amendments" in fake.writes, "must persist the collapse"
    statuses = {a["attack_id"]: a["deploy_status"] for a in res["approved"]}
    assert statuses["I1"] == "staged"
    assert statuses["C1"] == "gone", "stale staged id must report 'gone', not a live deploy command"
