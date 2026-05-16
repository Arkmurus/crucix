"""R-F574 (2026-05-16) — discard_improvement capability tests.

Closes the gap exposed by 039bf8be (P_BANKING_1 amendment that got
operator-approved + auto-staged but was actually a duplicate of
Clause 27/R-F534). Pre-R-F574, staged_improvements that shouldn't
deploy could only TTL-expire or sit indefinitely. R-F574 adds an
explicit operator-reject path with audit trail.

Mirrors the adversarial_amendments_reject pattern at
routes/aria.py:15355.
"""
from __future__ import annotations

import asyncio
import json
import time

import pytest


def _setup_fake_redis(monkeypatch, initial: dict | None = None):
    """Stub redis_store so the discard path operates in-memory."""
    from aria_service.intel import redis_store as rs

    store: dict[str, object] = dict(initial or {})

    async def fake_get_json(key):
        return store.get(key)

    async def fake_set_json(key, value, ex=None):
        store[key] = value

    monkeypatch.setattr(rs, "get_json", fake_get_json)
    monkeypatch.setattr(rs, "set_json", fake_set_json)
    return store


def _staged_entry(id_: str, status: str = "staged") -> dict:
    return {
        "id": id_,
        "status": status,
        "file": "aria_service/intel/example.py",
        "change_type": "bug_fix",
        "description": "fake staged entry",
        "old_content": "old",
        "new_content": "new",
        "created_at": time.time(),
    }


# ── Core behaviour ──────────────────────────────────────────────────────


def test_discard_moves_entry_from_staged_to_discarded(monkeypatch):
    """Capability: a staged entry must be removed from STAGED_KEY and
    appear in DISCARDED_KEY with rejection metadata."""
    from aria_service.intel import self_improve as si
    store = _setup_fake_redis(monkeypatch, {
        si.STAGED_KEY: [_staged_entry("imp_abc"), _staged_entry("imp_xyz")],
    })

    result = asyncio.run(si.discard_improvement("imp_abc", "duplicate of Clause 27"))
    assert result["ok"] is True
    assert result["improvement_id"] == "imp_abc"
    assert result["queue_depth_now"] == 1

    staged_after = store[si.STAGED_KEY]
    assert all(s["id"] != "imp_abc" for s in staged_after), "discarded entry should not remain in STAGED_KEY"

    discarded = store[si.DISCARDED_KEY]
    assert len(discarded) == 1
    assert discarded[0]["id"] == "imp_abc"
    assert discarded[0]["status"] == "discarded"
    assert discarded[0]["discard_reason"] == "duplicate of Clause 27"
    assert "discarded_at" in discarded[0]


def test_discard_requires_reason(monkeypatch):
    from aria_service.intel import self_improve as si
    _setup_fake_redis(monkeypatch, {si.STAGED_KEY: [_staged_entry("imp_a")]})
    r = asyncio.run(si.discard_improvement("imp_a", ""))
    assert r["ok"] is False
    assert "reason" in r["error"].lower()


def test_discard_requires_improvement_id(monkeypatch):
    from aria_service.intel import self_improve as si
    _setup_fake_redis(monkeypatch, {si.STAGED_KEY: [_staged_entry("imp_a")]})
    r = asyncio.run(si.discard_improvement("", "reason"))
    assert r["ok"] is False


def test_discard_returns_404_shape_for_unknown_id(monkeypatch):
    from aria_service.intel import self_improve as si
    _setup_fake_redis(monkeypatch, {si.STAGED_KEY: [_staged_entry("imp_a")]})
    r = asyncio.run(si.discard_improvement("imp_does_not_exist", "test"))
    assert r["ok"] is False
    assert "no staged improvement" in r["error"].lower()


def test_discard_ignores_already_deployed_entry(monkeypatch):
    """Capability: discard targets `status: staged` only. A deployed
    entry cannot be retroactively discarded (must rollback instead)."""
    from aria_service.intel import self_improve as si
    _setup_fake_redis(monkeypatch, {
        si.STAGED_KEY: [_staged_entry("imp_dep", status="deployed")],
    })
    r = asyncio.run(si.discard_improvement("imp_dep", "test"))
    assert r["ok"] is False


