"""R-F679 (2026-05-18) — Lightpanda render lock prevents port-9222 collision.

Live fly logs 2026-05-18 07:16:08-12 showed three back-to-back warnings:
    Lightpanda failed to start: address already in use
        host=127.0.0.1 port=9222

Three concurrent fetch_rendered_html calls all tried to spawn Lightpanda
on the same port; only one won. Peer renders silently returned empty
strings — making extraction non-deterministic.

R-F679 wraps fetch_rendered_html in a module-level asyncio.Lock so
concurrent calls queue cleanly. Lightpanda renders complete in 1-3s
so queueing on the extraction path is acceptable.

This test uses a fake inner body so we don't need an actual Lightpanda
binary; we only verify that the lock SERIALISES concurrent callers.
"""
from __future__ import annotations

import asyncio

import pytest


def test_rf679_lock_exists_and_is_lazy():
    """Importing the module must not require an event loop."""
    from aria_service.intel import headless
    # Before the first lock acquisition, the global may be None — that's
    # fine; the helper lazy-inits it. Calling _get_render_lock() in a sync
    # context just constructs the asyncio.Lock object (doesn't await).
    lock = headless._get_render_lock()
    assert isinstance(lock, asyncio.Lock)
    # Idempotent — same lock instance returned across calls.
    assert headless._get_render_lock() is lock


def test_rf679_concurrent_renders_serialise(monkeypatch):
    """3 concurrent fetch_rendered_html calls must run one-at-a-time
    under the lock, never overlapping."""
    from aria_service.intel import headless

    # Reset the lock so prior tests don't leave a different loop's lock in place.
    headless._RENDER_LOCK = None

    # Force is_available() to True so we reach the lock + inner body.
    monkeypatch.setattr(headless, "is_available", lambda: True)

    concurrent_count = 0
    max_concurrent = 0
    call_order: list[str] = []

    async def fake_inner(url, timeout):
        nonlocal concurrent_count, max_concurrent
        concurrent_count += 1
        max_concurrent = max(max_concurrent, concurrent_count)
        call_order.append(f"start:{url}")
        await asyncio.sleep(0.05)  # simulate render work
        call_order.append(f"end:{url}")
        concurrent_count -= 1
        return f"<html>{url}</html>"

    monkeypatch.setattr(
        headless, "_fetch_rendered_html_locked", fake_inner,
    )

    async def go():
        # Fire 3 concurrent renders.
        results = await asyncio.gather(
            headless.fetch_rendered_html("https://a.example/"),
            headless.fetch_rendered_html("https://b.example/"),
            headless.fetch_rendered_html("https://c.example/"),
        )
        return results

    results = asyncio.run(go())

    # Each call returned its own HTML — no skipped peers.
    assert len(results) == 3
    assert all("<html>" in r for r in results), results

    # CRITICAL: max concurrency was 1 — lock serialised the calls.
    assert max_concurrent == 1, (
        f"R-F679: lock didn't serialise — peak concurrency {max_concurrent}, "
        f"call order: {call_order}"
    )

    # Pattern must be start-end-start-end-start-end, never start-start-...
    for i in range(0, 6, 2):
        assert call_order[i].startswith("start:"), call_order
        assert call_order[i + 1].startswith("end:"), call_order


def test_rf679_unavailable_lightpanda_short_circuits_before_lock(monkeypatch):
    """is_available() False must return "" without ever acquiring the
    lock — otherwise a missing Lightpanda binary would block the lock
    chain for unrelated test cases."""
    from aria_service.intel import headless

    monkeypatch.setattr(headless, "is_available", lambda: False)

    inner_calls = 0

    async def fake_inner(url, timeout):
        nonlocal inner_calls
        inner_calls += 1
        return "should not be called"

    monkeypatch.setattr(
        headless, "_fetch_rendered_html_locked", fake_inner,
    )

    result = asyncio.run(headless.fetch_rendered_html("https://x.example/"))
    assert result == ""
    assert inner_calls == 0, "inner body must NOT run when Lightpanda is unavailable"
