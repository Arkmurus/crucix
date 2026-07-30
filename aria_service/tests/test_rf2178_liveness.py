# -*- coding: utf-8 -*-
"""Capability tests for R-F2178 — per-limb liveness registry (proprioception).

Drives the real record_beat → get_liveness → check_stale_and_gap path with an
in-memory fake redis_store, asserting the user-visible outcome: fresh beats read
alive, aged beats read stale, and a stale limb produces a coder-visible gap.
"""
from __future__ import annotations

import asyncio
import fnmatch
import time

import aria_service.intel.liveness as livemod
from aria_service.intel.liveness import (
    record_beat, get_liveness, check_stale_and_gap, probe_searxng_and_beat,
)


class _FakeRS:
    """In-memory async stand-in for redis_store."""

    def __init__(self):
        self.store = {}

    async def set_json(self, k, v, ex=None):
        self.store[k] = v

    async def get_json(self, k):
        return self.store.get(k)

    async def scan_keys(self, pattern, count=200):
        return [k for k in self.store if fnmatch.fnmatch(k, pattern)]


def _install_fake(monkeypatch):
    """R-F3449 — was `livemod.rs = fake` with no restore, leaving the liveness module's
    store a _FakeRS for the REST OF THE SESSION. Six tests call this, so the leak was
    six-deep; latent in the R-F3448 baseline only because nothing after this file re-uses
    livemod.rs. monkeypatch restores it at teardown, so every caller now passes its own."""
    fake = _FakeRS()
    monkeypatch.setattr(livemod, "rs", fake)
    return fake


def test_rf2178_fresh_beat_reads_alive(monkeypatch):
    _install_fake(monkeypatch)
    asyncio.run(record_beat("aria-wa", interval_s=120))
    live = asyncio.run(get_liveness())
    e = live["limbs"]["aria-wa"]
    assert e["alive"] is True and e["stale"] is False


def test_rf2178_aged_beat_reads_stale(monkeypatch):
    fake = _install_fake(monkeypatch)
    asyncio.run(record_beat("aria-web", interval_s=120))
    # Age the beat well past the staleness floor.
    fake.store["crucix:aria:liveness:aria-web"]["ts"] = time.time() - 9999
    live = asyncio.run(get_liveness())
    e = live["limbs"]["aria-web"]
    assert e["stale"] is True and e["alive"] is False


def test_rf2178_non_alive_status_not_alive_even_when_fresh(monkeypatch):
    _install_fake(monkeypatch)
    asyncio.run(record_beat("aria-searxng", status="degraded", interval_s=120))
    live = asyncio.run(get_liveness())
    assert live["limbs"]["aria-searxng"]["alive"] is False  # fresh but not 'alive'


def test_rf2178_stale_limb_records_gap(monkeypatch):
    fake = _install_fake(monkeypatch)
    asyncio.run(record_beat("aria-wa", interval_s=120))
    fake.store["crucix:aria:liveness:aria-wa"]["ts"] = time.time() - 9999

    recorded = []

    async def _spy(**kwargs):
        recorded.append(kwargs)
        return {}

    import aria_service.intel.capability_gaps as cg
    monkeypatch.setattr(cg, "record_gap", _spy)

    stale = asyncio.run(check_stale_and_gap())
    assert "aria-wa" in stale
    assert any("aria-wa" in (r.get("title", "") + r.get("detail", "")) for r in recorded)


def test_rf2181_searxng_probe_records_alive_beat(monkeypatch):
    _install_fake(monkeypatch)

    async def _healthy():
        return {"searxng": True}

    import aria_service.intel.web_search as ws
    monkeypatch.setattr(ws, "get_search_health", _healthy)
    entry = asyncio.run(probe_searxng_and_beat())
    assert entry["limb"] == "aria-searxng" and entry["status"] == "alive"
    live = asyncio.run(get_liveness())
    assert live["limbs"]["aria-searxng"]["alive"] is True


def test_rf2181_searxng_probe_records_down_when_unhealthy(monkeypatch):
    _install_fake(monkeypatch)

    async def _down():
        return {"searxng": False}

    import aria_service.intel.web_search as ws
    monkeypatch.setattr(ws, "get_search_health", _down)
    entry = asyncio.run(probe_searxng_and_beat())
    assert entry["status"] == "down"
    live = asyncio.run(get_liveness())
    assert live["limbs"]["aria-searxng"]["alive"] is False  # fresh but down


def test_rf2178_no_gap_when_all_fresh(monkeypatch):
    _install_fake(monkeypatch)
    asyncio.run(record_beat("aria-web", interval_s=120))
    recorded = []

    async def _spy(**kwargs):
        recorded.append(kwargs)
        return {}

    import aria_service.intel.capability_gaps as cg
    monkeypatch.setattr(cg, "record_gap", _spy)

    stale = asyncio.run(check_stale_and_gap())
    assert stale == []
    # No limb-down gap when all limbs are fresh (ignore incidental brain_hook
    # telemetry from wire_success in the uninitialised test env).
    assert not any(r.get("source") == "liveness_watchdog" for r in recorded)
