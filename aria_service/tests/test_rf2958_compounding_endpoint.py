"""R-F2958 — /student/mastery/compounding endpoint (Phase A gate #2 velocity surface).

Invokes the real endpoint function with a mocked regional_drift_monitor so it
exercises the actual response-shaping path without booting the whole app.
"""
from __future__ import annotations

import asyncio
import time
from unittest import mock


def test_rf2958_compounding_endpoint_shape():
    """The endpoint returns velocity + trend (per-snapshot aggregates, NO per-cell
    map) + snapshot count + gate target."""
    from aria_service.routes import aria as aria_routes

    now = time.time()
    fake_velocity = {
        "ok": True, "compounding": True, "floor_delta": 0.07,
        "count_ge_070_delta": 1, "latest_floor": 0.12, "latest_count_ge_070": 3,
    }
    fake_snapshots = [
        {"ts": now, "floor": 0.12, "mean": 0.55, "count_ge_070": 3, "cell_count": 4,
         "cells": {"a:b": 0.12, "c:d": 0.9}},
        {"ts": now - 7 * 86400, "floor": 0.05, "mean": 0.50, "count_ge_070": 2, "cell_count": 4,
         "cells": {"a:b": 0.05, "c:d": 0.9}},
    ]

    async def fake_velocity_fn(window_hours=168):
        return fake_velocity

    async def fake_read():
        return fake_snapshots

    async def run():
        from aria_service.intel import regional_drift_monitor as rdm
        with mock.patch.object(rdm, "floor_velocity", side_effect=fake_velocity_fn), \
             mock.patch.object(rdm, "_read_snapshots", side_effect=fake_read):
            return await aria_routes.mastery_compounding_ep(window_hours=168)

    out = asyncio.run(run())
    assert out["velocity"]["compounding"] is True
    assert out["velocity"]["floor_delta"] == 0.07
    assert out["snapshots"] == 2
    assert out["gate_2_target"] == 0.70
    assert len(out["trend"]) == 2
    # trend rows carry aggregates only — NOT the heavy per-cell map
    assert out["trend"][0]["floor"] == 0.12
    assert "cells" not in out["trend"][0], "per-cell map must be stripped from the trend payload"


def test_rf2958_endpoint_insufficient_history_is_honest():
    """With <2 snapshots the endpoint surfaces ok=False/insufficient_history —
    never a fabricated compounding claim."""
    from aria_service.routes import aria as aria_routes

    async def fake_velocity_fn(window_hours=168):
        return {"ok": False, "reason": "insufficient_history", "snapshots": 1, "latest_floor": 0.05}

    async def fake_read():
        return [{"ts": time.time(), "floor": 0.05, "count_ge_070": 2, "cell_count": 4}]

    async def run():
        from aria_service.intel import regional_drift_monitor as rdm
        with mock.patch.object(rdm, "floor_velocity", side_effect=fake_velocity_fn), \
             mock.patch.object(rdm, "_read_snapshots", side_effect=fake_read):
            return await aria_routes.mastery_compounding_ep()

    out = asyncio.run(run())
    assert out["velocity"]["ok"] is False
    assert out["velocity"]["reason"] == "insufficient_history"
    assert out["snapshots"] == 1
