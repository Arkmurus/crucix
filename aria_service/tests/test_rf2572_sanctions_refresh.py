"""R-F2572 — reliable canonical-sanctions refresh loop + fixed OFAC URL.

The DAILY-SANCTIONS-REFRESH task used `tool: shell`, which the engine can't execute
(silent no-op → 58-day-stale store), and the OFAC URL (treasury.gov/.../sdn_enhanced.xml)
is dead (404). This tests the replacement lifespan helper `_sanctions_refresh_once`
(staleness-gated, wires failure on incomplete) + the corrected URL.
"""
from __future__ import annotations

import asyncio
import time

from aria_service import main as M
from aria_service.intel.sanctions_canonical import store as _ss
from scripts import refresh_sanctions as _rs


def test_refresh_skips_when_store_is_fresh(monkeypatch):
    monkeypatch.setattr(_ss, "newest_entry_refresh", lambda *a, **k: time.time() - 3600)  # 1h old
    called = {"n": 0}

    def _should_not_run(*a, **k):
        called["n"] += 1
        return {}
    monkeypatch.setattr(_rs, "refresh_all", _should_not_run)
    r = asyncio.run(M._sanctions_refresh_once())
    assert r["refreshed"] is False and r["reason"] == "fresh"
    assert called["n"] == 0     # a fresh store must NOT re-download the 108MB feed


def test_refresh_runs_when_stale(monkeypatch):
    monkeypatch.setattr(_ss, "newest_entry_refresh", lambda *a, **k: time.time() - 60 * 86400)  # 60d stale
    monkeypatch.setattr(_rs, "refresh_all", lambda *a, **k: {
        "ofac_sdn": {"success": True, "rows_loaded": 19000},
        "eu_consolidated": {"success": True, "rows_loaded": 6000},
    })
    r = asyncio.run(M._sanctions_refresh_once())
    assert r["refreshed"] is True and r["ok"] is True


def test_refresh_flags_incomplete_when_a_source_loads_zero(monkeypatch):
    # empty store (newest=None) is treated as stale → refresh runs; ofac drifted to 0 rows
    # (R-F2570 returns success=True + rows_loaded=0) must be flagged NOT-ok.
    monkeypatch.setattr(_ss, "newest_entry_refresh", lambda *a, **k: None)
    monkeypatch.setattr(_rs, "refresh_all", lambda *a, **k: {
        "ofac_sdn": {"success": True, "rows_loaded": 0},
        "eu_consolidated": {"success": True, "rows_loaded": 6000},
    })
    r = asyncio.run(M._sanctions_refresh_once())
    assert r["refreshed"] is True and r["ok"] is False


def test_ofac_url_is_the_live_service_not_the_dead_treasury_url():
    url = _rs.SOURCES["ofac_sdn"]["url"]
    assert "sanctionslistservice.ofac.treas.gov" in url
    assert "treasury.gov/ofac/downloads" not in url   # the 404 URL is gone


if __name__ == "__main__":
    import sys
    import pytest
    sys.exit(pytest.main([__file__, "-v"]))
