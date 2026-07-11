"""R-F2537 — brain observability DD fixes.

Finding #4: a "healthy" /api/aria/brain/stats could hide a dead-lettered ingest
backlog, a stuck drain, or dropped semantic-index work — get_stats() didn't expose
either queue. Finding #5: the stats implied full-ecosystem coverage when they only
reflect the current visible signal window.

These tests drive the REAL brain_hook.get_stats() and assert the new surfaces.
"""
from __future__ import annotations

import asyncio


def test_get_stats_exposes_queue_health():
    from aria_service.intel import brain_hook
    s = asyncio.run(brain_hook.get_stats())

    assert "queues" in s, "get_stats must surface queue health (finding #4)"
    q = s["queues"]
    assert "brain_ingest" in q and "semantic_index" in q
    # Durable ingest queue surface: depth / processing / dead_letter (or an explicit
    # unavailable marker — never silently omitted).
    bi = q["brain_ingest"]
    assert ("dead_letter" in bi and "depth" in bi) or bi.get("available") is False
    # Semantic-index queue surface: worker_alive / drops / queue_depth (or unavailable).
    si = q["semantic_index"]
    assert ("worker_alive" in si and "queue_depth" in si) or si.get("available") is False


def test_get_stats_labels_coverage_scope():
    from aria_service.intel import brain_hook
    s = asyncio.run(brain_hook.get_stats())

    assert "coverage" in s, "get_stats must label coverage scope (finding #5)"
    cov = s["coverage"]
    assert cov["scope"] == "current_visible_window"
    assert "not" in cov["note"].lower() and "coverage" in cov["note"].lower()
    assert isinstance(cov["modules_tracked"], int)


def test_get_stats_queue_health_survives_queue_error(monkeypatch):
    """A queue-stats failure must degrade to an explicit 'unavailable', never crash
    get_stats or silently drop the surface (never-false-clean)."""
    from aria_service.intel import brain_hook, brain_ingest_queue

    async def _boom():
        raise RuntimeError("queue db unreachable")
    monkeypatch.setattr(brain_ingest_queue, "stats", _boom)
    # get_stats() caches its result — force a fresh compute so the monkeypatch is hit.
    brain_hook._stats_cache = None
    brain_hook._stats_cache_at = 0.0

    s = asyncio.run(brain_hook.get_stats())
    bi = s["queues"]["brain_ingest"]
    assert bi.get("available") is False and "unreachable" in bi.get("error", "")


if __name__ == "__main__":
    import sys
    import pytest
    sys.exit(pytest.main([__file__, "-v"]))
