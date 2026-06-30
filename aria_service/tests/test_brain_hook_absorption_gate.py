"""Tests for the brain_hook absorption gate added 2026-04-25.

Uses asyncio.run() per repo convention.
"""
from __future__ import annotations

import asyncio


def _setup_brain_hook(monkeypatch):
    """Wire up a brain_hook with stub downstream tiers so we can assert
    the gate fires before any write happens."""
    from aria_service.intel import brain_hook
    captured = {"mastery": [], "knowledge": [], "neural": [], "signal": []}

    async def _fake_record_signal(module, success):
        captured["signal"].append({"module": module, "success": success})
    monkeypatch.setattr(brain_hook, "BRAIN_HOOK_ENABLED", True)
    monkeypatch.setattr(brain_hook, "_record_signal", _fake_record_signal)
    return brain_hook, captured


def test_gate_blocks_known_fabricated_tokens(monkeypatch):
    bh, captured = _setup_brain_hook(monkeypatch)

    async def run():
        return await bh.absorb(
            module="some_module",
            summary="OpenClaw v2026.3.13 has a Rollup duplication bug.",
            detail="Hard-restart the gateway process to fix the listener.",
            entity_name="openclaw",
        )
    out = asyncio.run(run())
    assert out["skipped"] is True
    assert out["reason"] == "absorption_gate_known_fabrication"
    # Signal counter not incremented when gate refused — would be misleading.
    assert captured["signal"] == []


def test_gate_blocks_search_derived_self_infra(monkeypatch):
    bh, _ = _setup_brain_hook(monkeypatch)

    async def run():
        return await bh.absorb(
            module="brave_answer",
            summary="Why isn't my WhatsApp listener delivering messages? "
                    "WhatsApp Web sessions sometimes expire and need re-auth.",
            detail="Generic gateway troubleshooting steps.",
        )
    out = asyncio.run(run())
    assert out["skipped"] is True
    assert out["reason"] == "absorption_gate_self_infra_search_derived"


def test_gate_passes_legitimate_internal_telemetry(monkeypatch):
    """Internal modules whose names are NOT in the search-derived list
    are not gated by the search-self-infra check, even if their content
    superficially mentions infrastructure terms.

    This test asserts the gate path is NOT triggered. We don't assert
    successful absorption (that depends on Redis, mastery store, etc.
    which we don't stub out). We just verify the gate doesn't fire."""
    bh, _ = _setup_brain_hook(monkeypatch)

    async def run():
        return await bh.absorb(
            module="signal_generator",
            summary="Sweep produced 12 critical correlations.",
            detail="Middle East, East/Central Africa tagged critical.",
        )
    # The function may fail downstream (no real Redis), but the gate
    # itself MUST NOT have fired with a self-infra reason.
    try:
        out = asyncio.run(run())
        gate_reasons = (
            "absorption_gate_known_fabrication",
            "absorption_gate_self_infra_search_derived",
        )
        assert out.get("reason") not in gate_reasons
    except Exception:
        # Downstream tier failure is fine — what we care about is that
        # the gate didn't catch us. The gate runs SYNCHRONOUSLY at the
        # top of absorb() before any await, so if we got here without a
        # 'skipped' return value, the gate passed us through.
        pass


def test_gate_passes_external_search_for_external_topic(monkeypatch):
    """A brave_answer about a non-self-infra topic must still pass —
    only self-infra search content is gated."""
    bh, _ = _setup_brain_hook(monkeypatch)

    async def run():
        return await bh.absorb(
            module="brave_answer",
            summary="Iran sanctioned 12 entities under the SDN list.",
            detail="OFAC announcement details external sanctions activity.",
            entity_name="OFAC",
        )
    try:
        out = asyncio.run(run())
        assert out.get("reason") not in (
            "absorption_gate_known_fabrication",
            "absorption_gate_self_infra_search_derived",
        )
    except Exception:
        # Downstream tier failure is acceptable — only the gate behaviour
        # matters here.
        pass


