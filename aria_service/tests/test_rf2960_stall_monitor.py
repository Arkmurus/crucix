"""R-F2960 (B2) — score-stagnation stall monitor.

detect_stalled_cells flags below-floor regional cells that are FLAT over the
window (the case research_engine._is_stalled misses: ingesting but never lifting),
classifying starved vs grade_failing from live sample counts. record_stalled_gaps
records ONE aggregate stalled_cell gap (no per-cell spam). Never mutates a score.
"""
from __future__ import annotations

import asyncio
import time
from unittest import mock


def _snaps(now, latest_cells, base_cells, *, extra_mid=True):
    """Build a >=3 snapshot ring (min_snapshots guard) with a latest + a
    week-old baseline."""
    ring = [
        {"ts": now, "floor": min(latest_cells.values()), "cell_count": len(latest_cells), "cells": latest_cells},
    ]
    if extra_mid:
        ring.append({"ts": now - 3 * 86400, "floor": 0.1, "cell_count": len(latest_cells), "cells": latest_cells})
    ring.append({"ts": now - 7 * 86400, "floor": min(base_cells.values()), "cell_count": len(base_cells), "cells": base_cells})
    return ring


def test_rf2960_flat_below_floor_cell_is_stalled_and_classified():
    """A below-floor cell flat across the window is stalled; classified starved
    (<=1 sample) vs grade_failing (has samples)."""
    from aria_service.intel import regional_drift_monitor as rdm

    now = time.time()
    latest = {"procurement:central_africa": 0.05, "technical:southern_africa": 0.30, "market_intel:lusophone": 0.94}
    base = {"procurement:central_africa": 0.05, "technical:southern_africa": 0.30, "market_intel:lusophone": 0.90}
    ring = _snaps(now, latest, base)

    # central_africa: 0 samples → starved; southern_africa: 40 samples → grade_failing
    fake_rm = {
        "procurement:central_africa": {"score": 0.05, "samples": 0},
        "technical:southern_africa": {"score": 0.30, "samples": 40},
    }

    async def fake_read():
        return ring

    async def fake_load_rm():
        return fake_rm

    async def run():
        with mock.patch.object(rdm, "_read_snapshots", side_effect=fake_read), \
             mock.patch("aria_service.intel.student._load_regional_mastery", side_effect=fake_load_rm):
            return await rdm.detect_stalled_cells(window_hours=168, min_snapshots=3)

    stalled = asyncio.run(run())
    cells = {c["cell"]: c for c in stalled}
    assert "procurement:central_africa" in cells
    assert cells["procurement:central_africa"]["why"] == "starved"
    assert "technical:southern_africa" in cells
    assert cells["technical:southern_africa"]["why"] == "grade_failing"
    # A cell at/above the 0.70 floor is NOT stalled even if flat
    assert "market_intel:lusophone" not in cells


def test_rf2960_moving_cell_not_stalled():
    """A below-floor cell that is RISING is not stalled."""
    from aria_service.intel import regional_drift_monitor as rdm

    now = time.time()
    latest = {"procurement:central_africa": 0.20}   # rose from 0.05
    base = {"procurement:central_africa": 0.05}
    ring = _snaps(now, latest, base)

    async def fake_read():
        return ring

    async def fake_load_rm():
        return {"procurement:central_africa": {"score": 0.20, "samples": 30}}

    async def run():
        with mock.patch.object(rdm, "_read_snapshots", side_effect=fake_read), \
             mock.patch("aria_service.intel.student._load_regional_mastery", side_effect=fake_load_rm):
            return await rdm.detect_stalled_cells(window_hours=168, min_snapshots=3)

    assert asyncio.run(run()) == []


def test_rf2960_insufficient_history_no_stall():
    """< min_snapshots history → no stall calls (early boot flatness is not a stall)."""
    from aria_service.intel import regional_drift_monitor as rdm

    async def fake_read():
        return [{"ts": time.time(), "cells": {"a:b": 0.05}}]

    async def run():
        with mock.patch.object(rdm, "_read_snapshots", side_effect=fake_read):
            return await rdm.detect_stalled_cells(window_hours=168, min_snapshots=3)

    assert asyncio.run(run()) == []


def test_rf2960_record_stalled_gaps_records_one_aggregate_gap():
    """record_stalled_gaps records exactly ONE aggregate stalled_cell gap (not one
    per cell) with severity + a starved/grade_failing breakdown."""
    from aria_service.intel import regional_drift_monitor as rdm

    now = time.time()
    latest = {"procurement:central_africa": 0.05, "technical:southern_africa": 0.30}
    base = {"procurement:central_africa": 0.05, "technical:southern_africa": 0.30}
    ring = _snaps(now, latest, base)
    gaps: list = []

    async def fake_read():
        return ring

    async def fake_load_rm():
        return {"procurement:central_africa": {"score": 0.05, "samples": 0},
                "technical:southern_africa": {"score": 0.30, "samples": 40}}

    async def fake_record_gap(**kw):
        gaps.append(kw)
        return {}

    async def run():
        with mock.patch.object(rdm, "_read_snapshots", side_effect=fake_read), \
             mock.patch("aria_service.intel.student._load_regional_mastery", side_effect=fake_load_rm), \
             mock.patch("aria_service.intel.capability_gaps.record_gap", side_effect=fake_record_gap):
            return await rdm.record_stalled_gaps(window_hours=168, min_snapshots=3)

    out = asyncio.run(run())
    assert out["stalled"] == 2
    assert out["starved"] == 1 and out["grade_failing"] == 1
    assert out["recorded"] is True
    assert len(gaps) == 1, "must record ONE aggregate gap, not one per cell"
    assert gaps[0]["gap_type"] == "stalled_cell"
    assert gaps[0]["severity"] == "HIGH"
