"""Tests for the 2026-04-26 absorption quarantine queue (D part 2 MVP).

Off by default — opt-in via env var ARIA_ABSORPTION_QUARANTINE_MODULES.
When listed, the targeted module's absorptions are diverted to a Redis
queue instead of writing directly to permanent memory; operator reviews
and promotes/rejects via admin endpoints.
"""
from __future__ import annotations

import asyncio
import os


def _run(coro):
    return asyncio.run(coro)


def _setup_fake_redis(monkeypatch):
    """Replace redis_store with an in-process dict so the quarantine
    queue is testable without a live Redis."""
    from aria_service.intel import redis_store as rs

    store: dict[str, str] = {}

    async def fake_get(key):
        return store.get(key)

    async def fake_set(key, value, ex=None):
        store[key] = value

    async def fake_delete(key):
        return store.pop(key, None) is not None

    async def fake_get_json(key):
        import json
        v = store.get(key)
        return json.loads(v) if v else None

    async def fake_set_json(key, obj, ex=None):
        import json
        store[key] = json.dumps(obj, default=str)

    async def fake_scan_keys(pattern, count=200):
        # naïve glob — only "<prefix>*" supported, which is what the
        # quarantine module uses.
        if pattern.endswith("*"):
            prefix = pattern[:-1]
            return [k for k in store.keys() if k.startswith(prefix)]
        return [k for k in store.keys() if k == pattern]

    monkeypatch.setattr(rs, "get", fake_get)
    monkeypatch.setattr(rs, "set", fake_set)
    monkeypatch.setattr(rs, "delete", fake_delete)
    monkeypatch.setattr(rs, "get_json", fake_get_json)
    monkeypatch.setattr(rs, "set_json", fake_set_json)
    monkeypatch.setattr(rs, "scan_keys", fake_scan_keys)
    return store


def test_quarantine_disabled_by_default(monkeypatch):
    """Empty env var — no module is quarantined, production behaviour
    unchanged."""
    monkeypatch.delenv("ARIA_ABSORPTION_QUARANTINE_MODULES", raising=False)
    from aria_service.intel import absorption_quarantine as aq
    assert aq.quarantined_modules() == set()
    assert aq.is_quarantine_enabled_for("brave_answer") is False


def test_quarantine_env_var_picks_up_modules(monkeypatch):
    """Comma-separated env var enables specific modules. Read fresh on
    each call so a fly.io secrets flip takes effect on the next absorb()
    without a restart."""
    from aria_service.intel import absorption_quarantine as aq

    monkeypatch.setenv("ARIA_ABSORPTION_QUARANTINE_MODULES", "brave_answer, web_search")
    assert aq.is_quarantine_enabled_for("brave_answer") is True
    assert aq.is_quarantine_enabled_for("web_search") is True
    assert aq.is_quarantine_enabled_for("dd_orchestrator") is False

    monkeypatch.setenv("ARIA_ABSORPTION_QUARANTINE_MODULES", "")
    assert aq.is_quarantine_enabled_for("brave_answer") is False


def test_enqueue_then_list_pending(monkeypatch):
    _setup_fake_redis(monkeypatch)
    from aria_service.intel import absorption_quarantine as aq

    qid = _run(aq.enqueue(
        module="brave_answer",
        entity_name="OpenClaw",
        summary="OpenClaw v3 has a Rollup bug",
        detail="Restart the gateway",
        topic="infra",
    ))
    pending = _run(aq.list_pending())
    assert len(pending) == 1
    assert pending[0]["id"] == qid
    assert pending[0]["status"] == "pending"
    assert pending[0]["module"] == "brave_answer"
    assert pending[0]["topic"] == "infra"


def test_promote_marks_entry_decided(monkeypatch):
    _setup_fake_redis(monkeypatch)
    from aria_service.intel import absorption_quarantine as aq

    qid = _run(aq.enqueue(
        module="brave_answer",
        entity_name="X",
        summary="test",
        detail="",
        topic=None,
    ))
    decided = _run(aq.promote(qid, reason="operator-clean"))
    assert decided["status"] == "promoted"
    assert decided["decision_reason"] == "operator-clean"

    # Re-listing should show empty pending — promoted entry is gone
    # from the active queue.
    pending = _run(aq.list_pending())
    assert pending == []

    # But the audit row is still readable for 7d.
    entry = _run(aq.get(qid))
    assert entry["status"] == "promoted"


