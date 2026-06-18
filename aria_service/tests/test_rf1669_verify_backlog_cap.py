"""R-F1669 — the R-F1530 verify path bounds its PENDING-task QUEUE, not just
concurrency. R-F1656's Semaphore(4) capped execution but create_task still fired
per fact, so a fact burst piled up unbounded pending _verify() coroutines → the
wedge recurred. This proves: with verifies stuck (slow search), the in-flight
backlog never exceeds ARIA_VERIFY_MAX_PENDING and excess facts are dropped.
"""
from __future__ import annotations

import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

from aria_service.intel import knowledge as K


@pytest.mark.asyncio
async def test_pending_backlog_is_capped(monkeypatch):
    monkeypatch.setenv("ARIA_VERIFY_MAX_PENDING", "8")
    # reset the function-level counters
    if hasattr(K._auto_verify_fact, "_pending"):
        K._auto_verify_fact._pending = 0
    if hasattr(K._auto_verify_fact, "_dropped"):
        K._auto_verify_fact._dropped = 0

    # make every verify HANG (simulates slow/stuck search) so created tasks
    # stay in-flight and the backlog would grow unbounded without the cap.
    blocker = asyncio.Event()
    async def _hang(*a, **k):
        await blocker.wait()
    monkeypatch.setattr(K, "_do_verify", _hang)

    # fire far more facts than the cap
    for i in range(50):
        K._auto_verify_fact({"source_url": ""}, f"topic{i}", "x" * 60, "src")

    await asyncio.sleep(0.05)  # let the scheduled tasks register
    pending = getattr(K._auto_verify_fact, "_pending", 0)
    dropped = getattr(K._auto_verify_fact, "_dropped", 0)

    assert pending <= 8, f"backlog not bounded: {pending} pending (cap 8)"
    assert dropped >= 40, f"excess facts should be dropped, got {dropped}"
    assert pending + dropped <= 50

    # release the hung verifies and let the backlog drain
    blocker.set()
    await asyncio.sleep(0.05)
    assert getattr(K._auto_verify_fact, "_pending", 0) < pending or pending == 0


@pytest.mark.asyncio
async def test_no_loop_is_safe(monkeypatch):
    # called outside a running loop (defensive) — must not raise.
    # (here we ARE in a loop, so just confirm a normal call doesn't blow up)
    monkeypatch.setenv("ARIA_VERIFY_MAX_PENDING", "64")
    K._auto_verify_fact({"source_url": ""}, "t", "y" * 60, "s")  # no exception
