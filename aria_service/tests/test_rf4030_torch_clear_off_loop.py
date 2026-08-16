"""R-F4030 (C-99) — the torch cache clear must not run on the event loop.

WHY THIS TEST EXISTS. `memory_leak_detector.run_forever` cleared torch's CUDA +
autocast caches immediately before its GC pass. R-F3924 had already moved the
`gc.collect()` OFF the loop for exactly the starvation class this repo keeps
paying for — and left the `import torch` two lines above it ON the loop.

`import torch` is not a dict lookup on the first threshold crossing in a
process: it loads a large C-extension tree and takes seconds. Live evidence
(2026-08-16, aria-intel): an R-F704 wedge stack caught the MAIN thread
mid-import under this exact frame during a measured 5.25s event-loop stall:

    File "/app/aria_service/intel/memory_leak_detector.py", line 307 in run_forever
    File "/usr/local/lib/python3.13/site-packages/torch/__init__.py", line 2821 in <module>
    File "/usr/local/lib/python3.13/site-packages/torch/export/__init__.py", line 42 in <module>

The assertions below are the two halves of the real contract:
  1. the torch work happens on a NON-loop thread, and
  2. the loop stays responsive while it happens (the user-visible outcome —
     a stalled loop is what freezes serving, per R-F2849).

A structural grep for `to_thread` would pass on a fix that threads the wrong
call, so both assertions are behavioural.
"""
from __future__ import annotations

import asyncio
import sys
import threading
import time
import types

import pytest

from aria_service.intel import memory_leak_detector as mld


def _install_fake_torch(monkeypatch, record: dict, block_s: float):
    """A stand-in torch whose cache-clear is observably slow.

    Real torch is absent on win32/ARM64 (CLAUDE.md §16) and its import cost is
    exactly what we are modelling, so a fake is the only honest way to assert
    this on every platform.
    """
    torch = types.ModuleType("torch")

    def _is_available():
        record["thread"] = threading.get_ident()
        time.sleep(block_s)          # models the import + cache-clear cost
        return False

    cuda = types.SimpleNamespace(is_available=_is_available, empty_cache=lambda: None)
    torch.cuda = cuda
    torch._C = types.SimpleNamespace(_clear_autocast_cache=lambda: None)
    monkeypatch.setitem(sys.modules, "torch", torch)
    return torch


@pytest.mark.asyncio
async def test_torch_cache_clear_runs_off_the_event_loop(monkeypatch):
    """The torch clear must not execute on the loop thread, and must not stall it."""
    record: dict = {}
    block_s = 0.6
    _install_fake_torch(monkeypatch, record, block_s)

    # Force the threshold branch on the first pass: any RSS beats a 0 threshold.
    monkeypatch.setattr(mld, "_INTERVAL_S", 0)
    monkeypatch.setattr(mld, "_get_rss_bytes", lambda: 4096)

    detector = mld.MemoryLeakDetector(threshold_mb=0)
    detector._last_gc_at = 0.0

    loop_thread = threading.get_ident()

    # Ticker: samples how long the loop goes between turns while the clear runs.
    worst = {"gap": 0.0}

    async def _ticker():
        last = time.monotonic()
        while True:
            await asyncio.sleep(0.01)
            now = time.monotonic()
            worst["gap"] = max(worst["gap"], now - last)
            last = now

    tick = asyncio.create_task(_ticker())
    task = asyncio.create_task(detector.run_forever())

    # Wait for the torch path to be exercised, bounded so a regression fails fast.
    deadline = time.monotonic() + 15.0
    while "thread" not in record and time.monotonic() < deadline:
        await asyncio.sleep(0.01)

    detector._running = False
    task.cancel()
    tick.cancel()
    for t in (task, tick):
        try:
            await t
        except asyncio.CancelledError:
            pass

    assert "thread" in record, "torch cache-clear path never ran — test drove the wrong branch"

    # (1) It ran, but NOT on the loop thread.
    assert record["thread"] != loop_thread, (
        "torch cache clear ran ON the event-loop thread — this is the R-F4030 "
        "defect: `import torch` blocks the loop for seconds on a cold process"
    )

    # (2) The loop kept turning while it ran — the user-visible outcome.
    assert worst["gap"] < block_s / 2, (
        f"event loop stalled {worst['gap']:.3f}s while the torch cache was "
        f"cleared (blocking work was {block_s}s) — the loop must stay responsive"
    )


@pytest.mark.asyncio
async def test_torch_clear_failure_is_wired_not_swallowed(monkeypatch):
    """§21a — the failure branch must reach the brain, not `except: pass`.

    The clear sat behind a bare `except Exception: pass`, so a torch API change
    would have silently stopped reclaiming memory with nothing observable.
    """
    calls: list = []

    def _fake_wire_failure(**kw):
        calls.append(kw)

    monkeypatch.setattr(mld, "wire_failure", _fake_wire_failure)
    mld._TORCH_CLEAR_FAILURE_WIRED = False        # one-shot guard; reset per test

    torch = types.ModuleType("torch")

    def _boom():
        raise RuntimeError("torch API moved")

    torch.cuda = types.SimpleNamespace(is_available=_boom, empty_cache=lambda: None)
    torch._C = types.SimpleNamespace(_clear_autocast_cache=lambda: None)
    monkeypatch.setitem(sys.modules, "torch", torch)

    # Direct call: this is the unit whose failure branch we are pinning.
    await asyncio.to_thread(mld._clear_torch_caches)

    assert calls, "torch cache-clear failure was swallowed — nothing reached the brain"
    assert calls[0].get("module") == "memory_leak_detector"
    assert "torch" in (calls[0].get("detail") or "").lower()
