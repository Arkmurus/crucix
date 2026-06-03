"""R-F1294 — Capability test: the autonomous coder does NOT regenerate a fix for a
module that already has a pending staged fix (the source of the 186× churn).

With AUTO_DEPLOY off, fix_gap stages a fix but the gap is never marked fixed, so
gap_detector re-surfaces it every cycle and the coder regenerates — burning LLM
tokens + R-numbers and piling the queue. R-F1294 skips a gap up-front if a fix for
its module is already staged + pending review.
"""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import aria_service.intel.self_improve as si
from aria_service.autonomous import self_coder as sc
from aria_service.autonomous.gap_detector import Gap, GapSeverity, GapType


class _FakeRS:
    def __init__(self, staged):
        self.store = {si.STAGED_KEY: staged}

    async def get_json(self, k):
        return self.store.get(k)

    async def set_json(self, k, v, ex=None):
        self.store[k] = v


# ── the helper ──────────────────────────────────────────────────────────────

def test_helper_matches_module_across_dirs(monkeypatch):
    monkeypatch.setattr(si, "rs", _FakeRS([
        {"file": "aria_service/llm/prompt_budget.py", "status": "staged"},
    ]))
    # matched by stem, regardless of the llm/ directory
    assert asyncio.run(si.has_pending_staged_fix_for_module("prompt_budget")) is True
    assert asyncio.run(si.has_pending_staged_fix_for_module("memory_leak_detector")) is False


def test_helper_ignores_non_pending(monkeypatch):
    monkeypatch.setattr(si, "rs", _FakeRS([
        {"file": "aria_service/intel/x.py", "status": "deployed"},
    ]))
    assert asyncio.run(si.has_pending_staged_fix_for_module("x")) is False


# ── the coder behaviour ──────────────────────────────────────────────────────

def _gap(module="prompt_budget"):
    g = Gap(
        gap_id="g1", gap_type=GapType.MODULE_BUG, severity=GapSeverity.HIGH,
        title="bug", description="d", module=module,
    )
    return g


def _coder_with(gap, fix_gap_mock):
    coder = sc.ARIACoder.__new__(sc.ARIACoder)
    coder.gap_detector = MagicMock()
    coder.gap_detector.scan = AsyncMock(return_value=[gap])
    coder.gap_detector.mark_attempted = AsyncMock()
    coder.gap_detector.mark_fixed = AsyncMock()
    coder.harvester = None
    coder.fix_gap = fix_gap_mock
    return coder


def test_coder_skips_gap_with_pending_fix(monkeypatch):
    gap = _gap("prompt_budget")
    if not gap.auto_fixable:
        import pytest
        pytest.skip("gap not auto_fixable in this config")
    monkeypatch.setattr(si, "has_pending_staged_fix_for_module", AsyncMock(return_value=True))
    fix_gap = AsyncMock()
    coder = _coder_with(gap, fix_gap)
    asyncio.run(coder._one_cycle())
    fix_gap.assert_not_called()
    coder.gap_detector.mark_attempted.assert_not_called()


def test_coder_fixes_gap_with_no_pending(monkeypatch):
    gap = _gap("memory_leak_detector")
    if not gap.auto_fixable:
        import pytest
        pytest.skip("gap not auto_fixable in this config")
    monkeypatch.setattr(si, "has_pending_staged_fix_for_module", AsyncMock(return_value=False))
    fix_gap = AsyncMock(return_value=sc.FixResult(success=False, fix_id="f", gap_id="g1"))
    coder = _coder_with(gap, fix_gap)
    asyncio.run(coder._one_cycle())
    fix_gap.assert_awaited_once()
