"""R-F2746 — atomic cross-trigger lock for watchlist re-screen (Codex finding 3).

rescreen_watchlist is invoked by three unlocked paths (daily loop, autonomous
sweep, manual API). A per-scope INCR lock serialises SAME-scope runs (so the
daily loop and the autonomous sweep, both global, can't double-process) while
still letting different tenants re-screen concurrently. Drives the real
rescreen_watchlist.
"""
from __future__ import annotations

import asyncio
import pytest

from aria_service.intel import dd_orchestrator as o
import aria_service.intel.sanctions as _sanc
import aria_service.intel._sanctions_classify as _cls


class _Store:
    def __init__(self):
        self.d = {}
    async def get_json(self, k):
        return self.d.get(k)
    async def get_json_strict(self, k):
        # R-F3520 — R-F3506 moved the watchlist read-modify-writes to the STRICT
        # reader; a fake stubbing only `get_json` is bypassed, so rescreen_watchlist
        # returns early with entities_screened: 0 and these tests fail with a
        # PLAUSIBLE empty result rather than an error.
        return self.d.get(k)
    async def set_json(self, k, v, ex=None, keepttl=False):
        self.d[k] = v
    async def lpush(self, k, v):
        self.d.setdefault(k, []).insert(0, v)
    async def ltrim(self, k, a, b):
        pass
    async def expire(self, k, s):
        pass
    async def incr(self, k, amount=1, *, critical=False):
        self.d[k] = int(self.d.get(k, 0)) + amount
        return self.d[k]
    async def delete(self, k):
        self.d.pop(k, None)
        return True


@pytest.fixture
def store(monkeypatch):
    s = _Store()
    import aria_service.intel.redis_store as rs
    for fn in ("get_json", "get_json_strict", "set_json", "lpush", "ltrim", "expire", "incr", "delete"):
        monkeypatch.setattr(rs, fn, getattr(s, fn))
    monkeypatch.setattr(_sanc, "_looks_like_entity_name", lambda n: True, raising=False)

    async def _no_fanout(alert):
        return []
    monkeypatch.setattr(o, "_fan_out_alert_to_deals", _no_fanout, raising=False)
    return s


def _mock_screen(monkeypatch, *, matches, severity):
    async def _fake_screen(name, *a, **k):
        return {"matches": list(matches), "screened": True, "name": name}
    monkeypatch.setattr(_sanc, "screen_with_aliases", _fake_screen, raising=False)
    monkeypatch.setattr(_sanc, "fuzzy_screen", _fake_screen, raising=False)
    monkeypatch.setattr(
        _cls, "classify_matches",
        lambda m, query_name="": {"worst_severity": severity, "summary": ""})


# ── same-scope overlap is skipped (daily loop vs autonomous sweep) ─────────────
def test_same_scope_second_run_skipped(store, monkeypatch):
    async def run():
        store.d[o.WATCHLIST_KEY] = [{"name": "Assan Group", "user_id": "u1"}]
        _mock_screen(monkeypatch, matches=[{"score": 0.9}], severity="clean")
        # a same-scope run is already in flight (holds the global lock)
        assert await o._acquire_rescreen_lock("global") is True
        return await o.rescreen_watchlist(user_id=None)
    r = asyncio.run(run())
    assert r.get("skipped") == "locked", r
    assert r["entities_screened"] == 0


# ── a different tenant is NOT blocked by the global lock ───────────────────────
def test_different_scope_not_blocked(store, monkeypatch):
    async def run():
        async def fake_gw(uid=None, dom=None):
            return [{"name": "Beta Corp", "user_id": "userX"}]
        monkeypatch.setattr(o, "get_watchlist", fake_gw)
        _mock_screen(monkeypatch, matches=[{"score": 0.9}], severity="clean")
        assert await o._acquire_rescreen_lock("global") is True   # global held
        return await o.rescreen_watchlist(user_id="userX", user_email_domain="x.com")
    r = asyncio.run(run())
    assert r.get("skipped") != "locked", r      # userX scope proceeds
    assert r["entities_screened"] == 1


# ── the lock is released on the normal path (next run can acquire) ─────────────
def test_lock_released_after_run(store, monkeypatch):
    async def run():
        store.d[o.WATCHLIST_KEY] = [{"name": "Assan Group", "user_id": "u1"}]
        _mock_screen(monkeypatch, matches=[{"score": 0.9}], severity="clean")
        r1 = await o.rescreen_watchlist(user_id=None)
        # released → a fresh acquire succeeds (incr on a deleted key returns 1)
        reacquired = await o._acquire_rescreen_lock("global")
        return r1, reacquired
    r1, reacquired = asyncio.run(run())
    assert r1.get("skipped") != "locked"
    assert reacquired is True
