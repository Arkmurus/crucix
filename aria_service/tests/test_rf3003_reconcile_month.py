"""R-F3003/R-F3004 — reconcile_month_costs REBUILDS a month's rollup from the
per-call records, re-pricing LLM calls at current rates.

R-F3004: an earlier delta-adjust approach was replaced after a live dry-run showed
the stored rollup's call count had diverged ~10x below the actual record count,
which made delta-adjust produce a negative total. Rebuilding from records is the
only sound method. This drives the real function against a mocked store: two
mispriced deepseek-v4-flash records + one correctly-priced Claude record, and
asserts the rebuilt total = sum of corrected costs, the divergence is exposed, and
apply=True backs up before overwriting.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

from aria_service.intel import cost_tracker as ct

_JULY_TS = datetime(2026, 7, 15, 12, 0, 0, tzinfo=timezone.utc).timestamp()
_WRONG = (3.0 * 20000 + 15.0 * 1000) / 1_000_000  # old Claude-default cost for v4-flash


def _records():
    opus = ct.estimate_cost_usd("claude-opus-4-8", 1000, 500)
    return [
        (f"{ct.COST_RECORD_PREFIX}a", {"id": "a", "ts": _JULY_TS, "model": "deepseek-v4-flash",
            "provider": "deepseek", "feature": "uncategorized", "tier": "unattributed",
            "input_tokens": 20000, "output_tokens": 1000, "total_tokens": 21000, "cost_usd": _WRONG}),
        (f"{ct.COST_RECORD_PREFIX}b", {"id": "b", "ts": _JULY_TS, "model": "deepseek-v4-flash",
            "provider": "deepseek", "feature": "uncategorized", "tier": "unattributed",
            "input_tokens": 20000, "output_tokens": 1000, "total_tokens": 21000, "cost_usd": _WRONG}),
        (f"{ct.COST_RECORD_PREFIX}c", {"id": "c", "ts": _JULY_TS, "model": "claude-opus-4-8",
            "provider": "anthropic", "feature": "self_improve", "tier": "unattributed",
            "input_tokens": 1000, "output_tokens": 500, "total_tokens": 1500, "cost_usd": opus}),
    ]


# the stored rollup DIVERGED: it only ever counted 1 of the 3 real calls
_STORED_ROLLUP = {"month": "2026-07", "total_cost_usd": _WRONG, "total_calls": 1,
                  "total_tokens": 21000, "by_provider": {}, "by_model": {}, "by_feature": {}, "by_tier": {}}


def _run(apply):
    recs = _records()
    writes = {}

    async def _scan(pattern, count=200):
        return recs if pattern.startswith(ct.COST_RECORD_PREFIX) else []  # external empty

    async def _set_json(k, v, ex=None):
        writes[k] = v

    with patch.object(ct.rs, "scan_json", AsyncMock(side_effect=_scan)), \
         patch.object(ct.rs, "get_json", AsyncMock(return_value=_STORED_ROLLUP)), \
         patch.object(ct.rs, "set_json", AsyncMock(side_effect=_set_json)):
        res = asyncio.run(ct.reconcile_month_costs("2026-07", apply=apply))
    return res, writes


def test_rf3004_rebuild_uses_records_as_ground_truth():
    res, writes = _run(apply=False)
    right = ct.estimate_cost_usd("deepseek-v4-flash", 20000, 1000)
    opus = ct.estimate_cost_usd("claude-opus-4-8", 1000, 500)
    expected_total = round(2 * right + opus, 6)
    # rebuilt from the 3 records — NOT the diverged rollup's 1 call
    assert res["rebuilt_total_calls"] == 3, res
    assert res["old_rollup_total_calls"] == 1, "divergence must be exposed"
    assert abs(res["rebuilt_total_usd"] - expected_total) < 1e-6
    assert res["rebuilt_total_usd"] > 0, "rebuild must never produce a negative/nonsense total"
    assert res["repriced_records"] == 2
    assert res["repriced_models"] == {"deepseek-v4-flash": 2}
    assert res["applied"] is False and not writes


def test_rf3004_apply_backs_up_then_writes_rebuilt():
    res, writes = _run(apply=True)
    assert res["applied"] is True
    key = f"{ct.COST_MONTH_PREFIX}2026-07"
    backup = [k for k in writes if k.startswith(f"{key}:pre_reconcile:")]
    assert backup and writes[backup[0]]["total_cost_usd"] == _WRONG, "must back up the ORIGINAL rollup"
    written = writes[key]
    assert written["total_calls"] == 3
    right = ct.estimate_cost_usd("deepseek-v4-flash", 20000, 1000)
    assert abs(written["by_model"]["deepseek-v4-flash"]["cost_usd"] - round(2 * right, 6)) < 1e-6
    # correctly-priced Claude call preserved exactly
    assert abs(written["by_model"]["claude-opus-4-8"]["cost_usd"]
               - ct.estimate_cost_usd("claude-opus-4-8", 1000, 500)) < 1e-6
