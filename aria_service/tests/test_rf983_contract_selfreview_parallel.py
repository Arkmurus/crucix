"""R-F983 — contract self-review audits its windows CONCURRENTLY.

Pre-R-F983 self_review_contract looped `await llm.complete` once per ~11k-char
window SERIALLY — a 5-window review made 5 serial DeepSeek round-trips, the bulk
of an observed 143s doc-review chat trace. The windows are independent audits of
disjoint slices, so they now run under a bounded asyncio.gather; results are
reassembled in window order so the merged findings read identically.
"""
from __future__ import annotations

import asyncio
import re


class _ConcurrencyLLM:
    """Records max concurrent in-flight completions to prove parallelism."""
    is_configured = True

    def __init__(self):
        self.inflight = 0
        self.max_inflight = 0
        self.calls = 0

    async def complete(self, system, prompt, max_tokens=2000, timeout=60.0):
        self.inflight += 1
        self.calls += 1
        self.max_inflight = max(self.max_inflight, self.inflight)
        await asyncio.sleep(0.05)          # hold the slot so overlap is observable
        self.inflight -= 1
        n = (re.search(r"window (\d+)/", prompt) or [None, "?"])[1]

        class _R:
            text = f"Window {n}: No issues in this window."
            model = "test-model"
        return _R()


def test_rf983_windows_run_concurrently_and_in_order(monkeypatch):
    from aria_service.intel import contract_intelligence as ci
    monkeypatch.setenv("ARIA_CONTRACT_INTELLIGENCE", "1")
    monkeypatch.setenv("ARIA_SELF_REVIEW_CONCURRENCY", "3")

    # A document long enough to chunk into several windows.
    doc = "Clause text here. " * 5000          # ~90k chars → multiple windows
    llm = _ConcurrencyLLM()
    out = asyncio.run(ci.self_review_contract(doc, "draft review", llm))

    assert out["self_reviewed"] is True
    assert out["windows"] >= 3, f"expected a multi-window review, got {out['windows']}"
    assert llm.calls == out["windows"], "one completion per window"
    # The headline assertion: windows overlapped (serial would be max_inflight==1).
    assert llm.max_inflight >= 2, (
        f"self-review windows did not run concurrently (max_inflight={llm.max_inflight})"
    )

    # Findings must still be assembled in ascending window order.
    nums = [int(m) for m in re.findall(r"Self-review window (\d+)/", out["findings"])]
    assert nums == sorted(nums), f"window findings out of order: {nums}"


def test_rf983_concurrency_one_is_still_serial_and_correct(monkeypatch):
    """Guard: concurrency=1 (env override) still works and preserves order —
    proves the refactor degrades gracefully and the env knob is honored."""
    from aria_service.intel import contract_intelligence as ci
    monkeypatch.setenv("ARIA_CONTRACT_INTELLIGENCE", "1")
    monkeypatch.setenv("ARIA_SELF_REVIEW_CONCURRENCY", "1")

    doc = "Clause text here. " * 5000
    llm = _ConcurrencyLLM()
    out = asyncio.run(ci.self_review_contract(doc, "draft", llm))

    assert out["self_reviewed"] is True
    assert llm.max_inflight == 1, "concurrency=1 must serialize"
    nums = [int(m) for m in re.findall(r"Self-review window (\d+)/", out["findings"])]
    assert nums == sorted(nums)