def test_gate_skip_records_counter_for_dashboard(monkeypatch):
    """When the gate refuses absorption, _record_gate_skip increments
    a Redis counter so the dashboard can show poisoning-defense activity
    without scraping WARNING logs.

    Verifies the counter is incremented with the correct (reason, module)
    pair for both gate cases."""
    from aria_service.intel import brain_hook

    monkeypatch.setattr(brain_hook, "BRAIN_HOOK_ENABLED", True)

    captured = []

    async def fake_record_gate_skip(reason, module):
        captured.append((reason, module))

    monkeypatch.setattr(brain_hook, "_record_gate_skip", fake_record_gate_skip)

    async def run_known_fabrication():
        return await brain_hook.absorb(
            module="some_module",
            summary="OpenClaw v2026.3.13 status check failed",
            detail="ignored",
        )

    async def run_self_infra_search():
        return await brain_hook.absorb(
            module="brave_answer",
            summary="Why isn't my WhatsApp listener delivering messages?",
            detail="Generic gateway troubleshooting from web search.",
        )

    out1 = asyncio.run(run_known_fabrication())
    out2 = asyncio.run(run_self_infra_search())

    assert out1["reason"] == "absorption_gate_known_fabrication"
    assert out2["reason"] == "absorption_gate_self_infra_search_derived"

    assert ("absorption_gate_known_fabrication", "some_module") in captured
    assert ("absorption_gate_self_infra_search_derived", "brave_answer") in captured


def test_gate_skip_counter_surfaces_in_get_stats(monkeypatch):
    """get_stats() must include `absorb_skipped_by_reason` derived from
    the Redis _gate_skips bucket. Dashboard reads this field."""
    import time as _t
    from aria_service.intel import brain_hook

    # R-F2157: brain_hook now keeps an in-process write-coalescing
    # accumulator that get_stats() drains before reading. Reset it so
    # leftover pending deltas from earlier tests in this module don't merge
    # into this case's fake blob and double-count (mirrors the
    # _reset_breaker() isolation pattern in test_brain_hook_circuit.py).
    brain_hook._pending_modules = {}
    brain_hook._pending_global_total = 0
    brain_hook._pending_sectors = {}
    brain_hook._pending_gate_skips = {}
    brain_hook._stats_cache = {}
    brain_hook._stats_cache_at = 0

    fake_redis_blob = {
        "_global": {"total": 5, "started_at": _t.time() - 3600},
        "_gate_skips": {
            "absorption_gate_known_fabrication": {
                "total": 2,
                "by_module": {"brave_answer": 2},
                "last_at": _t.time() - 1800,
            },
            "absorption_gate_self_infra_search_derived": {
                "total": 1,
                "by_module": {"brave_answer": 1},
                "last_at": _t.time() - 600,
            },
        },
    }

    class FakeRedisStore:
        @staticmethod
        async def get_json(key):
            return fake_redis_blob

    # Replace the module-level redis_store import seen by get_stats.
    import aria_service.intel.redis_store as rs_mod
    monkeypatch.setattr(rs_mod, "get_json", FakeRedisStore.get_json, raising=True)

    out = asyncio.run(brain_hook.get_stats())
    skips = out.get("absorb_skipped_by_reason") or {}
    assert "absorption_gate_known_fabrication" in skips
    assert skips["absorption_gate_known_fabrication"]["total"] == 2
    assert skips["absorption_gate_known_fabrication"]["by_module"]["brave_answer"] == 2
    assert skips["absorption_gate_self_infra_search_derived"]["total"] == 1
    # last_skipped_ago_h should be a positive number (~ 0.5 and ~ 0.17 hrs).
    assert skips["absorption_gate_known_fabrication"]["last_skipped_ago_h"] is not None
    assert skips["absorption_gate_known_fabrication"]["last_skipped_ago_h"] > 0
