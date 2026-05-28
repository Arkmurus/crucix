"""R-F560 (2026-05-16) — error-streak counter capability tests.

Phase A exit gate #3 says "0 fly ERRORs in last 7 days". R-F560
computes that consecutive_clean_days streak from the live error
ledger and surfaces it at GET /api/aria/health/error-streak.

These tests use a fake redis_store stub (same pattern as
test_verification_accumulator.py) so they don't need a live ledger.
"""
from __future__ import annotations

import asyncio
import json
import time

import pytest


def _setup_fake_redis(monkeypatch, initial_events: list[dict] | None = None):
    """Stub redis_store.get_json/set_json so the streak computation
    operates against an in-memory ledger."""
    from aria_service.intel import redis_store as rs
    from aria_service.intel import self_improve as si

    store: dict[str, object] = {}
    if initial_events is not None:
        store[si.ERROR_LOG_KEY] = list(initial_events)

    async def fake_get_json(key):
        v = store.get(key)
        # Match real rs.get_json behaviour: return None when missing.
        return v if v is not None else None

    async def fake_set_json(key, obj, ex=None):
        store[key] = obj

    monkeypatch.setattr(rs, "get_json", fake_get_json)
    monkeypatch.setattr(rs, "set_json", fake_set_json)
    return store


def _ev(ts_offset_h: float, level: str = "error", file: str = "x.py") -> dict:
    """Build a fake error-log event N hours back from now."""
    return {
        "type": f"log:{level}",
        "message": f"sample {level}",
        "file": file,
        "function": "fn",
        "timestamp": time.time() - ts_offset_h * 3600,
    }


# ── Streak math ─────────────────────────────────────────────────────────


def test_no_errors_at_all_passes_gate(monkeypatch):
    _setup_fake_redis(monkeypatch, initial_events=[])
    from aria_service.intel import error_streak as es

    r = asyncio.run(es.compute_error_streak())
    assert r["consecutive_clean_days"] == 7
    assert r["phase_a_gate_3_pass"] is True
    assert r["last_error"] is None
    assert r["window_errors_24h"] == 0
    assert r["window_errors_7d"] == 0


def test_recent_error_resets_streak(monkeypatch):
    _setup_fake_redis(monkeypatch, initial_events=[
        _ev(ts_offset_h=2, level="error"),  # 2h ago
    ])
    from aria_service.intel import error_streak as es

    r = asyncio.run(es.compute_error_streak())
    assert r["consecutive_clean_days"] == 0
    assert r["phase_a_gate_3_pass"] is False
    assert r["last_error"] is not None
    assert r["last_error"]["type"] == "log:error"
    assert 1.5 < r["last_error_age_hours"] < 2.5


def test_warnings_dont_reset_streak(monkeypatch):
    """Capability: WARNINGs are in the windowed totals but don't reset
    the ERROR-only streak. Phase A gate #3 is about ERROR, not noise."""
    _setup_fake_redis(monkeypatch, initial_events=[
        _ev(ts_offset_h=1, level="warning"),
        _ev(ts_offset_h=4, level="warning"),
    ])
    from aria_service.intel import error_streak as es

    r = asyncio.run(es.compute_error_streak())
    assert r["consecutive_clean_days"] == 7   # no error → full clean
    assert r["phase_a_gate_3_pass"] is True
    assert r["window_errors_24h"] == 2        # warnings still counted
    assert r["window_errors_7d"] == 2


def test_error_8d_old_still_passes_gate(monkeypatch):
    """Capability: an ERROR older than the 7-day TTL doesn't fail the
    gate. (TTL prunes it anyway, but we test the math.)"""
    _setup_fake_redis(monkeypatch, initial_events=[
        _ev(ts_offset_h=8 * 24, level="error"),  # 8 days ago
    ])
    from aria_service.intel import error_streak as es

    r = asyncio.run(es.compute_error_streak())
    assert r["consecutive_clean_days"] >= 7
    assert r["phase_a_gate_3_pass"] is True


def test_critical_resets_streak(monkeypatch):
    """log:critical must reset just like log:error."""
    _setup_fake_redis(monkeypatch, initial_events=[
        _ev(ts_offset_h=10, level="critical"),
    ])
    from aria_service.intel import error_streak as es

    r = asyncio.run(es.compute_error_streak())
    assert r["consecutive_clean_days"] == 0
    assert r["phase_a_gate_3_pass"] is False
    assert r["last_error"]["type"] == "log:critical"


