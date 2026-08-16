"""R-F2234 — aria-brain dashboard latency: three root-cause fixes.

Capability tests that drive the REAL broken/changed paths:

  Fix A — GET /api/aria/brain/dashboard collapses the ~24-way client fan-out
          into ONE cached server-side aggregate (per-panel timeout + try/except,
          short-TTL cache). Drives brain_dashboard_ep directly.
  Fix B — learning_stats_ep parallelizes its ~13 subsystem reads under
          asyncio.gather (identical output shape, per-subsystem default on
          failure) + short-TTL cache. Drives learning_stats_ep directly.
  Fix C — cost READ endpoints no longer force a write-flush (force=False), so a
          dashboard cost read stops doing a read-modify-write on 3 hot keys.
          Drives get_month_spend / get_month_breakdown / get_cost_summary.

NB: modules imported under full names (not short aliases) so the R-F1958
pre-commit function-name verifier resolves them correctly.
"""
from __future__ import annotations

import asyncio

import pytest

from aria_service.routes import aria as aria_routes
from aria_service.intel import cost_tracker
from aria_service.intel import redis_store as rs


# The exact path strings the frontend public/aria-brain.html fetchJson() uses
# inside refreshAll(). The server registry must be a subset of these.
_FRONTEND_REFRESH_PATHS = {
    "/health", "/operating-mode", "/autonomous/status", "/adversarial/stats",
    "/circuit-breakers", "/adversarial/amendments", "/dd/layer-5c/stats",
    "/predictor/block_rate", "/chat-audit/stats", "/chat-audit/recent?limit=10",
    "/student/mastery/heatmap", "/autonomous/dlq", "/student/mastery",
    "/autonomy/composite", "/calibration/review", "/autonomy/surface",
    "/learning/stats", "/cost/monthly", "/cost/external", "/diagnostic/details",
    "/learning/coverage", "/learning/coverage/gaps?max_targets=15",
    "/learning/freshness", "/hallucination/stats",
    # R-F4070 (C-111) — the Chat Audit panel now fetches a chain verdict.
    # Served from the aggregate so surfacing it costs no extra fan-out request.
    "/chat-audit/verify?sample=200",
}


@pytest.fixture(autouse=True)
def _clear_caches():
    aria_routes._DASHBOARD_AGG_CACHE.clear()
    aria_routes._LEARNING_STATS_CACHE.clear()
    yield
    aria_routes._DASHBOARD_AGG_CACHE.clear()
    aria_routes._LEARNING_STATS_CACHE.clear()


# ═══════════════════════════ FIX A ═══════════════════════════════════════════

def test_rf2234_aggregate_shape_and_failing_panel(monkeypatch):
    """One failing panel must NOT 500 the aggregate.

    R-F2390: aggregate-side failures are omitted from panels and reported in
    omitted, so the frontend falls back to the real endpoint before declaring
    a user-visible endpoint failure.
    """
    async def _good():
        return {"value": 42}

    async def _boom():
        raise RuntimeError("panel exploded")

    registry = {"/good": _good, "/bad": _boom}
    monkeypatch.setattr(aria_routes, "_dashboard_panel_registry", lambda: registry)

    blob = asyncio.run(aria_routes.brain_dashboard_ep())

    assert isinstance(blob, dict)
    assert blob["ok"] is True
    assert "generated_at" in blob
    assert "panels" in blob
    assert "omitted" in blob
    panels = blob["panels"]
    # keys ⊆ the registry keys
    assert set(panels.keys()) <= set(registry.keys())
    # good panel → real payload
    assert panels["/good"] == {"value": 42}
    # failing panel → omitted, NOT a cached endpoint-failure marker, NOT a 500
    assert "/bad" not in panels
    assert "panel exploded" in blob["omitted"]["/bad"]


def test_rf2234_second_call_within_ttl_is_cached(monkeypatch):
    """Second call within TTL serves the cached blob — the underlying handler
    is NOT re-invoked, and cache_age_s > 0."""
    calls = {"n": 0}

    async def _counting():
        calls["n"] += 1
        return {"n": calls["n"]}

    monkeypatch.setattr(aria_routes, "_dashboard_panel_registry",
                        lambda: {"/counting": _counting})
    # generous TTL so the second call is unambiguously a hit
    monkeypatch.setattr(aria_routes, "_DASHBOARD_AGG_TTL_S", 999.0)

    async def run():
        first = await aria_routes.brain_dashboard_ep()
        await asyncio.sleep(0.02)  # let monotonic advance so cache_age_s is measurable
        second = await aria_routes.brain_dashboard_ep()
        return first, second

    first, second = asyncio.run(run())
    assert calls["n"] == 1, "handler re-invoked on the cached second call"
    assert first["cache_age_s"] == 0.0
    assert second["cache_age_s"] > 0.0
    assert second["panels"] == first["panels"]


