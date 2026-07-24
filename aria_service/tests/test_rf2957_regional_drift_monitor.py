"""R-F2957 — regional-mastery drift monitor (Phase A gate #2 compounding observability).

Tests the regional snapshot + drift/velocity logic in
aria_service/intel/regional_drift_monitor.py without hitting real Redis or the
real student heatmap. Mirrors test_rf779_brier_drift_monitor.py.
"""
from __future__ import annotations

import asyncio
import time
from unittest import mock


def _fake_heatmap(cells: dict[str, float]) -> dict:
    """Build a get_regional_heatmap()-shaped return from a {topic:region: score} map."""
    heatmap: dict[str, dict[str, float]] = {}
    for key, score in cells.items():
        topic, region = key.split(":", 1)
        heatmap.setdefault(topic, {})[region] = score
    return {"heatmap": heatmap, "weak_cells": [], "floor_breach_cells": [], "gate_2_floor_target": 0.70}


def test_rf2957_snapshot_persists_with_floor_and_counts():
    """snapshot_regional() must lpush+ltrim the regional key with floor / mean /
    count_ge_070 / cell_count and the FULL per-cell map (unbounded, no [:N])."""
    from aria_service.intel import regional_drift_monitor as rdm

    cells = {
        "procurement:central_africa": 0.05,
        "market_intel:lusophone": 0.94,
        "legal:europe": 0.69,
        "sanctions:gulf": 0.72,
    }
    pushes: list = []
    trims: list = []

    async def fake_lpush(key, value):
        pushes.append((key, value))

    async def fake_ltrim(key, start, stop):
        trims.append((key, start, stop))

    async def fake_hm():
        return _fake_heatmap(cells)

    async def run():
        with mock.patch("aria_service.intel.student.get_regional_heatmap", side_effect=fake_hm), \
             mock.patch.object(rdm.rs, "lpush", side_effect=fake_lpush), \
             mock.patch.object(rdm.rs, "ltrim", side_effect=fake_ltrim):
            return await rdm.snapshot_regional()

    snap = asyncio.run(run())
    assert snap["floor"] == 0.05
    assert snap["cell_count"] == 4
    assert snap["count_ge_070"] == 2  # 0.94 and 0.72 (0.69 is below 0.70)
    assert snap["cells"]["procurement:central_africa"] == 0.05
    assert len(snap["cells"]) == 4, "per-cell map must be unbounded (no [:N] truncation, §1)"
    assert len(pushes) == 1 and pushes[0][0] == rdm._SNAPSHOT_KEY
    assert len(trims) == 1 and trims[0][2] == rdm._MAX_SNAPSHOTS - 1


def test_rf2957_empty_heatmap_skips_write():
    """A 0-cell heatmap (store not ready, R-F2664) must NOT poison the series
    with a null-floor row — no lpush at all."""
    from aria_service.intel import regional_drift_monitor as rdm

    pushes: list = []

    async def fake_lpush(key, value):
        pushes.append((key, value))

    async def fake_hm():
        return {"heatmap": {}, "weak_cells": [], "floor_breach_cells": []}

    async def run():
        with mock.patch("aria_service.intel.student.get_regional_heatmap", side_effect=fake_hm), \
             mock.patch.object(rdm.rs, "lpush", side_effect=fake_lpush):
            return await rdm.snapshot_regional()

    snap = asyncio.run(run())
    assert snap.get("skipped") == "no_cells"
    assert pushes == [], "must not write a snapshot when there are 0 cells"


def test_rf2957_compute_drift_signed_floor_delta():
    """compute_regional_drift returns a SIGNED floor delta (latest - baseline);
    a positive floor delta = the gate-#2 floor rose."""
    from aria_service.intel import regional_drift_monitor as rdm

    now = time.time()
    fake_snapshots = [
        {  # latest — floor rose 0.05 -> 0.12, one more cell crossed 0.70
            "ts": now, "floor": 0.12, "mean": 0.55, "count_ge_070": 3, "cell_count": 4,
            "cells": {"procurement:central_africa": 0.12, "legal:europe": 0.71},
        },
        {  # a week ago
            "ts": now - 7 * 86400, "floor": 0.05, "mean": 0.50, "count_ge_070": 2, "cell_count": 4,
            "cells": {"procurement:central_africa": 0.05, "legal:europe": 0.69},
        },
    ]

    async def fake_read():
        return fake_snapshots

    async def run():
        with mock.patch.object(rdm, "_read_snapshots", side_effect=fake_read):
            return await rdm.compute_regional_drift(window_hours=168)

    drift = asyncio.run(run())
    assert drift["ok"] is True
    assert drift["floor_delta"] == round(0.12 - 0.05, 4)
    assert drift["count_ge_070_delta"] == 1
    assert drift["cell_deltas"]["procurement:central_africa"] == round(0.12 - 0.05, 4)
    assert drift["top_risers"][0][0] in ("procurement:central_africa", "legal:europe")


def test_rf2957_floor_velocity_reports_compounding():
    """floor_velocity flags compounding=True when the floor rose or more cells
    crossed the target, and reports floor_delta."""
    from aria_service.intel import regional_drift_monitor as rdm

    now = time.time()
    fake_snapshots = [
        {"ts": now, "floor": 0.12, "mean": 0.55, "count_ge_070": 3, "cell_count": 4, "cells": {"a:b": 0.12}},
        {"ts": now - 7 * 86400, "floor": 0.05, "mean": 0.50, "count_ge_070": 2, "cell_count": 4, "cells": {"a:b": 0.05}},
    ]

    async def fake_read():
        return fake_snapshots

    async def run():
        with mock.patch.object(rdm, "_read_snapshots", side_effect=fake_read):
            return await rdm.floor_velocity(window_hours=168)

    vel = asyncio.run(run())
    assert vel["ok"] is True
    assert vel["compounding"] is True
    assert vel["floor_delta"] == round(0.12 - 0.05, 4)
    assert vel["count_ge_070_delta"] == 1


def test_rf2957_floor_velocity_insufficient_history():
    """With <2 snapshots, floor_velocity degrades to ok=False / insufficient_history
    (never crashes, never claims compounding)."""
    from aria_service.intel import regional_drift_monitor as rdm

    async def fake_read():
        return [{"ts": time.time(), "floor": 0.05, "count_ge_070": 2, "cell_count": 4, "cells": {}}]

    async def run():
        with mock.patch.object(rdm, "_read_snapshots", side_effect=fake_read):
            return await rdm.floor_velocity(window_hours=168)

    vel = asyncio.run(run())
    assert vel["ok"] is False
    assert vel["reason"] == "insufficient_history"
    assert vel["latest_floor"] == 0.05