def test_discard_truncates_long_reason(monkeypatch):
    """Reason must be truncated at 500 chars so a verbose operator
    note doesn't bloat the audit row indefinitely."""
    from aria_service.intel import self_improve as si
    _setup_fake_redis(monkeypatch, {si.STAGED_KEY: [_staged_entry("imp_a")]})
    long_reason = "x" * 600
    r = asyncio.run(si.discard_improvement("imp_a", long_reason))
    assert r["ok"] is True
    from aria_service.intel import self_improve as si
    discarded = asyncio.run(si.list_discarded_improvements(limit=10))
    assert len(discarded[0]["discard_reason"]) == 500


# ── Audit / list endpoint ───────────────────────────────────────────────


def test_list_discarded_returns_newest_first(monkeypatch):
    from aria_service.intel import self_improve as si
    _setup_fake_redis(monkeypatch, {
        si.STAGED_KEY: [_staged_entry("a"), _staged_entry("b"), _staged_entry("c")],
    })
    asyncio.run(si.discard_improvement("a", "first reject"))
    asyncio.run(si.discard_improvement("b", "second reject"))
    asyncio.run(si.discard_improvement("c", "third reject"))

    items = asyncio.run(si.list_discarded_improvements(limit=10))
    assert len(items) == 3
    # Inserted with insert(0) → most recent first
    assert items[0]["id"] == "c"
    assert items[2]["id"] == "a"


def test_list_discarded_respects_limit(monkeypatch):
    from aria_service.intel import self_improve as si
    _setup_fake_redis(monkeypatch, {
        si.STAGED_KEY: [_staged_entry(f"imp_{i}") for i in range(5)],
    })
    for i in range(5):
        asyncio.run(si.discard_improvement(f"imp_{i}", "test"))
    items = asyncio.run(si.list_discarded_improvements(limit=3))
    assert len(items) == 3


# ── Endpoint wiring ─────────────────────────────────────────────────────


def test_discard_endpoint_registered():
    from aria_service.routes import aria as aria_routes
    paths = [getattr(r, "path", "") for r in aria_routes.router.routes]
    assert any(p.endswith("/self/improvements/{improvement_id}/discard") for p in paths), (
        f"R-F574 discard endpoint missing. Sample: {sorted(paths)[:5]}"
    )


def test_discarded_list_endpoint_registered():
    from aria_service.routes import aria as aria_routes
    paths = [getattr(r, "path", "") for r in aria_routes.router.routes]
    assert any(p.endswith("/self/improvements/discarded") for p in paths), (
        f"R-F574 discarded-list endpoint missing"
    )


# ── Capability: R-F558 dedupe scenario end-to-end ───────────────────────


def test_capability_039bf8be_class_can_be_rejected(monkeypatch):
    """Capability proof: the exact symptom that motivated R-F574 —
    a staged improvement that turns out to be a duplicate of an
    existing constitution clause — can now be cleanly rejected
    without TTL-expire or manual Redis surgery."""
    from aria_service.intel import self_improve as si
    _setup_fake_redis(monkeypatch, {
        si.STAGED_KEY: [{
            "id": "039bf8be",
            "status": "staged",
            "file": "aria_service/aria_engine.py",
            "change_type": "prompt_evolution",
            "description": "Approved amendment for P_BANKING_1_RETROACTIVE_CLEAN_STATUS",
            "old_content": "# clauses 1-29",
            "new_content": "# clauses 1-30 (adds P_BANKING_1 retroactive clean)",
            "created_at": time.time() - 3600,
        }],
    })

    result = asyncio.run(si.discard_improvement(
        "039bf8be",
        "R-F558 duplicate: P_BANKING_1 already covered by Clause 27 + R-F534 Premise Verifier",
    ))
    assert result["ok"] is True
    assert result["queue_depth_now"] == 0

    discarded = asyncio.run(si.list_discarded_improvements())
    assert len(discarded) == 1
    assert discarded[0]["id"] == "039bf8be"
    assert "R-F558 duplicate" in discarded[0]["discard_reason"]
