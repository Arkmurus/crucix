"""R-F2559 — system-public watchlist + GDPR-safe Golden Intel promotion.

The critical property: the promotion adapter is FAIL-CLOSED — it promotes ONLY
scope=="system_public" alerts and REFUSES any alert carrying a tenant field
(user_id / user_email_domain / impacted_deals / run_id). The public store is
physically separate from the customer watchlist and never holds tenant data.
"""
from __future__ import annotations

import asyncio
import json

from aria_service.intel import golden_intel_bridge as bridge


def _pub_alert(**kw):
    a = {
        "entity": "Wagner Group", "change_type": "new_hit", "old_status": "CLEAN",
        "new_status": "HIT", "old_score": 0.0, "new_score": 0.9,
        "detail": "Previously clean, now sanctioned. OFAC SDN match.",
        "timestamp": "2026-07-12T10:00:00+00:00", "scope": "system_public",
    }
    a.update(kw)
    return a


# ── Adapter: GDPR fail-closed (the headline property) ─────────────────────────
def test_public_new_hit_is_decision_grade(monkeypatch):
    from aria_service.intel import dd_orchestrator
    async def fake(since_hours=168):
        return [_pub_alert()]
    monkeypatch.setattr(dd_orchestrator, "get_public_watchlist_alerts", fake)
    findings = asyncio.run(bridge._public_watchlist_adapter())
    assert len(findings) == 1
    f = findings[0]
    assert f["signal_type"] == "sanctions_change" and f["priority"] == "HIGH" and f["source_tier"] == "tier_1b"
    assert bridge._is_distribution_ready(bridge._normalize_finding_to_signal(f)) is True


def test_adapter_refuses_any_tenant_field(monkeypatch):
    from aria_service.intel import dd_orchestrator
    fails: list = []
    monkeypatch.setattr(bridge, "wire_failure", lambda *a, **k: fails.append((a, k)))
    for leak in ({"user_id": "u1"}, {"user_email_domain": "acme.com"},
                 {"impacted_deals": [{"id": "d1"}]}, {"run_id": "dd_123"}):
        async def fake(since_hours=168, _l=leak):
            return [_pub_alert(**_l)]
        monkeypatch.setattr(dd_orchestrator, "get_public_watchlist_alerts", fake)
        out = asyncio.run(bridge._public_watchlist_adapter())
        assert out == [], f"tenant alert {leak} was NOT refused"
    assert len(fails) >= 4, "tenant-field refusals were not recorded as gaps"


def test_adapter_requires_system_public_scope(monkeypatch):
    from aria_service.intel import dd_orchestrator
    async def fake(since_hours=168):
        return [_pub_alert(scope=""), _pub_alert(scope="user")]
    monkeypatch.setattr(dd_orchestrator, "get_public_watchlist_alerts", fake)
    assert asyncio.run(bridge._public_watchlist_adapter()) == []


def test_score_change_is_mining_queue(monkeypatch):
    from aria_service.intel import dd_orchestrator
    async def fake(since_hours=168):
        return [_pub_alert(change_type="score_change", new_status="CLEAN")]
    monkeypatch.setattr(dd_orchestrator, "get_public_watchlist_alerts", fake)
    f = asyncio.run(bridge._public_watchlist_adapter())[0]
    assert f["priority"] == "MEDIUM"
    assert bridge._is_distribution_ready(bridge._normalize_finding_to_signal(f)) is False


# ── Store: separate + tenant-free ─────────────────────────────────────────────
def _rs_stub(monkeypatch):
    store: dict = {}
    lists: dict = {}
    from aria_service.intel import redis_store as rs
    async def get_json(k):
        return store.get(k)
    async def set_json(k, v, *a, **kw):
        store[k] = v
    async def lpush(k, v, **kw):
        lists.setdefault(k, []).insert(0, v)
    async def ltrim(k, a, b):
        if a == 0:
            lists[k] = lists.get(k, [])[:b + 1]
    async def lrange(k, a, b):
        return lists.get(k, [])
    async def expire(k, s):
        return True
    for n, f in (("get_json", get_json), ("set_json", set_json), ("lpush", lpush),
                 ("ltrim", ltrim), ("lrange", lrange), ("expire", expire)):
        monkeypatch.setattr(rs, n, f)
    return store, lists