def test_mixed_events_use_most_recent_error(monkeypatch):
    """Capability: when multiple ERRORs exist, the MOST RECENT one
    drives the streak (oldest don't get used as the anchor)."""
    _setup_fake_redis(monkeypatch, initial_events=[
        _ev(ts_offset_h=72, level="error"),   # 3d ago
        _ev(ts_offset_h=24, level="error"),   # 1d ago
        _ev(ts_offset_h=4, level="warning"),  # 4h ago (noise)
    ])
    from aria_service.intel import error_streak as es

    r = asyncio.run(es.compute_error_streak())
    # Most recent ERROR is 1d ago, so streak should be ~1 day clean
    assert r["consecutive_clean_days"] == 1
    assert 23 < r["last_error_age_hours"] < 25


def test_level_breakdown_aggregates_by_type(monkeypatch):
    _setup_fake_redis(monkeypatch, initial_events=[
        _ev(ts_offset_h=1, level="error"),
        _ev(ts_offset_h=2, level="error"),
        _ev(ts_offset_h=3, level="warning"),
    ])
    from aria_service.intel import error_streak as es

    r = asyncio.run(es.compute_error_streak())
    assert r["level_breakdown_7d"].get("log:error") == 2
    assert r["level_breakdown_7d"].get("log:warning") == 1


def test_explicit_threshold_override(monkeypatch):
    """The 7d threshold is configurable for ops who want a stricter gate."""
    _setup_fake_redis(monkeypatch, initial_events=[
        _ev(ts_offset_h=3 * 24, level="error"),  # 3d ago
    ])
    from aria_service.intel import error_streak as es

    r3 = asyncio.run(es.compute_error_streak(threshold_days=3))
    assert r3["phase_a_gate_3_pass"] is True   # 3d streak meets 3d threshold

    r5 = asyncio.run(es.compute_error_streak(threshold_days=5))
    assert r5["phase_a_gate_3_pass"] is False  # 3d streak < 5d threshold


def test_ledger_fetch_failure_degrades_gracefully(monkeypatch):
    """Capability: a Redis blow-up must not 500 the endpoint — it
    returns honest 'unknown' fields so the dashboard can render."""
    from aria_service.intel import redis_store as rs

    async def boom(_):
        raise RuntimeError("ledger gone")

    monkeypatch.setattr(rs, "get_json", boom)
    from aria_service.intel import error_streak as es

    r = asyncio.run(es.compute_error_streak())
    assert "error" in r
    assert r["phase_a_gate_3_pass"] is False  # honest default


# ── R-F969 — deploy-noise immunity is explicit + real operational WARNINGs ──


def test_rf969_noise_excluded_documented(monkeypatch):
    """R-F969 — the output explicitly documents that fly-platform deploy-window
    errors + operational WARNINGs don't reset the streak, so the gate stops
    being re-litigated each session."""
    _setup_fake_redis(monkeypatch, initial_events=[])
    from aria_service.intel import error_streak as es

    r = asyncio.run(es.compute_error_streak())
    note = r.get("noise_excluded", "")
    assert "deploy-window" in note and "WARNING" in note


def test_rf969_operational_warnings_do_not_reset(monkeypatch):
    """Realistic regression: a redeploy's circuit-breaker source-down +
    R-F703 event-loop-stall WARNINGs (both log:warning) must NOT reset the
    gate-3 streak — they are operational, not app ERROR/CRITICAL."""
    base = time.time()
    events = [
        {"type": "log:warning", "file": "aria_service/intel/circuit_breaker.py",
         "message": "[circuit_breaker] search:duckduckgo: CLOSED -> OPEN (5 consecutive failures)",
         "timestamp": base - 3600},
        {"type": "log:warning", "file": "aria_service/main.py",
         "message": "[R-F703] event loop stalled for 11.64s (threshold=5.0s)",
         "timestamp": base - 1800},
    ]
    _setup_fake_redis(monkeypatch, initial_events=events)
    from aria_service.intel import error_streak as es

    r = asyncio.run(es.compute_error_streak())
    assert r["phase_a_gate_3_pass"] is True
    assert r["consecutive_clean_days"] == 7
    assert r["window_errors_7d"] == 2  # still counted in the window


# ── Endpoint wiring ─────────────────────────────────────────────────────


def test_route_endpoint_is_registered():
    """Capability: /api/aria/health/error-streak is mounted on the router."""
    from aria_service.routes import aria as aria_routes

    paths = []
    for route in aria_routes.router.routes:
        path = getattr(route, "path", "")
        paths.append(path)
    assert any(p.endswith("/health/error-streak") for p in paths), (
        f"R-F560 endpoint missing. Mounted paths: {sorted(paths)[:10]}..."
    )
