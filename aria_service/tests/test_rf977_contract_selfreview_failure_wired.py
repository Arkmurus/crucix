"""R-F977 — contract self-review's "all windows failed" branch is wired.

self_review_contract's success path absorbs its findings to the brain, but the
early return when every window produced no output returned silently — a dark
failure branch (CLAUDE.md §21a). Now it mirrors the success path with
brain_hook.absorb(success=False) so ARIA learns when self-review collapses
entirely (e.g. every LLM window erroring out under provider cooldown).
"""
from __future__ import annotations

import asyncio

import pytest


class _EmptyLLM:
    """Configured LLM whose every window returns empty text → no findings
    appended → the all-windows-empty branch fires."""
    is_configured = True

    async def complete(self, system_prompt, prompt, max_tokens=2000, timeout=60.0):
        class _R:
            text = ""        # empty → nothing appended to all_findings
            model = "test-model"
        return _R()


def test_rf977_all_windows_empty_absorbs_failure(monkeypatch):
    from aria_service.intel import contract_intelligence as ci
    from aria_service.intel import brain_hook as bh

    absorbed: list[dict] = []

    async def fake_absorb(**kwargs):
        absorbed.append(kwargs)
        return {}

    monkeypatch.setattr(bh, "absorb", fake_absorb)
    monkeypatch.setenv("ARIA_CONTRACT_INTELLIGENCE", "1")

    # A document large enough to chunk, so the loop runs but appends nothing.
    doc = "Clause text. " * 1500
    out = asyncio.run(ci.self_review_contract(doc, "draft review text", _EmptyLLM()))

    assert out["self_reviewed"] is False
    assert out["reason"] == "all windows failed"
    assert any(
        c.get("module") == "contract_intelligence" and c.get("success") is False
        for c in absorbed
    ), f"all-windows-failed must absorb success=False, got {absorbed!r}"
    # The failure branch should also flag a capability gap via absorb's bridge.
    assert any(c.get("gap_type") for c in absorbed), (
        f"failure absorb should carry a gap_type, got {absorbed!r}"
    )


def test_rf977_success_path_still_absorbs_success(monkeypatch):
    """Guard against over-correction: a normal review must still absorb
    success-side (no regression to the existing success wiring)."""
    from aria_service.intel import contract_intelligence as ci
    from aria_service.intel import brain_hook as bh

    absorbed: list[dict] = []

    async def fake_absorb(**kwargs):
        absorbed.append(kwargs)
        return {}

    class _OkLLM:
        is_configured = True

        async def complete(self, system_prompt, prompt, max_tokens=2000, timeout=60.0):
            class _R:
                text = "No issues in this window."
                model = "test-model"
            return _R()

    monkeypatch.setattr(bh, "absorb", fake_absorb)
    monkeypatch.setenv("ARIA_CONTRACT_INTELLIGENCE", "1")

    doc = "Clause text. " * 1500
    out = asyncio.run(ci.self_review_contract(doc, "draft", _OkLLM()))

    assert out["self_reviewed"] is True
    assert any(
        c.get("module") == "contract_intelligence" for c in absorbed
    ), "success path must still absorb"