def test_add_public_is_tenant_free_and_separate(monkeypatch):
    from aria_service.intel import dd_orchestrator as dd
    from aria_service.intel import sanctions
    monkeypatch.setattr(sanctions, "_looks_like_entity_name", lambda n: True, raising=False)
    store, _ = _rs_stub(monkeypatch)
    asyncio.run(dd.add_public_watchlist_entity("Wagner Group"))
    pub = store.get(dd.PUBLIC_WATCHLIST_KEY)
    assert pub and len(pub) == 1
    e = pub[0]
    assert e["scope"] == "system_public"
    for tenant in ("user_id", "user_email_domain", "share_to_company"):
        assert tenant not in e, f"public entry carried tenant field {tenant}"
    # did NOT write the customer watchlist store
    assert not store.get(dd.WATCHLIST_KEY)
    # idempotent dedup (case-insensitive)
    asyncio.run(dd.add_public_watchlist_entity("wagner group"))
    assert len(store.get(dd.PUBLIC_WATCHLIST_KEY)) == 1


def test_rescreen_emits_tenant_free_alert(monkeypatch):
    from aria_service.intel import dd_orchestrator as dd
    from aria_service.intel import sanctions, _sanctions_classify
    store, lists = _rs_stub(monkeypatch)
    store[dd.PUBLIC_WATCHLIST_KEY] = [{"name": "Wagner Group", "scope": "system_public"}]

    async def fake_screen(name):
        return {"matches": [{"name": name, "score": 0.95}]}
    monkeypatch.setattr(sanctions, "screen_with_aliases", fake_screen, raising=False)
    monkeypatch.setattr(_sanctions_classify, "classify_matches",
                        lambda m, query_name=None: {"worst_severity": "red", "summary": "OFAC SDN match"})
    monkeypatch.setattr(dd, "_derive_score_from_matches", lambda m: 0.95)

    res = asyncio.run(dd.rescreen_public_watchlist())
    assert res["entities_screened"] == 1
    raw = lists.get(dd.PUBLIC_WATCHLIST_ALERTS_KEY, [])
    assert len(raw) == 1
    a = json.loads(raw[0])
    assert a["change_type"] == "new_hit" and a["scope"] == "system_public"
    for tenant in ("user_id", "user_email_domain", "share_to_company", "run_id", "impacted_deals"):
        assert tenant not in a, f"tenant field {tenant} leaked into a PUBLIC alert"


def test_rescreen_suppresses_removed_never_false_clean(monkeypatch):
    """never-false-clean (#G): HIT->CLEAN (a dead store could fabricate 'removed')
    must NOT emit a promotable alert, but MUST still update state for future diffs."""
    from aria_service.intel import dd_orchestrator as dd
    from aria_service.intel import sanctions, _sanctions_classify
    store, lists = _rs_stub(monkeypatch)
    store[dd.PUBLIC_WATCHLIST_KEY] = [{"name": "Wagner Group", "scope": "system_public"}]
    store[dd.PUBLIC_WATCHLIST_STATE_KEY] = {"wagner group": {"status": "HIT", "score": 0.9}}

    async def fake_screen(name):
        return {"matches": []}
    monkeypatch.setattr(sanctions, "screen_with_aliases", fake_screen, raising=False)
    monkeypatch.setattr(_sanctions_classify, "classify_matches",
                        lambda m, query_name=None: {"worst_severity": "clean", "summary": ""})
    monkeypatch.setattr(dd, "_derive_score_from_matches", lambda m: 0.0)

    asyncio.run(dd.rescreen_public_watchlist())
    assert lists.get(dd.PUBLIC_WATCHLIST_ALERTS_KEY, []) == []   # 'removed' NOT emitted
    assert store[dd.PUBLIC_WATCHLIST_STATE_KEY]["wagner group"]["status"] == "CLEAN"  # state tracked


if __name__ == "__main__":
    import sys
    import pytest
    sys.exit(pytest.main([__file__, "-v"]))
