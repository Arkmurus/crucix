"""R-F4032 (C-100) — the security audit must not hold the GIL for seconds.

WHY THIS TEST EXISTS. `run_security_audit` joined EVERY fact into one string
and ran ~9 regexes plus up to four full `.lower()` copies over it. R-F749 had
already moved that body to a worker via `asyncio.to_thread` after a captured
7.20s stall — but `asyncio.to_thread` does NOT protect the event loop from
CPU-bound work: Python's `re` and `str.lower` hold the GIL, so the loop thread
still cannot be scheduled. That is R-F3252's "thread/GIL starvation with an
idle loop", and it is what the live dumps show: `_run_security_audit_sync`
appears in 31% of the 51 starved wedge dumps (2026-08-16), each with the main
thread parked in uvloop's C loop with nothing blocking it.

It is also self-worsening. R-F749 sized this at "~56k facts, ~50MB+ concatenated
text"; the corpus is now ~533k facts / ~410MB — ~10x — and §7 forbids eviction,
so it only grows. Every O(whole-corpus) step under an infinite-memory policy is
this same bug.

The fix scans in bounded batches and releases the GIL between them. These tests
pin the two things that must BOTH hold: the loop stays responsive, and the audit
does not get less sensitive — a security check that is fast because it stopped
looking is worse than a slow one.
"""
from __future__ import annotations

import asyncio
import time

import pytest

from aria_service.intel import security_protocol as sp


def _corpus(n: int, filler: str = "benign operational fact about a company ") -> list[dict]:
    """A corpus big enough that an unbatched scan visibly stalls the loop."""
    return [{"content": f"{filler}{i} " + ("x" * 160)} for i in range(n)]


@pytest.mark.asyncio
async def test_audit_does_not_starve_the_event_loop(monkeypatch):
    """The loop must keep turning while the audit scans a large corpus."""
    from aria_service.intel import knowledge

    facts = _corpus(120_000)

    async def _fake_get_all_facts():
        return facts

    monkeypatch.setattr(knowledge, "get_all_facts", _fake_get_all_facts)

    worst = {"gap": 0.0}

    async def _ticker():
        last = time.monotonic()
        while True:
            await asyncio.sleep(0.005)
            now = time.monotonic()
            worst["gap"] = max(worst["gap"], now - last)
            last = now

    tick = asyncio.create_task(_ticker())
    await asyncio.sleep(0.05)          # let the ticker establish a baseline

    result = await sp.run_security_audit()

    tick.cancel()
    try:
        await tick
    except asyncio.CancelledError:
        pass

    assert isinstance(result, dict) and "issues_found" in result

    # The real contract: no multi-hundred-ms freeze. Unbatched, this corpus
    # holds the GIL for well over a second.
    # Margin is deliberately wide: measured 0.137s after the fix vs 13.390s
    # before. A tight threshold here would just add another flaky loop-latency
    # test to the suite (CLAUDE.md §16 already records two).
    assert worst["gap"] < 0.5, (
        f"event loop starved {worst['gap']:.3f}s during the security audit — "
        f"the scan is holding the GIL. asyncio.to_thread does not fix this; "
        f"the scan must yield between bounded batches."
    )


@pytest.mark.asyncio
async def test_audit_still_detects_planted_secrets(monkeypatch):
    """Sensitivity must survive the batching — this is a security check."""
    from aria_service.intel import knowledge

    facts = _corpus(5_000)
    facts.append({"content": "sk-" + "a" * 32})                       # CHECK 1
    facts.append({"content": "leaked path /app/aria_service/main.py"})  # CHECK 2

    async def _fake_get_all_facts():
        return facts

    monkeypatch.setattr(knowledge, "get_all_facts", _fake_get_all_facts)

    result = await sp.run_security_audit()

    blob = " ".join(result.get("critical", []) + result.get("warning", []))
    assert "CHECK 1 FAIL" in blob, f"planted API key was NOT detected: {blob}"
    assert "CHECK 2 FAIL" in blob, f"planted internal path was NOT detected: {blob}"


@pytest.mark.asyncio
async def test_audit_detects_a_secret_split_across_a_batch_boundary(monkeypatch):
    """Batching must not open a seam a secret can hide in.

    The pre-fix code joined the whole corpus, so a pattern spanning two adjacent
    facts could match. Naive batching would silently lose exactly those matches
    — a sensitivity regression that no other test here would catch.
    """
    from aria_service.intel import knowledge

    batch = sp._AUDIT_BATCH_FACTS
    facts = _corpus(batch * 2)
    # Straddle the seam: last fact of batch 1 and first of batch 2.
    facts[batch - 1] = {"content": "prefix sk-aaaaaaaaaaaaaaaa"}
    facts[batch] = {"content": "aaaaaaaaaaaaaaaaaaaa suffix"}

    async def _fake_get_all_facts():
        return facts

    monkeypatch.setattr(knowledge, "get_all_facts", _fake_get_all_facts)

    result = await sp.run_security_audit()
    blob = " ".join(result.get("critical", []) + result.get("warning", []))
    assert "CHECK 1 FAIL" in blob, (
        "a key straddling a batch boundary was missed — the overlap carry is broken"
    )


def test_scan_yields_between_batches(monkeypatch):
    """Pin the mechanism: the scan must actually release the GIL, not just batch.

    Batching alone is not the fix. Without a yield, one thread can still run
    every batch back-to-back and starve the loop just as effectively.
    """
    yields = {"n": 0}
    real_sleep = time.sleep

    def _counting_sleep(s):
        if s == 0:
            yields["n"] += 1
        return real_sleep(s)

    monkeypatch.setattr(sp.time, "sleep", _counting_sleep)

    facts = _corpus(sp._AUDIT_BATCH_FACTS * 3)
    sp._run_security_audit_sync(facts, "2026-08-16T00:00:00Z")

    assert yields["n"] >= 2, (
        f"scan yielded the GIL only {yields['n']}x over 3 batches — a batched "
        f"scan that never yields starves the loop exactly as the unbatched one did"
    )
