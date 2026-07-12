"""R-F2577 — sources/ofac_sdn + un_sc_sanctions partial-drift canary.

A large feed that parses implausibly few records (schema drift) previously overwrote the
healthy in-memory cache with thin data → a dropped sanctioned entity screened as no-hit =
clean. The canary keeps the last-known-good cache and warns instead.
"""
from __future__ import annotations

import asyncio

import pytest

from aria_service.intel.sources import ofac_sdn, un_sc_sanctions
from aria_service.intel.sources import _common


_BIG_XML = "<x>" + ("y" * 2_000_000) + "</x>"   # >1MB feed body


def _load(monkeypatch, mod, parse_returns, prior):
    mod._CACHE["records"] = list(prior)
    mod._CACHE["fetched_at"] = 0.0    # stale → force a refresh
    async def _fake_get(*a, **k):
        return _BIG_XML
    monkeypatch.setattr(_common, "http_get_text", _fake_get)
    monkeypatch.setattr(mod, "_parse_xml", lambda _x: list(parse_returns))
    try:
        return asyncio.run(mod._load_records())
    finally:
        mod._CACHE["records"] = []
        mod._CACHE["fetched_at"] = 0.0


# ── ofac_sdn (absolute floor 5000) ───────────────────────────────────────────
def test_ofac_thin_first_load_keeps_empty(monkeypatch):
    r = _load(monkeypatch, ofac_sdn, range(100), prior=[])
    assert len(r) == 0            # thin (100 < 5000) NOT cached → lookup reports unavailable


def test_ofac_healthy_load_caches(monkeypatch):
    r = _load(monkeypatch, ofac_sdn, range(6000), prior=[])
    assert len(r) == 6000


def test_ofac_drift_keeps_last_known_good(monkeypatch):
    # healthy prior (6000) + a drifted parse (100) → keep the 6000, don't overwrite with 100
    r = _load(monkeypatch, ofac_sdn, range(100), prior=range(6000))
    assert len(r) == 6000


# ── un_sc_sanctions (absolute floor 300) ─────────────────────────────────────
def test_un_thin_first_load_keeps_empty(monkeypatch):
    r = _load(monkeypatch, un_sc_sanctions, range(50), prior=[])
    assert len(r) == 0


def test_un_healthy_load_caches(monkeypatch):
    r = _load(monkeypatch, un_sc_sanctions, range(1000), prior=[])
    assert len(r) == 1000


if __name__ == "__main__":
    import sys
    sys.exit(pytest.main([__file__, "-v"]))
