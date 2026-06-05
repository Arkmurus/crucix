"""R-F1344 — bounded headless render-lock (Pillar-1 invariant, mirrors R-F1341).

The process-wide Lightpanda render lock had no acquire-timeout and the render
wasn't bounded — one hung render (stalled subprocess/Playwright) would queue
every other render behind it and starve the loop. Now: a waiter that can't
acquire in time falls back (returns "") and a render that exceeds the op cap
aborts + releases. These tests prove a hung render does not starve the loop.
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

from aria_service.intel import headless


@pytest.fixture(autouse=True)
def _fresh(monkeypatch):
    headless._RENDER_LOCK = None
    monkeypatch.setattr(headless, "is_available", lambda: True)
    monkeypatch.setattr(headless, "_RENDER_LOCK_ACQUIRE_S", 0.3)
    monkeypatch.setattr(headless, "_RENDER_OP_MAX_S", 0.3)
    yield
    headless._RENDER_LOCK = None


@pytest.mark.asyncio
async def test_hung_render_does_not_starve_the_loop(monkeypatch):
    async def _hang(url, timeout):
        await asyncio.sleep(30)  # render that never returns
        return "should not reach"
    monkeypatch.setattr(headless, "_fetch_rendered_html_locked", _hang)

    ticks = 0
    async def _heartbeat():
        nonlocal ticks
        for _ in range(15):
            ticks += 1
            await asyncio.sleep(0.05)

    holder = asyncio.create_task(headless.fetch_rendered_html("http://a"))
    waiter = asyncio.create_task(headless.fetch_rendered_html("http://b"))
    hb = asyncio.create_task(_heartbeat())
    holder_r, waiter_r, _ = await asyncio.gather(holder, waiter, hb)

    # Both bail safely (holder aborts at op-cap, waiter at acquire-timeout).
    assert holder_r == ""
    assert waiter_r == ""
    # The loop kept breathing the whole time — no blackout.
    assert ticks == 15


@pytest.mark.asyncio
async def test_acquire_timeout_falls_back(monkeypatch):
    """While one render holds the lock, a second caller gives up fast → ''."""
    async def _hold(url, timeout):
        await asyncio.sleep(30)
    monkeypatch.setattr(headless, "_fetch_rendered_html_locked", _hold)

    holder = asyncio.create_task(headless.fetch_rendered_html("http://a"))
    await asyncio.sleep(0.05)  # let holder take the lock
    t0 = asyncio.get_event_loop().time()
    out = await headless.fetch_rendered_html("http://b")
    elapsed = asyncio.get_event_loop().time() - t0
    assert out == ""
    assert elapsed < 1.0  # fast fallback, not a 30s queue
    holder.cancel()
    try:
        await holder
    except asyncio.CancelledError:
        pass


@pytest.mark.asyncio
async def test_lock_released_after_abort(monkeypatch):
    """After a render aborts at the op-cap, the lock is free for the next."""
    async def _hang(url, timeout):
        await asyncio.sleep(30)
    monkeypatch.setattr(headless, "_fetch_rendered_html_locked", _hang)
    await headless.fetch_rendered_html("http://a")  # aborts at 0.3s op-cap
    assert not headless._get_render_lock().locked()


@pytest.mark.asyncio
async def test_unavailable_returns_empty_without_lock(monkeypatch):
    monkeypatch.setattr(headless, "is_available", lambda: False)
    assert await headless.fetch_rendered_html("http://a") == ""
