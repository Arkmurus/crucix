"""R-F795 (2026-05-22): per-tier asyncio.wait_for in brain_hook.absorb().

Live evidence 2026-05-22 15:59-16:04 UTC: brain_hook circuit breaker
p95 hit 1,250,404ms (20m 51s) on absorb(web_atlas). 3 trips in 5 min,
Watchlist re-screen wall-time hit 19m 58s. Root cause: sentence-
transformers model.encode() is GIL-serialised, so concurrent absorbs
queue serially through one encoder.

R-F795 caps each of the 3 expensive tiers (mastery, knowledge, neural)
at _TIER_TIMEOUT_S (default 5.0s, env-tunable via
ARIA_BRAIN_ABSORB_TIER_TIMEOUT_MS). Worst-case absorb wall-time
becomes ~3 × _TIER_TIMEOUT_S instead of unbounded.

Uses asyncio.run() per repo convention (see test_brain_hook_absorption_gate.py).
"""
from __future__ import annotations

import asyncio
import time

from aria_service.intel import brain_hook


def _reset_breaker():
    brain_hook._breaker_state["open"] = False
    brain_hook._breaker_state["tripped_at"] = 0.0
    brain_hook._breaker_state["consecutive_high"] = 0
    brain_hook._breaker_state["ticket_filed_this_episode"] = False
    brain_hook._recent_latencies_ms.clear()


def _patch_tiers(monkeypatch, mastery=None, knowledge=None, neural=None):
    """Stub the three expensive tiers + _record_signal so tests don't
    touch real Redis / chromadb / sentence_transformers."""
    from aria_service.intel import student, knowledge as kn, neural_memory

    async def _default_mastery(*a, **kw):
        return None

    async def _default_knowledge(*a, **kw):
        return {}

    async def _default_neural(*a, **kw):
        return {"neurons_activated": 0, "connections_formed": 0}

    async def _fake_record_signal(module, success=True, sector=""):
        return None

    monkeypatch.setattr(student, "update_mastery", mastery or _default_mastery)
    monkeypatch.setattr(kn, "store_fact", knowledge or _default_knowledge)
    monkeypatch.setattr(neural_memory, "learn_from_text", neural or _default_neural)
    monkeypatch.setattr(brain_hook, "_record_signal", _fake_record_signal)
    monkeypatch.setattr(brain_hook, "BRAIN_HOOK_ENABLED", True)


async def _hang_forever(*a, **kw):
    await asyncio.sleep(60.0)


_LONG_TEXT = (
    "Test summary content that exceeds 50 chars for the neural-tier gate."
)


def test_rf795_mastery_timeout_skips_tier_records_error(monkeypatch):
    """When student.update_mastery hangs past _TIER_TIMEOUT_S, the
    timeout fires, mastery_ok is False, and an error string is recorded.
    Knowledge and neural still run independently."""
    _reset_breaker()
    monkeypatch.setattr(brain_hook, "_TIER_TIMEOUT_S", 0.05)
    _patch_tiers(monkeypatch, mastery=_hang_forever)

    result = asyncio.run(brain_hook.absorb(
        module="dd_orchestrator", summary=_LONG_TEXT, detail=_LONG_TEXT,
    ))

    assert result["mastery_ok"] is False
    assert any("mastery: timeout" in e for e in result["errors"])
    assert result["knowledge_ok"] is True
    assert result["neural_ok"] is True


def test_rf795_knowledge_timeout_skips_tier(monkeypatch):
    _reset_breaker()
    monkeypatch.setattr(brain_hook, "_TIER_TIMEOUT_S", 0.05)
    _patch_tiers(monkeypatch, knowledge=_hang_forever)

    result = asyncio.run(brain_hook.absorb(
        module="dd_orchestrator", summary=_LONG_TEXT, detail=_LONG_TEXT,
    ))

    assert result["mastery_ok"] is True
    assert result["knowledge_ok"] is False
    assert any("knowledge: timeout" in e for e in result["errors"])
    assert result["neural_ok"] is True


