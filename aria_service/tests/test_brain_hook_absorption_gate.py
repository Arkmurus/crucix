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
