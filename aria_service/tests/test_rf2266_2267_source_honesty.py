"""Capability tests — source-uptime checker honesty + self_healing tile fix.

R-F2266 — the uptime monitor conflated "reachable but WAF/auth-blocked" (401/403/
          405/406/429/451) with "down", so ~38/69 live Tier-1a sources (fatf-gafi.org,
          adb.org, …) were falsely reported down and could accrue toward auto-suspend.
          The honest verdict: a server that ANSWERS is reachable=UP; only transport
          failure, 404/410 (dead URL) or 5xx counts as down.
R-F2267 — self_healing called source_uptime_monitor.get_status() which never existed
          (module exposes health()) → the source-health tile threw every cycle.

These drive the REAL classifier, the REAL _ping_one (mocked transport), and the REAL
self_healing source-health branch.
"""
import types

import httpx
import pytest

import aria_service.intel.source_uptime_monitor as m


# ── R-F2266 classifier: the core honesty contract ────────────────────────────
def test_rf2266_reachable_but_blocked_is_up():
    for code in (401, 403, 405, 406, 429, 451):
        reachable, cls = m._classify_ping(code, None)
        assert reachable is True, f"HTTP {code} must be reachable (server answered)"
        assert cls == "blocked"


def test_rf2266_2xx_3xx_up():
    for code in (200, 204, 301, 302, 308):
        reachable, cls = m._classify_ping(code, None)
        assert reachable is True and cls == "up"


def test_rf2266_genuinely_down_stays_down():
    assert m._classify_ping(404, None) == (False, "not_found")
    assert m._classify_ping(410, None) == (False, "not_found")
    assert m._classify_ping(500, None) == (False, "server_error")
    assert m._classify_ping(503, None) == (False, "server_error")
    # transport failure (DNS / connect / read timeout)
    reachable, cls = m._classify_ping(None, "ConnectTimeout")
    assert reachable is False and cls == "ConnectTimeout"


# ── R-F2266 _ping_one: a 403 WAF block must NOT be marked down ────────────────
async def test_rf2266_ping_one_403_is_reachable(monkeypatch):
    class _Resp:
        status_code = 403

    class _Client:
        def __init__(self, *a, **k): ...
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def head(self, *a, **k): return _Resp()
        async def get(self, *a, **k): return _Resp()

    monkeypatch.setattr(httpx, "AsyncClient", _Client)
    res = await m._ping_one({"name": "fatf", "url": "https://www.fatf-gafi.org/"})
    assert res["ok"] is True                     # reachable → UP (was False before R-F2266)
    assert res["classification"] == "blocked"
    assert res["status"] == 403


async def test_rf2266_ping_one_404_is_down(monkeypatch):
    class _Resp:
        status_code = 404

    class _Client:
        def __init__(self, *a, **k): ...
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def head(self, *a, **k): return _Resp()
        async def get(self, *a, **k): return _Resp()

    monkeypatch.setattr(httpx, "AsyncClient", _Client)
    res = await m._ping_one({"name": "dead", "url": "https://example.gov/gone"})
    assert res["ok"] is False and res["classification"] == "not_found"


async def test_rf2675_ping_one_confirms_head_404_with_get(monkeypatch):
    """Sites with broken HEAD but healthy GET must not be reported down."""
    class _Resp:
        def __init__(self, status_code):
            self.status_code = status_code

    class _Client:
        def __init__(self, *a, **k):
            self.calls = []
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def head(self, *a, **k):
            self.calls.append("HEAD")
            return _Resp(404)
        async def get(self, *a, **k):
            self.calls.append("GET")
            return _Resp(200)

    monkeypatch.setattr(httpx, "AsyncClient", _Client)
    res = await m._ping_one({"name": "qinetiq_press", "url": "https://www.qinetiq.com/"})
    assert res["ok"] is True
    assert res["classification"] == "up"
    assert res["status"] == 200


