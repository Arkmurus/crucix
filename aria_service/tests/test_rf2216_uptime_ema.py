"""Capability tests for the source-uptime auto-suspend revival (2026-07-01).

R-F2216 — reliability was HARDCODED 0.5, so the auto-suspend gate (reliability<0.3)
          could NEVER fire; the sweep constants were also too slow.
R-F2223 — the /run endpoint blocked ~120s on the 200-source sweep and timed out
          (502/000); now backgrounded.
R-F2225 — the sweep did ~400 sequential per-source state_store ops (lpush/lrange)
          → took minutes, never finished in the request/cron budget. Now ONE running
          -state blob (read once / write once); reliability is a running EMA.

Tests invoke the REAL pure helpers and drive the REAL run_daily_ping / endpoint.
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
    assert m._MAX_CONCURRENT_PINGS >= 30
    # R-F2266 — _PING_TIMEOUT_S (float) became _PING_TIMEOUT (httpx.Timeout) with a
    # 12s ceiling; still bounded so 200/40 concurrent stays inside the cron budget.
    import httpx
    assert isinstance(m._PING_TIMEOUT, httpx.Timeout)
    assert (m._PING_TIMEOUT.read or 0) <= 15.0 and (m._PING_TIMEOUT.connect or 0) <= 12.0


# ── R-F2225 running-state EMA (the inert-auto-suspend + slow-I/O root causes) ──
def test_rf2225_state_ema_degrades_and_recovers():
    st = m._update_source_state(None, False, "t")        # 1st sample: fail
    assert st == {"ema": 0.0, "n": 1, "fails": 1, "last_ok": False, "last_check": "t"}
    st = m._update_source_state(st, False, "t")
    st = m._update_source_state(st, False, "t")           # 3 consecutive fails
    assert st["n"] == 3 and st["fails"] == 3
    assert m._suspend_reliability(st) < m._RELIABILITY_SUSPEND_THRESHOLD   # was permanently 0.5
    st = m._update_source_state(st, True, "t")            # one success
    assert st["fails"] == 0 and st["ema"] > 0.0           # streak cleared, EMA rises


def test_rf2225_thin_history_is_neutral():
    # <3 samples → neutral 0.5 so a barely-seen source is never suspended on thin data
    st = m._update_source_state(None, False, "t")
    assert m._suspend_reliability(st) == 0.5
    st = m._update_source_state(st, False, "t")
    assert m._suspend_reliability(st) == 0.5


def test_rf2225_healthy_source_high_reliability():
    st = None
    for _ in range(5):
        st = m._update_source_state(st, True, "t")
    assert m._suspend_reliability(st) > 0.9


# ── R-F2216/2225 the gate now actually FIRES end-to-end ──────────────────────
async def test_rf2225_autosuspend_fires_end_to_end(monkeypatch):
    src = {"name": "DeadSrc", "url": "https://dead.invalid/x", "reliability": 0.5, "tier": "tier_3"}
    monkeypatch.setattr(m, "_get_registered_sources", _areturn([src]))

    async def _fail_ping(s, client=None):
        return {"name": "DeadSrc", "url": s["url"], "ok": False, "status": None,
                "latency_ms": 0, "error": "timeout", "checked_at": "2026-07-01T00:00:00Z"}
    monkeypatch.setattr(m, "_ping_one", _fail_ping)

    # pre-seed the running-state blob so DeadSrc already has a degraded history
    # (n=3, ema<0.3, fails=2) — one more fail this sweep → fails=3 → suspend.
    seeded = {"DeadSrc": {"ema": 0.05, "n": 3, "fails": 2, "last_ok": False, "last_check": "t"}}
    monkeypatch.setattr(m, "_get_source_state", _areturn(seeded))
    monkeypatch.setattr(m, "_set_source_state", _anoop)
    monkeypatch.setattr(m, "_get_suspended", _areturn(set()))
    monkeypatch.setattr(rss, "set_json", _anoop)
    monkeypatch.setattr(rss, "get_json", _areturn([]))
    import aria_service.intel.brain_hook as bh
    monkeypatch.setattr(bh, "absorb", _anoop)
    # suspend() writes _K_SUSPENDED via _set_suspended → stub it
    monkeypatch.setattr(m, "_set_suspended", _anoop)

    res = await m.run_daily_ping()
    assert res.get("sources_checked") == 1
    assert "DeadSrc" in (res.get("suspended_now") or []) or "DeadSrc" in (res.get("currently_suspended") or [])


# ── R-F2223 the endpoint backgrounds the sweep (no more 120s block) ──────────
async def test_rf2223_endpoint_backgrounds_sweep(monkeypatch):
    import asyncio
    from aria_service.routes import aria as A
    ran = {"done": False}

    async def _fake_sweep():
        ran["done"] = True
        return {"ok": True, "sources_checked": 1}
    monkeypatch.setattr(m, "run_daily_ping", _fake_sweep)

    out = await A.sources_uptime_run_ep()
    assert out.get("ok") is True and out.get("started") is True   # returns immediately
    await asyncio.sleep(0.05)
    assert ran["done"] is True                                    # sweep ran in background