def test_rf2234_registry_paths_are_frontend_paths():
    """Every registry key must be a real frontend fetchJson path, and
    /dd/layer-5c/stats must now be COVERED (R-F2587).

    It was previously omitted as an "ambiguous dup-handler path", but R-F2278
    resolved that collision (the digital-footprint variant was renamed to
    `dd_layer_5c_digital_stats_ep`), so `dd_layer_5c_stats_ep` is a unique
    symbol and R-F2587 includes it — removing the last structural fall-through.
    """
    reg = aria_routes._dashboard_panel_registry()
    assert set(reg.keys()) <= _FRONTEND_REFRESH_PATHS, (
        "registry contains a path the frontend never fetches: "
        f"{set(reg.keys()) - _FRONTEND_REFRESH_PATHS}"
    )
    assert "/dd/layer-5c/stats" in reg, "R-F2587: dd/layer-5c/stats must be covered post-R-F2278"
    # sanity: registry should carry the bulk of the panels
    assert len(reg) >= 20


# ═══════════════════════════ FIX B ═══════════════════════════════════════════

_LEARNING_KEYS = {
    "training_export", "knowledge_spider", "metacog_journal", "research_engine",
    "verification_gate", "output_harvester", "quarantine", "bright_lines",
    "sanctions_propagation", "style_learner", "pdf_deep_ingest", "memory_backup",
}


def test_rf2234_learning_stats_key_set_and_default_on_failure(monkeypatch):
    """learning_stats_ep returns the SAME key set as before, and a forced
    subsystem exception degrades that key to its default without raising."""
    from aria_service.learning import knowledge_spider

    async def _boom():
        raise RuntimeError("spider down")

    monkeypatch.setattr(knowledge_spider, "get_stats", _boom)

    out = asyncio.run(aria_routes.learning_stats_ep())
    assert set(out.keys()) == _LEARNING_KEYS, out.keys()
    # the failed subsystem degraded to its default ({}), did NOT raise
    assert out["knowledge_spider"] == {}


def test_rf2234_learning_stats_cached(monkeypatch):
    """Two calls within TTL don't re-invoke a patched subsystem."""
    from aria_service.learning import knowledge_spider

    calls = {"n": 0}

    async def _counting():
        calls["n"] += 1
        return {"seen": calls["n"]}

    monkeypatch.setattr(knowledge_spider, "get_stats", _counting)
    monkeypatch.setattr(aria_routes, "_LEARNING_STATS_TTL_S", 999.0)

    async def run():
        a = await aria_routes.learning_stats_ep()
        b = await aria_routes.learning_stats_ep()
        return a, b

    a, b = asyncio.run(run())
    assert calls["n"] == 1, "subsystem re-invoked on the cached second call"
    assert a["knowledge_spider"] == b["knowledge_spider"] == {"seen": 1}


# ═══════════════════════════ FIX C ═══════════════════════════════════════════

def test_rf2234_cost_reads_do_not_force_flush(monkeypatch):
    """The cost READ endpoints must call _flush_cost_pending with force=False —
    so they piggyback the 15s-gated flush instead of a per-read RMW on 3 hot
    keys through the shared write lock."""
    forces: list[bool] = []

    async def spy_flush(force=False):
        forces.append(force)
        return False

    # keep the store reads cheap + deterministic
    async def _empty_json(*a, **k):
        return {}

    monkeypatch.setattr(cost_tracker, "_flush_cost_pending", spy_flush)
    monkeypatch.setattr(rs, "get_json", _empty_json)

    async def run():
        await cost_tracker.get_month_spend()
        await cost_tracker.get_month_breakdown()
        await cost_tracker.get_cost_summary()
        await cost_tracker.get_cumulative_aggregate()
        await cost_tracker.list_recent_calls()

    asyncio.run(run())
    assert forces == [False, False, False, False, False], forces


def test_rf2234_cap_enforcement_path_untouched(monkeypatch):
    """The $300 cap is enforced by the SEPARATE atomic reserve path
    (assert_monthly_cap → INCRBYFLOAT), which must NOT depend on the flush.
    Prove assert_monthly_cap does not call _flush_cost_pending and still uses
    the atomic reserve."""
    flush_called = {"n": 0}
    incr_called = {"n": 0}

    async def spy_flush(force=False):
        flush_called["n"] += 1
        return False

    async def spy_incr(key, amt):
        incr_called["n"] += 1
        return 0.01  # well under any cap

    async def _noop_expire(*a, **k):
        return None

    monkeypatch.setattr(cost_tracker, "_flush_cost_pending", spy_flush)
    monkeypatch.setattr(rs, "incrbyfloat", spy_incr)
    monkeypatch.setattr(rs, "expire", _noop_expire)

    # a positive cap so the enforcement body runs (default is 300 anyway)
    monkeypatch.setenv("ARIA_MONTHLY_CAP_USD", "300")

    asyncio.run(cost_tracker.assert_monthly_cap(estimated_cost_usd=0.02))

    assert flush_called["n"] == 0, "cap path must not depend on the flush"
    assert incr_called["n"] >= 1, "cap path must use the atomic INCRBYFLOAT reserve"
