"""R-F2580 — the liveness watchdog must detect a wedged COLD writer conn.

probe_liveness previously exercised only the HOT conn (the heartbeat key routes hot), so a
wedged cold writer thread would silently blind verified_facts/audit/reasoning_library with
no escalation. Now it also does a SELECT 1 through the cold conn when the split is active.
"""
from __future__ import annotations

import asyncio

from aria_service.intel import state_store as ss


def _connect(tmp_path):
    return asyncio.run(ss.connect(str(tmp_path / "rf2580.db")))


def test_probe_healthy_when_split_off(tmp_path, monkeypatch):
    # split off (default) → cold probe skipped → probe reflects hot health (True)
    async def go():
        await ss.connect(str(tmp_path / "rf2580.db"))
        monkeypatch.setattr(ss, "_HOTCOLD_SPLIT", False)
        return await ss.probe_liveness()
    assert asyncio.run(go()) is True


def test_probe_fails_on_wedged_cold_conn(tmp_path, monkeypatch):
    class _WedgedCold:
        async def execute(self, *a, **k):
            await asyncio.sleep(10)          # hang → wait_for times out

    async def go():
        await ss.connect(str(tmp_path / "rf2580.db"))
        monkeypatch.setattr(ss, "_HOTCOLD_SPLIT", True)
        monkeypatch.setattr(ss, "_cold_conn", _WedgedCold())
        return await ss.probe_liveness(timeout_s=0.5)
    assert asyncio.run(go()) is False        # cold wedge → unavailable → watchdog escalates


def test_probe_ok_with_healthy_cold_conn(tmp_path, monkeypatch):
    class _Cur:
        async def fetchone(self):
            return (1,)
        async def close(self):
            return None

    class _HealthyCold:
        async def execute(self, *a, **k):
            return _Cur()                     # answers immediately with a real cursor

    async def go():
        await ss.connect(str(tmp_path / "rf2580.db"))
        monkeypatch.setattr(ss, "_HOTCOLD_SPLIT", True)
        monkeypatch.setattr(ss, "_cold_conn", _HealthyCold())
        return await ss.probe_liveness(timeout_s=1.0)
    assert asyncio.run(go()) is True


if __name__ == "__main__":
    import sys
    import pytest
    sys.exit(pytest.main([__file__, "-v"]))
