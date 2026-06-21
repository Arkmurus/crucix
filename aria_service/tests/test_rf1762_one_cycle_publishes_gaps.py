"""R-F1762 — capability test for R-F1761 (coder publishes scan results).

The bug (R-F1046 regression): self_coder._one_cycle() ran gap_detector.scan()
every cycle but NEVER called publish_latest() — so the scan results were never
written to `crucix:aria:gaps:latest`, and /api/aria/coder/gaps always returned
scanned_at=null (the coder looked "off" even though it was scanning).

R-F1761 added `await self.gap_detector.publish_latest(gaps)` to _one_cycle()
right after scan(). This test drives the REAL _one_cycle() path and asserts the
user-visible outcome: publish_latest IS invoked with the scan results.

ARIA shipped R-F1761 without a fix-specific capability test; Claude added it.
"""
from __future__ import annotations

import asyncio


class _StubRedis:
    """Minimal async redis surface ARIACoder construction touches."""
    def __init__(self) -> None:
        self.store: dict = {}

    async def get(self, k):
        return self.store.get(k)

    async def set(self, k, v, *a, **kw):
        self.store[k] = v

    async def setex(self, k, _ttl, v):
        self.store[k] = v

    async def lpush(self, k, v):
        self.store.setdefault(k, []).insert(0, v)

    async def ltrim(self, *a, **kw):
        return None

    async def lrange(self, k, s, e):
        return (self.store.get(k) or [])[s:e + 1]

    async def incr(self, k):
        self.store[k] = int(self.store.get(k, 0)) + 1
        return self.store[k]

    async def expire(self, *a, **kw):
        return None


def test_one_cycle_calls_publish_latest_with_scan_results(monkeypatch):
    """_one_cycle must publish whatever scan() returned to gaps:latest.
    Pre-R-F1761 this was never called → /coder/gaps stayed null."""
    from aria_service.autonomous.self_coder import ARIACoder

    coder = ARIACoder(redis_client=_StubRedis(),
                      aria_service_url="http://localhost:8000")

    SENTINEL_GAPS = []  # empty scan → downstream fix pipeline is a clean no-op
    published: list = []

    async def _fake_scan():
        return SENTINEL_GAPS

    async def _spy_publish_latest(gaps):
        published.append(gaps)

    monkeypatch.setattr(coder.gap_detector, "scan", _fake_scan)
    monkeypatch.setattr(coder.gap_detector, "publish_latest", _spy_publish_latest)

    asyncio.run(coder._one_cycle())

    # The capability: publish_latest was invoked exactly once, with the
    # scan results — restoring /coder/gaps observability (the R-F1761 fix).
    assert len(published) == 1, (
        f"publish_latest called {len(published)}x (expected 1) — "
        "the R-F1046 regression (never published) is back"
    )
    assert published[0] is SENTINEL_GAPS, "must publish the exact scan results"


def test_publish_failure_does_not_break_the_cycle(monkeypatch):
    """Honesty/robustness: a Redis hiccup in publish_latest must NOT crash
    the cycle (R-F1761 wraps it in try/except)."""
    from aria_service.autonomous.self_coder import ARIACoder

    coder = ARIACoder(redis_client=_StubRedis(),
                      aria_service_url="http://localhost:8000")

    async def _fake_scan():
        return []

    async def _boom_publish(gaps):
        raise RuntimeError("redis down")

    monkeypatch.setattr(coder.gap_detector, "scan", _fake_scan)
    monkeypatch.setattr(coder.gap_detector, "publish_latest", _boom_publish)

    # Must NOT raise — the cycle swallows publish errors (non-fatal).
    asyncio.run(coder._one_cycle())
