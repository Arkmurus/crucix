"""Capability tests for R-F2216 — source_uptime_monitor auto-suspend revival.

Root causes fixed:
  (1) reliability was HARDCODED to 0.5 in _get_registered_sources, and the
      auto-suspend gate needs reliability < 0.3 → the gate could NEVER fire.
      Now reliability is an EMA derived from the recorded ping history.
  (2) the sweep was 10 concurrent × 10s → could exceed edge/cron budgets and
      never complete. Now 40 × 6s. (constant assertions below)

Tests invoke the REAL _reliability_ema and drive the REAL run_daily_ping suspend
path with a stubbed redis/brain layer (no network, no live DB).
"""
import json

import aria_service.intel.source_uptime_monitor as m
import aria_service.intel.redis_store as rss


def _areturn(val):
    async def _f(*a, **k):
        return val
    return _f


async def _anoop(*a, **k):
    return None


# ── R-F2216 sweep-speed constants (the never-completes root cause) ────────────
def test_rf2216_sweep_bounded_fast():
    # 200 sources / 40 concurrent × 6s ≈ 30s worst case — well under the 122s edge
    # proxy limit and the 300s cron budget (was 10 × 10s ≈ 200s).
    assert m._MAX_CONCURRENT_PINGS >= 30
    assert m._PING_TIMEOUT_S <= 8.0


# ── R-F2216 reliability EMA (the inert-auto-suspend root cause) ───────────────
async def test_rf2216_ema_dead_source_below_threshold(monkeypatch):
    monkeypatch.setattr(rss, "lrange", _areturn([json.dumps({"ok": False}) for _ in range(6)]))
    ema = await m._reliability_ema("dead")
    assert ema < m._RELIABILITY_SUSPEND_THRESHOLD, ema   # was permanently 0.5 → never < 0.3


async def test_rf2216_ema_healthy_source_high(monkeypatch):
    monkeypatch.setattr(rss, "lrange", _areturn([json.dumps({"ok": True}) for _ in range(6)]))
    assert await m._reliability_ema("live") > 0.8


async def test_rf2216_ema_thin_history_is_neutral(monkeypatch):
    # <3 pings → neutral 0.5 so a barely-seen source is never suspended on thin data
    monkeypatch.setattr(rss, "lrange", _areturn([json.dumps({"ok": False})]))
    assert await m._reliability_ema("new") == 0.5


# ── R-F2216 the gate now actually FIRES for a dead source ─────────────────────
async def test_rf2216_autosuspend_fires_end_to_end(monkeypatch):
    # 1 registered source that pings as dead, with an all-fail history.
    src = {"name": "DeadSrc", "url": "https://dead.invalid/x", "reliability": 0.5, "tier": "tier_3"}
    monkeypatch.setattr(m, "_get_registered_sources", _areturn([src]))

    async def _fail_ping(s):
        return {"name": "DeadSrc", "url": s["url"], "ok": False, "status": None,
                "latency_ms": 0, "error": "timeout", "checked_at": "2026-07-01T00:00:00Z"}
    monkeypatch.setattr(m, "_ping_one", _fail_ping)

    # history = 5 consecutive fails → _consecutive_failures>=3 AND EMA<0.3
    monkeypatch.setattr(rss, "lrange", _areturn([json.dumps({"ok": False}) for _ in range(5)]))
    monkeypatch.setattr(rss, "lpush", _anoop)      # _record_ping
    monkeypatch.setattr(rss, "get_json", _areturn([]))   # _get_suspended → none yet
    monkeypatch.setattr(rss, "set_json", _anoop)   # _set_suspended + last_run
    monkeypatch.setattr(rss, "set", _anoop)
    import aria_service.intel.brain_hook as bh
    monkeypatch.setattr(bh, "absorb", _anoop)

    res = await m.run_daily_ping()
    assert res.get("sources_checked") == 1
    # the dead source is now auto-suspended (was impossible pre-fix)
    assert "DeadSrc" in (res.get("suspended_now") or []) or "DeadSrc" in (res.get("currently_suspended") or [])


# ── R-F2223 — single-read health + backgrounded endpoint (never-completes fix) ─
async def test_rf2223_source_health_single_read(monkeypatch):
    monkeypatch.setattr(rss, "lrange", _areturn([json.dumps({"ok": False}) for _ in range(5)]))
    fails, ema = await m._source_health("dead")
    assert fails >= 3 and ema < m._RELIABILITY_SUSPEND_THRESHOLD

    monkeypatch.setattr(rss, "lrange", _areturn([json.dumps({"ok": True}) for _ in range(5)]))
    fails, ema = await m._source_health("live")
    assert fails == 0 and ema > 0.8

    monkeypatch.setattr(rss, "lrange", _areturn([json.dumps({"ok": False})]))   # thin
    fails, ema = await m._source_health("new")
    assert ema == 0.5


async def test_rf2223_endpoint_backgrounds_sweep(monkeypatch):
    import asyncio
    from aria_service.routes import aria as A
    ran = {"done": False}

    async def _fake_sweep():
        ran["done"] = True
        return {"ok": True, "sources_checked": 1}
    monkeypatch.setattr(m, "run_daily_ping", _fake_sweep)

    out = await A.sources_uptime_run_ep()
    # endpoint returns IMMEDIATELY (does not await the full sweep) — the fix for the
    # 502/000 timeout on the synchronous 200-source sweep.
    assert out.get("ok") is True and out.get("started") is True
    await asyncio.sleep(0.05)          # let the background task run
    assert ran["done"] is True
