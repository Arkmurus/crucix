"""R-F684 (2026-05-18) — heatmap floor filtering (gate #2 measurement honesty).

Parallel-agent DD 2026-05-18 found two sources of measurement noise
dragging the visible heatmap floor below 0.70:

  Layer 1 — stale dead-keys in _regional_cache (pre-2026-04-17 names):
    - `latam` (umbrella) — split into latam_lusophone + latam_non_lusophone
    - `asia_pacific` (umbrella) — split into south_asia + southeast_asia
    Both keys still sit in the cache near INITIAL_MASTERY = 0.5 because
    detect_regions() no longer emits them, so they'll never update.

  Layer 2 — `general` topic catch-all noise:
    detect_topics() falls back to `general` when classification fails.
    Quizzes/chats that hit general get a region tag but no real mastery
    signal — the EWMA hovers at 0.5 forever, dominating the floor.

R-F684 filters both at the read path (`get_regional_heatmap`). Cache
contents aren't modified — pre-split keys can be cleaned by a separate
one-time purge if needed.
"""
from __future__ import annotations

import asyncio
import pytest


def _patch_cache(monkeypatch, cache: dict):
    """Stub the _load_regional_mastery loader so we can inject test data."""
    from aria_service.intel import student

    async def fake_load():
        return cache

    monkeypatch.setattr(student, "_load_regional_mastery", fake_load)


def _run(coro):
    return asyncio.run(coro)


def test_rf684_filters_dead_region_keys(monkeypatch):
    """Pre-2026-04-17 region names (`latam`, `asia_pacific`) must be
    excluded from the heatmap because detect_regions never produces them
    anymore — they're frozen at INITIAL_MASTERY = 0.5 and noise."""
    _patch_cache(monkeypatch, {
        # Dead keys (pre-split) — must NOT appear in heatmap
        "procurement:latam":       {"score": 0.507, "samples": 1},
        "competitor_intel:asia_pacific": {"score": 0.557, "samples": 1},
        # Live keys — must appear
        "procurement:latam_lusophone":     {"score": 0.807, "samples": 5},
        "procurement:latam_non_lusophone": {"score": 0.612, "samples": 3},
        "procurement:south_asia":          {"score": 0.515, "samples": 2},
    })
    from aria_service.intel import student
    result = _run(student.get_regional_heatmap())
    heatmap = result["heatmap"]

    procurement = heatmap.get("procurement", {})
    assert "latam" not in procurement, (
        f"R-F684: dead region `latam` leaked through: {procurement}"
    )
    assert "asia_pacific" not in heatmap.get("competitor_intel", {})
    # Live keys retained.
    assert procurement["latam_lusophone"] == 0.807
    assert procurement["latam_non_lusophone"] == 0.612
    assert procurement["south_asia"] == 0.515


def test_rf684_excludes_general_topic_from_heatmap(monkeypatch):
    """The `general` topic is a detect_topics fallback — its regional
    cells are catch-all noise and must NOT appear in the heatmap."""
    _patch_cache(monkeypatch, {
        # general-topic cells — all noise, must NOT appear
        "general:south_asia":       {"score": 0.515, "samples": 1},
        "general:gulf":             {"score": 0.515, "samples": 1},
        "general:turkey":           {"score": 0.515, "samples": 1},
        # Legitimate topic cells
        "procurement:gulf":         {"score": 0.730, "samples": 4},
        "sanctions:turkey":         {"score": 0.812, "samples": 5},
    })
    from aria_service.intel import student
    result = _run(student.get_regional_heatmap())
    heatmap = result["heatmap"]
    assert "general" not in heatmap, (
        f"R-F684: `general` topic leaked into heatmap: keys={list(heatmap.keys())}"
    )
    assert "procurement" in heatmap
    assert "sanctions" in heatmap


def test_rf684_weak_cells_recomputed_after_filter(monkeypatch):
    """The weak_cells list must reflect the filtered heatmap —
    a dead-key cell at 0.507 must NOT show up as the worst weak cell,
    and `general`-topic noise must NOT dominate the list. WEAK_THRESHOLD
    is 0.55, so legitimate cells below that should still surface."""
    _patch_cache(monkeypatch, {
        # Dead key (0.507) — would be #1 weak cell pre-fix, now hidden
        "procurement:latam": {"score": 0.507, "samples": 1},
        # general-topic floor noise (0.5) — would dominate weak_cells
        "general:south_asia": {"score": 0.500, "samples": 1},
        "general:gulf":       {"score": 0.500, "samples": 1},
        # Legitimate weak cell (below WEAK_THRESHOLD=0.55) — should top list
        "procurement:turkey": {"score": 0.520, "samples": 2},
        # Strong cell
        "procurement:latam_lusophone": {"score": 0.812, "samples": 5},
    })
    from aria_service.intel import student
    result = _run(student.get_regional_heatmap())
    weak_cells = result["weak_cells"]

    # Top weak cell should be the legitimate 0.520, not the dead 0.507.
    assert weak_cells, f"weak_cells should not be empty after filter: {result}"
    top = weak_cells[0]
    assert top["topic"] == "procurement"
    assert top["region"] == "turkey"
    assert top["score"] == 0.520

    # No weak cell should be from `general` or `latam` (dead/noise).
    for cell in weak_cells:
        assert cell["topic"] != "general", f"general leaked: {cell}"
        assert cell["region"] != "latam", f"dead latam leaked: {cell}"
        assert cell["region"] != "asia_pacific"


def test_rf684_legitimate_regions_pass_through(monkeypatch):
    """All current REGIONS values must be allowed through unchanged."""
    from aria_service.intel import student
    cache = {
        f"procurement:{r}": {"score": 0.75, "samples": 1}
        for r in student.REGIONS
    }
    _patch_cache(monkeypatch, cache)
    result = _run(student.get_regional_heatmap())
    proc = result["heatmap"].get("procurement", {})
    for r in student.REGIONS:
        assert r in proc, f"R-F684 over-filtered: REGIONS member `{r}` dropped"
        assert proc[r] == 0.75