async def test_rf2675_ping_one_readtimeout_uses_light_get(monkeypatch):
    """ReadTimeout on HEAD can still be a live source if lightweight GET works."""
    class _Resp:
        status_code = 200

    class _Client:
        def __init__(self, *a, **k):
            self.get_headers = []
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def head(self, *a, **k):
            raise httpx.ReadTimeout("slow HEAD")
        async def get(self, *a, **k):
            self.get_headers.append(k.get("headers") or {})
            return _Resp()

    monkeypatch.setattr(httpx, "AsyncClient", _Client)
    res = await m._ping_one({"name": "au_dfat_sanctions", "url": "https://www.dfat.gov.au/international-relations/security/sanctions"})
    assert res["ok"] is True
    assert res["classification"] == "up"
    assert res["status"] == 200


# ── R-F2266 end-to-end: blocked sources count as UP and are NOT suspended ─────
async def test_rf2266_blocked_sources_not_suspended(monkeypatch):
    srcs = [{"name": f"Blocked{i}", "url": f"https://blocked{i}.gov/"} for i in range(3)]
    monkeypatch.setattr(m, "_get_registered_sources", _areturn(srcs))

    async def _blocked_ping(s, client=None):
        # server answered 403 → honest classifier marks reachable
        return {"name": s["name"], "url": s["url"], "ok": True, "status": 403,
                "classification": "blocked", "latency_ms": 10, "error": "blocked",
                "checked_at": "2026-07-01T00:00:00Z"}
    monkeypatch.setattr(m, "_ping_one", _blocked_ping)
    monkeypatch.setattr(m, "_get_source_state", _areturn({}))
    monkeypatch.setattr(m, "_set_source_state", _anoop)
    monkeypatch.setattr(m, "_get_suspended", _areturn(set()))
    monkeypatch.setattr(m, "_set_suspended", _anoop)
    import aria_service.intel.redis_store as rss
    monkeypatch.setattr(rss, "set_json", _anoop)
    monkeypatch.setattr(rss, "get_json", _areturn([]))
    import aria_service.intel.brain_hook as bh
    monkeypatch.setattr(bh, "absorb", _anoop)

    res = await m.run_daily_ping()
    assert res["up"] == 3 and res["down"] == 0     # all reachable
    assert res["blocked"] == 3                     # surfaced distinctly
    assert res["suspended_now"] == []              # WAF block never suspends a live source


# ── R-F2267 self_healing calls the REAL function name and maps fields ─────────
async def test_rf2267_self_healing_source_tile_no_longer_dark(monkeypatch):
    import aria_service.intel.self_healing as sh

    # The bug: the tile called get_status() which never existed on the monitor.
    assert not hasattr(m, "get_status"), "get_status must not exist — health() is the API"

    monkeypatch.setattr(m, "health", _areturn({
        "last_run": {"sources_checked": 200, "up": 169, "down": 31, "blocked": 38},
        "currently_suspended": ["x", "y"], "suspended_count": 2,
    }))

    # Drive the REAL diagnostic method (its step 8 is the broken branch) via the
    # orchestrator's own SelfDiagnostic instance.
    diag = sh.get_orchestrator().diagnostic
    results = await diag.run_full_diagnostic()

    tile = (results.get("subsystems") or {}).get("sources")
    assert tile is not None
    assert tile["status"] == "ok", f"source tile still dark: {tile}"   # was 'error' pre-fix
    assert tile["total"] == 200 and tile["healthy"] == 169 and tile["failed"] == 31
    assert tile["blocked"] == 38 and tile["suspended"] == 2


def _areturn(val):
    async def _f(*a, **k):
        return val
    return _f


async def _anoop(*a, **k):
    return None


async def test_rf2659_health_flags_stale_last_run(monkeypatch):
    import aria_service.intel.redis_store as rss

    monkeypatch.setattr(rss, "get_json", _areturn({
        "ran_at": "2026-07-07T07:43:48+00:00",
        "sources_checked": 200,
        "sources": [],
    }))
    monkeypatch.setattr(m, "_get_suspended", _areturn(set()))
    monkeypatch.setattr(m.time, "time", lambda: 1784180000.0)

    out = await m.health()
    assert out["stale"] is True
    assert out["freshness"]["reason"] == "stale_last_run"
    assert out["last_run_age_seconds"] > out["stale_after_seconds"]
