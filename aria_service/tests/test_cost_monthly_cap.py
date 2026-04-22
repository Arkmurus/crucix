"""Monthly LLM cost cap — unit tests.

Covers:
  1. `record_call` updates the monthly rollup (total, per-provider,
     per-feature, per-model, top-calls).
  2. `assert_monthly_cap` raises MonthlyCostCapExceeded when month-to-date
     spend crosses ARIA_MONTHLY_CAP_USD.
  3. ARIA_MONTHLY_CAP_WARN_ONLY=1 downgrades the hard stop to a log line.
  4. `get_month_breakdown` surfaces everything the /cost/monthly endpoint
     needs — this is the diagnostic the operator relies on.

All tests use the redis_store in-memory fallback, so no Redis required.
Sync-wrapper style (asyncio.run) to match repo convention.
"""
from __future__ import annotations

import asyncio

import pytest

from aria_service.intel import cost_tracker
from aria_service.intel import redis_store as rs


def _run(coro):
    return asyncio.run(coro)


@pytest.fixture(autouse=True)
def _reset_state(monkeypatch):
    """Wipe the in-memory redis + module caches before each test so the
    monthly rollup starts from zero and env overrides take effect."""
    rs._mem_store.clear()
    cost_tracker._month_cache.update({"month": "", "total": 0.0, "loaded_at": 0.0})
    cost_tracker._warned_thresholds.clear()
    # Default the cap low so tests hit the boundary quickly.
    monkeypatch.setenv("ARIA_MONTHLY_CAP_USD", "1.00")
    monkeypatch.delenv("ARIA_MONTHLY_CAP_WARN_ONLY", raising=False)
    yield
    rs._mem_store.clear()


def test_record_call_updates_monthly_rollup():
    _run(cost_tracker.record_call(
        model="claude-sonnet-4-6",
        input_tokens=1_000_000,
        output_tokens=100_000,
        provider_name="anthropic",
        feature_name="chat",
    ))
    bd = _run(cost_tracker.get_month_breakdown())
    # Sonnet: $3/MTok in + $15/MTok out → $3 + $1.50 = $4.50
    assert bd["total_cost_usd"] == pytest.approx(4.5, rel=1e-3)
    assert bd["total_calls"] == 1
    assert "anthropic" in bd["by_provider"]
    assert "chat" in bd["by_feature"]
    assert "claude-sonnet-4-6" in bd["by_model"]
    assert bd["by_provider"]["anthropic"]["cost_usd"] == pytest.approx(4.5, rel=1e-3)
    assert len(bd["top_calls"]) == 1


def test_assert_monthly_cap_blocks_once_over(monkeypatch):
    monkeypatch.setenv("ARIA_MONTHLY_CAP_USD", "0.50")
    # Spend ~$4.50 — well over the $0.50 cap.
    _run(cost_tracker.record_call(
        model="claude-sonnet-4-6",
        input_tokens=1_000_000,
        output_tokens=100_000,
        provider_name="anthropic",
        feature_name="chat",
    ))
    with pytest.raises(cost_tracker.MonthlyCostCapExceeded) as exc:
        _run(cost_tracker.assert_monthly_cap())
    assert exc.value.cap == pytest.approx(0.50)
    assert exc.value.spent > 0.50


def test_assert_monthly_cap_allows_under(monkeypatch):
    monkeypatch.setenv("ARIA_MONTHLY_CAP_USD", "100.00")
    _run(cost_tracker.record_call(
        model="deepseek-chat",
        input_tokens=10_000,
        output_tokens=5_000,
        provider_name="deepseek",
        feature_name="chat",
    ))
    # Should not raise — well under $100.
    _run(cost_tracker.assert_monthly_cap())


def test_warn_only_mode_does_not_raise(monkeypatch):
    monkeypatch.setenv("ARIA_MONTHLY_CAP_USD", "0.01")
    monkeypatch.setenv("ARIA_MONTHLY_CAP_WARN_ONLY", "1")
    _run(cost_tracker.record_call(
        model="claude-sonnet-4-6",
        input_tokens=1_000_000,
        output_tokens=100_000,
        provider_name="anthropic",
        feature_name="chat",
    ))
    # Cap blown many times over — but warn-only mode lets it pass.
    _run(cost_tracker.assert_monthly_cap())


def test_get_month_spend_reflects_cap_utilisation(monkeypatch):
    monkeypatch.setenv("ARIA_MONTHLY_CAP_USD", "10.00")
    _run(cost_tracker.record_call(
        model="claude-sonnet-4-6",
        input_tokens=1_000_000,
        output_tokens=100_000,
        provider_name="anthropic",
        feature_name="chat",
    ))
    status = _run(cost_tracker.get_month_spend())
    assert status["cap_usd"] == pytest.approx(10.0)
    assert status["spent_usd"] == pytest.approx(4.5, rel=1e-3)
    assert status["utilisation_pct"] == pytest.approx(45.0, rel=1e-2)
    assert status["remaining_usd"] == pytest.approx(5.5, rel=1e-3)


def test_breakdown_falls_back_to_index_when_rollup_empty():
    """Regression guard for the 2026-04-22 fix: dashboards were showing
    'NO DATA' even though the 90-day index held $30 of historical calls,
    because the monthly rollup key hadn't been populated yet. The breakdown
    must reconstruct from the index when the rollup is missing."""
    import json as _json
    import time as _time
    # Seed the 90-day index directly, bypassing record_call so the monthly
    # rollup stays empty — this mirrors the state on a fresh deploy.
    now_ts = _time.time()
    rs._mem_store[cost_tracker.COST_INDEX_KEY] = _json.dumps([
        {
            "id": "call_abc",
            "ts": now_ts,
            "model": "claude-sonnet-4-6",
            "feature": "chat",
            "total_tokens": 1000,
            "cost_usd": 5.0,
            "success": True,
        },
        {
            "id": "call_def",
            "ts": now_ts,
            "model": "deepseek-chat",
            "feature": "research_task",
            "total_tokens": 2000,
            "cost_usd": 2.0,
            "success": True,
        },
    ])
    bd = _run(cost_tracker.get_month_breakdown())
    assert bd.get("_source") == "index_fallback"
    assert bd["total_cost_usd"] == pytest.approx(7.0)
    assert bd["total_calls"] == 2
    # Provider derived from model name when the index record lacks one.
    assert "anthropic" in bd["by_provider"]
    assert "deepseek" in bd["by_provider"]


def test_breakdown_segments_by_provider_feature_model():
    # Two providers, two features, two models — every bucket should show up.
    _run(cost_tracker.record_call(
        model="claude-sonnet-4-6", input_tokens=1000, output_tokens=500,
        provider_name="anthropic", feature_name="chat",
    ))
    _run(cost_tracker.record_call(
        model="deepseek-chat", input_tokens=5000, output_tokens=2000,
        provider_name="deepseek", feature_name="research_task",
    ))
    bd = _run(cost_tracker.get_month_breakdown())
    assert set(bd["by_provider"].keys()) == {"anthropic", "deepseek"}
    assert set(bd["by_feature"].keys()) == {"chat", "research_task"}
    assert set(bd["by_model"].keys()) == {"claude-sonnet-4-6", "deepseek-chat"}
    assert bd["total_calls"] == 2