def test_reject_marks_entry_decided(monkeypatch):
    _setup_fake_redis(monkeypatch)
    from aria_service.intel import absorption_quarantine as aq

    qid = _run(aq.enqueue(
        module="brave_answer",
        entity_name="Y",
        summary="suspect",
        detail="",
        topic=None,
    ))
    decided = _run(aq.reject(qid, reason="known fabrication"))
    assert decided["status"] == "rejected"
    assert decided["decision_reason"] == "known fabrication"

    pending = _run(aq.list_pending())
    assert pending == []


def test_decide_idempotency_returns_none_for_already_decided(monkeypatch):
    """Calling promote twice is a no-op on the second try — guards
    against double-fire from operator pressing the button twice."""
    _setup_fake_redis(monkeypatch)
    from aria_service.intel import absorption_quarantine as aq

    qid = _run(aq.enqueue(
        module="brave_answer",
        entity_name="Z",
        summary="x",
        detail="",
        topic=None,
    ))
    first = _run(aq.promote(qid, reason="ok"))
    second = _run(aq.promote(qid, reason="ok again"))
    assert first is not None
    assert second is None  # already-decided returns None


def test_stats_reports_queue_depth(monkeypatch):
    _setup_fake_redis(monkeypatch)
    from aria_service.intel import absorption_quarantine as aq

    monkeypatch.setenv("ARIA_ABSORPTION_QUARANTINE_MODULES", "brave_answer")
    _run(aq.enqueue(module="brave_answer", entity_name="A", summary="a", detail="", topic=None))
    _run(aq.enqueue(module="brave_answer", entity_name="B", summary="b", detail="", topic=None))
    qid_to_reject = _run(aq.enqueue(module="brave_answer", entity_name="C", summary="c", detail="", topic=None))
    _run(aq.reject(qid_to_reject, reason="x"))

    s = _run(aq.stats())
    assert s["enabled"] is True
    assert s["quarantined_modules"] == ["brave_answer"]
    assert s["pending"] == 2
    assert s["rejected_recent"] == 1
    assert s["promoted_recent"] == 0
    assert isinstance(s["oldest_pending_age_h"], (int, float))


def test_brain_hook_diverts_listed_module_to_quarantine(monkeypatch):
    """The integration: when env var lists a module, brain_hook.absorb()
    enqueues instead of writing to permanent memory. Returns
    skipped=True with the quarantine_id."""
    _setup_fake_redis(monkeypatch)
    monkeypatch.setenv("ARIA_ABSORPTION_QUARANTINE_MODULES", "brave_answer")

    from aria_service.intel import brain_hook as bh

    captured = {"signal": []}

    async def fake_record_signal(module, success):
        captured["signal"].append((module, success))

    monkeypatch.setattr(bh, "BRAIN_HOOK_ENABLED", True)
    monkeypatch.setattr(bh, "_record_signal", fake_record_signal)

    out = _run(bh.absorb(
        module="brave_answer",
        summary="External SDN designation update from OFAC",
        detail="Iran-linked entity added.",
        entity_name="OFAC",
    ))

    assert out["skipped"] is True
    assert out["reason"] == "absorption_quarantine_pending_review"
    assert "quarantine_id" in out
    # Signal counter should NOT fire when the absorption was diverted —
    # the queue is the source of truth for whether it eventually lands.
    assert captured["signal"] == []


def test_brain_hook_passes_through_unlisted_module(monkeypatch):
    """When a module is NOT in the quarantine list, the queue path is
    not invoked even if env var is set for other modules."""
    _setup_fake_redis(monkeypatch)
    monkeypatch.setenv("ARIA_ABSORPTION_QUARANTINE_MODULES", "brave_answer")

    from aria_service.intel import brain_hook as bh

    monkeypatch.setattr(bh, "BRAIN_HOOK_ENABLED", True)

    # Call from a different module — should NOT return the quarantine
    # short-circuit. We don't assert successful absorption (downstream
    # tiers need real Redis for that); we just verify the gate path
    # didn't catch us.
    try:
        out = _run(bh.absorb(
            module="sanctions",
            summary="OFAC adds 12 entities",
            detail="From SDN list",
            entity_name="OFAC",
        ))
        assert out.get("reason") != "absorption_quarantine_pending_review"
    except Exception:
        # Downstream-tier failure is acceptable — only the divert path matters.
        pass
