"""R-F2589 — type/API drift: the live Prometheus monthly-cost gauge + the dead
strategic_evolution engine's rotted bindings.

LIVE bug: metrics.generate_metrics() called a NON-EXISTENT sync
cost_tracker.get_monthly_cost() inside a try/except, so aria_llm_monthly_cost_usd
silently never emitted. Root cause: the real fn (get_month_breakdown) is ASYNC
but generate_metrics is sync. Fix: the async /metrics route awaits the breakdown
and passes it in; generate_metrics uses the real key (total_cost_usd).

These invoke the actual paths (generate_metrics + the metrics_ep route). The
gauge test is §23-discriminating: pre-R-F2589 generate_metrics took no
monthly_cost param, so the call errors on old code.
"""
from __future__ import annotations

import asyncio

from aria_service.intel import metrics as M


def test_generate_metrics_emits_monthly_cost_gauge():
    out = M.generate_metrics(monthly_cost={"total_cost_usd": 12.5})
    assert "aria_llm_monthly_cost_usd 12.5" in out
    assert "# TYPE aria_llm_monthly_cost_usd gauge" in out


def test_generate_metrics_omits_gauge_when_absent():
    # None (fetch failed / not provided) → the gauge is simply absent, never a crash.
    out = M.generate_metrics(monthly_cost=None)
    assert "aria_llm_monthly_cost_usd" not in out
    # base metrics still render
    assert "aria_requests_total" in out


def test_metrics_route_awaits_breakdown_and_emits(monkeypatch):
    # Capability: the actual /metrics route fetches the (async) breakdown and the
    # gauge lands in the scrape body.
    from aria_service.routes import aria as A
    from aria_service.intel import cost_tracker as CT

    async def _breakdown(*a, **k):
        return {"month": "2026-07", "total_cost_usd": 42.0, "total_calls": 3}
    monkeypatch.setattr(CT, "get_month_breakdown", _breakdown)

    resp = asyncio.run(A.metrics_ep())
    body = resp.body.decode() if isinstance(resp.body, (bytes, bytearray)) else str(resp.body)
    assert "aria_llm_monthly_cost_usd 42.0" in body


def test_metrics_route_soft_fails_on_cost_error(monkeypatch):
    # The scrape path must never 500 if the cost read fails — gauge omitted.
    from aria_service.routes import aria as A
    from aria_service.intel import cost_tracker as CT

    async def _boom(*a, **k):
        raise RuntimeError("cost store down")
    monkeypatch.setattr(CT, "get_month_breakdown", _boom)

    resp = asyncio.run(A.metrics_ep())
    body = resp.body.decode() if isinstance(resp.body, (bytes, bytearray)) else str(resp.body)
    assert "aria_requests_total" in body                       # still served
    assert "aria_llm_monthly_cost_usd" not in body             # gauge cleanly omitted


def test_strategic_evolution_binds_real_symbols():
    # Re-drift guard: strategic_evolution now depends on symbols that MUST exist.
    from aria_service.intel import cost_tracker, self_healing
    from aria_service.llm import tier_router
    assert hasattr(cost_tracker, "get_month_breakdown")
    assert hasattr(self_healing, "get_status")
    assert hasattr(tier_router, "_TIER_TO_PROVIDER")
    # and the removed/invented symbols are confirmed absent (drift is gone)
    assert not hasattr(cost_tracker, "get_monthly_cost")
    assert not hasattr(tier_router, "AVAILABLE_PROVIDERS")
    assert not hasattr(self_healing, "get_health_summary")


if __name__ == "__main__":
    import sys
    import pytest
    sys.exit(pytest.main([__file__, "-v"]))
