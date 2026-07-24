"""R-F3003 — reconcile_month_costs re-prices historical records EXACTLY and corrects
the rollup, without disturbing correctly-priced or externally-priced spend.

Drives the real function against a mocked store: two mispriced deepseek-v4-flash
records (stored at the old Claude-rate cost) + one correctly-priced Claude record.
Asserts the corrected total, the per-record delta, that Claude/other cells are
untouched, and that apply=True backs up before overwriting.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

from aria_service.intel import cost_tracker as ct

_JULY_TS = datetime(2026, 7, 15, 12, 0, 0, tzinfo=timezone.utc).timestamp()


def _records():
    # old (wrong) stored cost for v4-flash @ Claude default (3/15): 20k in + 1k out
    wrong = (3.0 * 20000 + 15.0 * 1000) / 1_000_000  # 0.075
    opus = ct.estimate_cost_usd("claude-opus-4-8", 1000, 500)  # correctly priced already
    return [
        (f"{ct.COST_RECORD_PREFIX}a", {"id": "a", "ts": _JULY_TS, "model": "deepseek-v4-flash",
            "provider": "deepseek", "feature": "uncategorized", "tier": "unattributed",
            "input_tokens": 20000, "output_tokens": 1000, "total_tokens": 21000, "cost_usd": wrong}),
        (f"{ct.COST_RECORD_PREFIX}b", {"id": "b", "ts": _JULY_TS, "model": "deepseek-v4-flash",
            "provider": "deepseek", "feature": "uncategorized", "tier": "unattributed",
            "input_tokens": 20000, "output_tokens": 1000, "total_tokens": 21000, "cost_usd": wrong}),
        (f"{ct.COST_RECORD_PREFIX}c", {"id": "c", "ts": _JULY_TS, "model": "claude-opus-4-8",
            "provider": "anthropic", "feature": "self_improve", "tier": "unattributed",
            "input_tokens": 1000, "output_tokens": 500, "total_tokens": 1500, "cost_usd": opus}),
    ]


def _rollup(recs):
    total = round(sum(r["cost_usd"] for _k, r in recs), 6)
    return {
        "month": "2026-07", "total_cost_usd": total, "total_calls": len(recs),
        "total_tokens": sum(r["total_tokens"] for _k, r in recs),
        "by_provider": {"deepseek": {"calls": 2, "tokens": 42000, "cost_usd": round(recs[0][1]["cost_usd"] * 2, 6)},
                        "anthropic": {"calls": 1, "tokens": 1500, "cost_usd": recs[2][1]["cost_usd"]}},
        "by_model": {"deepseek-v4-flash": {"calls": 2, "tokens": 42000, "cost_usd": round(recs[0][1]["cost_usd"] * 2, 6)},
                     "claude-opus-4-8": {"calls": 1, "tokens": 1500, "cost_usd": recs[2][1]["cost_usd"]}},
        "by_feature": {"uncategorized": {"calls": 2, "tokens": 42000, "cost_usd": round(recs[0][1]["cost_usd"] * 2, 6)},
                       "self_improve": {"calls": 1, "tokens": 1500, "cost_usd": recs[2][1]["cost_usd"]}},
        "by_tier": {"unattributed": {"calls": 3, "tokens": 43500, "cost_usd": total}},
    }


def _run(apply):
    recs = _records()
    rollup = _rollup(recs)
    writes = {}

    async def _set_json(k, v, ex=None):
        writes[k] = v

    with patch.object(ct.rs, "scan_json", AsyncMock(return_value=recs)), \
         patch.object(ct.rs, "get_json", AsyncMock(return_value=rollup)), \
         patch.object(ct.rs, "set_json", AsyncMock(side_effect=_set_json)):
        res = asyncio.run(ct.reconcile_month_costs("2026-07", apply=apply))
    return res, rollup, writes


def test_rf3003_dry_run_computes_exact_correction():
    res, rollup, writes = _run(apply=False)
    right = ct.estimate_cost_usd("deepseek-v4-flash", 20000, 1000)  # ~0.00308
    expected_after = round(rollup["total_cost_usd"] + 2 * (right - 0.075), 6)
    assert res["corrected_records"] == 2, res
    assert res["corrected_models"] == {"deepseek-v4-flash": 2}
    assert res["completeness"] == "exact"
    assert abs(res["after_total_usd"] - expected_after) < 1e-6
    assert res["after_total_usd"] < res["before_total_usd"]  # overcount removed
    assert res["applied"] is False
    assert not writes, "dry-run must not write anything"


def test_rf3003_apply_backs_up_and_corrects_only_mispriced():
    res, rollup, writes = _run(apply=True)
    assert res["applied"] is True
    key = f"{ct.COST_MONTH_PREFIX}2026-07"
    # backup of the ORIGINAL (over-counted) rollup exists
    backup = [k for k in writes if k.startswith(f"{key}:pre_reconcile:")]
    assert backup, "must back up the pre-reconcile rollup"
    assert writes[backup[0]]["total_cost_usd"] == rollup["total_cost_usd"]
    # corrected rollup written to the live key
    after = writes[key]
    right = ct.estimate_cost_usd("deepseek-v4-flash", 20000, 1000)
    assert abs(after["by_model"]["deepseek-v4-flash"]["cost_usd"] - round(2 * right, 6)) < 1e-6
    # Claude cell UNTOUCHED, calls/tokens preserved
    assert after["by_model"]["claude-opus-4-8"]["cost_usd"] == rollup["by_model"]["claude-opus-4-8"]["cost_usd"]
    assert after["by_model"]["deepseek-v4-flash"]["calls"] == 2
    assert after["by_model"]["deepseek-v4-flash"]["tokens"] == 42000
