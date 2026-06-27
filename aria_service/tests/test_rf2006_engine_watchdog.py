"""R-F2006 (engine liveness watchdog) + R-F2007 (adversarial breaker override).

R-F2006: the autonomous engine had NO liveness signal, so a forgotten pause
(R-F2004) left it fire=0 for 187h with nothing flagging it. The engine now
writes last_tick_ts (every loop) + last_fire_ts (on fire); check_engine_liveness
classifies the state and a watchdog loop alerts the operator. These drive the
REAL check function.

R-F2007: adversarial_challenge (weekly burst self-eval) ran absorb p95 ~37s,
tripping the GLOBAL 6s breaker and skipping expensive tiers for every module. A
per-module latency override gives it headroom (the established R-F1598 pattern).
"""
import asyncio
import time

from aria_service.autonomous import engine, safety
from aria_service.intel import redis_store as rs
from aria_service.intel import brain_hook


def _run(coro):
    return asyncio.run(coro)


def _set_heartbeats(monkeypatch, tick_age=None, fire_age=None):
    now = time.time()
    store = {}
    if tick_age is not None:
        store["crucix:autonomous:last_tick_ts"] = str(int(now - tick_age))
    if fire_age is not None:
        store["crucix:autonomous:last_fire_ts"] = str(int(now - fire_age))

    async def fake_get(k):
        return store.get(k)
    monkeypatch.setattr(rs, "get", fake_get)


def _set_state(monkeypatch, enabled=True, paused=False):
    monkeypatch.setattr(engine, "is_enabled", lambda: enabled)
    async def _paused():
        return paused
    monkeypatch.setattr(safety, "is_engine_paused", _paused)


# ── R-F2006 engine liveness ───────────────────────────────────────────────────
def test_healthy_when_ticking_and_firing(monkeypatch):
    _set_heartbeats(monkeypatch, tick_age=30, fire_age=120)
    _set_state(monkeypatch, enabled=True, paused=False)
    s = _run(engine.check_engine_liveness())
    assert s["healthy"] is True and s["problem"] is None


def test_loop_dead_when_tick_stale(monkeypatch):
    _set_heartbeats(monkeypatch, tick_age=1200, fire_age=120)   # 20 min, no tick
    _set_state(monkeypatch, enabled=True, paused=False)
    s = _run(engine.check_engine_liveness())
    assert s["healthy"] is False and "NOT TICKING" in s["problem"]


def test_master_disabled_alerts(monkeypatch):
    _set_heartbeats(monkeypatch, tick_age=30, fire_age=120)
    _set_state(monkeypatch, enabled=False, paused=False)
    s = _run(engine.check_engine_liveness())
    assert s["healthy"] is False and "MASTER-DISABLED" in s["problem"]


def test_paused_is_not_an_alert(monkeypatch):
    # stale fire but PAUSED -> R-F2004 auto-expires pauses, so this is not an alert
    _set_heartbeats(monkeypatch, tick_age=30, fire_age=999_999)
    _set_state(monkeypatch, enabled=True, paused=True)
    s = _run(engine.check_engine_liveness())
    assert s["healthy"] is True and s["paused"] is True


def test_alive_but_not_firing_alerts(monkeypatch):
    _set_heartbeats(monkeypatch, tick_age=30, fire_age=4 * 3600)   # 4h since fire
    _set_state(monkeypatch, enabled=True, paused=False)
    s = _run(engine.check_engine_liveness())
    assert s["healthy"] is False and "FIRING NOTHING" in s["problem"]


def test_never_raises_on_redis_error(monkeypatch):
    async def boom(_k):
        raise RuntimeError("redis down")
    monkeypatch.setattr(rs, "get", boom)
    _set_state(monkeypatch, enabled=True, paused=False)
    s = _run(engine.check_engine_liveness())   # must not raise
    assert "healthy" in s   # degrades gracefully (no heartbeats -> ages None -> healthy)


# ── R-F2007 adversarial breaker override ─────────────────────────────────────
def test_adversarial_override_prevents_global_trip():
    assert brain_hook._MODULE_LATENCY_OVERRIDES.get("adversarial_challenge") == 60000
    # the breaker's per-module threshold resolver honours it
    assert brain_hook._module_trip_threshold("adversarial_challenge") == 60000
    # the observed 36.87s p95 is BELOW the override -> no global trip
    assert 36870 < brain_hook._module_trip_threshold("adversarial_challenge")
    # a normal module still uses the strict global threshold
    assert brain_hook._module_trip_threshold("some_other_module") == brain_hook._LATENCY_TRIP_MS
