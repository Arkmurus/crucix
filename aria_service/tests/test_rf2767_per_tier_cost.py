"""R-F2767 — per-subscription-tier Claude cost attribution.

The global + per-user caps bound spend, but the operator could not see cost PER
TIER (free vs Essentials vs Pro Intel) — the brain never received the caller's
tier. This adds a set_tier() contextvar (mirroring set_user), stamps `tier` on
every cost record, and buckets it `by_tier` in the month rollup + breakdown, so
`GET /cost/monthly` surfaces Claude spend per tier for precise margin monitoring.

Drives the REAL attribution path (the contextvar + the pure rollup merge that
record_call and the coalesced flush both use).
"""
from __future__ import annotations

from aria_service.intel import cost_tracker as ct


def test_set_tier_contextvar_roundtrip():
    tok = ct.set_tier("proIntel")
    try:
        assert ct.get_current_tier() == "proIntel"
    finally:
        ct._current_tier.reset(tok)
    assert ct.get_current_tier() == ""  # default when unset


def test_new_rollup_has_by_tier_bucket():
    r = ct._new_rollup("2026-07", 1234.0)
    assert r.get("by_tier") == {}


def test_merge_buckets_cost_by_tier():
    roll = ct._new_rollup("2026-07", 1000.0)
    for rec in (
        {"id": "c1", "ts": 1001.0, "provider": "anthropic", "feature": "dd",
         "model": "claude-sonnet-5", "tier": "free", "total_tokens": 1000, "cost_usd": 0.44},
        {"id": "c2", "ts": 1002.0, "provider": "anthropic", "feature": "chat",
         "model": "claude-haiku-4-5", "tier": "proIntel", "total_tokens": 500, "cost_usd": 0.01},
        {"id": "c3", "ts": 1003.0, "provider": "anthropic", "feature": "dd",
         "model": "claude-sonnet-5", "tier": "free", "total_tokens": 2000, "cost_usd": 0.30},
    ):
        ct._merge_record_into_rollup(roll, rec)
    assert set(roll["by_tier"].keys()) == {"free", "proIntel"}
    assert roll["by_tier"]["free"]["calls"] == 2
    assert abs(roll["by_tier"]["free"]["cost_usd"] - 0.74) < 1e-6
    assert roll["by_tier"]["proIntel"]["calls"] == 1
    assert abs(roll["by_tier"]["proIntel"]["cost_usd"] - 0.01) < 1e-6


def test_record_without_tier_is_unattributed_not_dropped():
    roll = ct._new_rollup("2026-07", 1000.0)
    ct._merge_record_into_rollup(roll, {
        "id": "c4", "ts": 1001.0, "provider": "deepseek", "feature": "x",
        "model": "deepseek-chat", "total_tokens": 100, "cost_usd": 0.001,  # no 'tier'
    })
    assert "unattributed" in roll["by_tier"]
    assert roll["by_tier"]["unattributed"]["calls"] == 1
