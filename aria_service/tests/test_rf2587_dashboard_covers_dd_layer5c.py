"""R-F2587 — the brain-dashboard aggregate must cover /dd/layer-5c/stats.

Pre-R-F2587 the aggregate registry deliberately OMITTED "/dd/layer-5c/stats" —
a stale workaround for a symbol collision that R-F2278 (2026-07-02) already
resolved (the digital-footprint variant was renamed to
`dd_layer_5c_digital_stats_ep`, leaving `dd_layer_5c_stats_ep` a unique symbol).
That omission made this panel the one structural fall-through: the frontend had
to hit it with a direct per-path probe, which returns 401 to a signed-out /
partial viewer and renders as "operator panel requires sign-in".

These tests invoke the ACTUAL aggregate builder and assert the panel is covered.
The registry test is §23-discriminating: it FAILS on pre-R-F2587 code (key
absent) and PASSES after the fix.
"""
from __future__ import annotations

import asyncio

from aria_service.routes import aria as A
from aria_service.intel import commercial_coherence as CC


def test_registry_includes_dd_layer5c():
    # The fix, directly: the path is now a registry key mapped to a zero-arg
    # callable (handler's only arg `limit` defaults to 200, matching the
    # frontend's query-less fetch). FAILS on pre-R-F2587 code.
    reg = A._dashboard_panel_registry()
    assert "/dd/layer-5c/stats" in reg, "aggregate must cover /dd/layer-5c/stats"
    assert callable(reg["/dd/layer-5c/stats"])


def test_aggregate_places_dd_panel_into_panels(monkeypatch):
    # Capability: the aggregate builder (brain_dashboard_ep) actually lands the
    # registered dd/layer-5c panel in `panels`, not `omitted`. Minimal registry
    # + stubbed dependency keeps it deterministic (no 24 live handlers).
    async def _stub_l5c(limit: int = 200):
        return {"by_tier": {"clear": 1}, "runs_scanned": 1, "_stub": True}

    monkeypatch.setattr(CC, "layer_5c_stats", _stub_l5c)
    monkeypatch.setattr(
        A, "_dashboard_panel_registry",
        lambda: {"/dd/layer-5c/stats": A.dd_layer_5c_stats_ep},
    )
    # Bust the 45s aggregate cache so we compute fresh.
    A._DASHBOARD_AGG_CACHE.pop("blob", None)

    blob = asyncio.run(A.brain_dashboard_ep())
    assert blob["ok"] is True
    assert "/dd/layer-5c/stats" in blob["panels"]
    assert blob["panels"]["/dd/layer-5c/stats"].get("_stub") is True
    assert "/dd/layer-5c/stats" not in blob.get("omitted", {})
    # cleanup so the stubbed blob never poisons a later real read
    A._DASHBOARD_AGG_CACHE.pop("blob", None)


if __name__ == "__main__":
    import sys
    import pytest
    sys.exit(pytest.main([__file__, "-v"]))