def test_rf795_neural_timeout_does_not_break_absorb(monkeypatch):
    """neural_memory.learn_from_text is the most common encode-wedge
    culprit. Timeout there must not block absorb returning."""
    _reset_breaker()
    monkeypatch.setattr(brain_hook, "_TIER_TIMEOUT_S", 0.05)
    _patch_tiers(monkeypatch, neural=_hang_forever)

    result = asyncio.run(brain_hook.absorb(
        module="web_atlas", summary=_LONG_TEXT, detail=_LONG_TEXT,
    ))

    assert result["mastery_ok"] is True
    assert result["knowledge_ok"] is True
    assert result["neural_ok"] is False
    assert any("neural: timeout" in e for e in result["errors"])


def test_rf795_absorb_wall_time_bounded_when_all_tiers_hang(monkeypatch):
    """The critical capability test: when ALL 3 tiers hang, absorb
    returns in ~3 × _TIER_TIMEOUT_S, not forever. Regression guard
    against the 18–20-minute wall-time wedges (2026-05-22 logs)."""
    _reset_breaker()
    monkeypatch.setattr(brain_hook, "_TIER_TIMEOUT_S", 0.05)
    _patch_tiers(
        monkeypatch,
        mastery=_hang_forever, knowledge=_hang_forever, neural=_hang_forever,
    )

    t0 = time.monotonic()
    result = asyncio.run(brain_hook.absorb(
        module="web_atlas", summary=_LONG_TEXT, detail=_LONG_TEXT,
    ))
    elapsed = time.monotonic() - t0

    # 3 × 0.05s = 0.15s ideal. Allow 2.0s slack for event-loop overhead.
    # The point: NOT 60s × 3 = 180s (unbounded behaviour pre-R-F795).
    assert elapsed < 2.0, (
        f"R-F795 regression: absorb wall-time should be bounded at "
        f"~3 × _TIER_TIMEOUT_S, but took {elapsed:.2f}s. The whole point "
        f"of this R-number is bounding wall-time when downstream wedges."
    )
    assert result["mastery_ok"] is False
    assert result["knowledge_ok"] is False
    assert result["neural_ok"] is False


def test_rf795_disabled_timeout_runs_unbounded(monkeypatch):
    """ARIA_BRAIN_ABSORB_TIER_TIMEOUT_MS=0 disables the timeout for
    legitimate slow batches. Sets _TIER_TIMEOUT_S=0 → legacy behaviour."""
    _reset_breaker()
    monkeypatch.setattr(brain_hook, "_TIER_TIMEOUT_S", 0.0)
    _patch_tiers(monkeypatch)  # all default quick stubs

    result = asyncio.run(brain_hook.absorb(
        module="dd_orchestrator", summary=_LONG_TEXT, detail=_LONG_TEXT,
    ))

    assert result["mastery_ok"] is True
    assert result["knowledge_ok"] is True
    assert result["neural_ok"] is True
    assert result["errors"] == []


def test_rf795_non_timeout_exception_still_caught(monkeypatch):
    """Tier raising a non-timeout exception is still caught and tagged
    with the underlying exception text (not "timeout"). Pre-R-F795
    behaviour preserved for non-timeout failures."""
    _reset_breaker()

    async def _raise(*a, **kw):
        raise ValueError("simulated downstream failure")

    _patch_tiers(monkeypatch, mastery=_raise)

    result = asyncio.run(brain_hook.absorb(
        module="dd_orchestrator", summary=_LONG_TEXT, detail=_LONG_TEXT,
    ))

    assert result["mastery_ok"] is False
    assert any("mastery: simulated downstream failure" in e for e in result["errors"])
    assert not any("mastery: timeout" in e for e in result["errors"])
    assert result["knowledge_ok"] is True
    assert result["neural_ok"] is True
