"""R-F1642 — RunPod scheduler STOP-ONLY mode + work-claim guard.

Capability under test (CLAUDE.md §24 — pre-shadow, fully-managed, never
auto-burn): with autostart OFF (the production default) the scheduler NEVER
starts a pod, force-stops an unclaimed RUNNING pod, and a live work-claim
protects an in-use pod from being stopped mid-cycle. Window-mode (autostart
ON) restores the R-F1335 start behaviour.
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import time
from datetime import datetime

try:
    from zoneinfo import ZoneInfo
    LONDON = ZoneInfo("Europe/London")
except (ImportError, KeyError, Exception):
    import pytest
    pytest.skip("zoneinfo Europe/London not available", allow_module_level=True)

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

from aria_service.intel import runpod_scheduler as rps


@pytest.fixture(autouse=True)
def _env(monkeypatch, tmp_path):
    monkeypatch.setenv("RUNPOD_API_KEY", "test-key")
    monkeypatch.setenv("ARIA_RUNPOD_POD_ID", "pod-test-123")
    monkeypatch.setenv("ARIA_RUNPOD_SCHEDULE_ENABLED", "1")
    monkeypatch.setenv("ARIA_RUNPOD_CLAIM_PATH", str(tmp_path / "claim.json"))
    monkeypatch.delenv("ARIA_RUNPOD_AUTOSTART", raising=False)  # default => stop-only
    monkeypatch.delenv("ARIA_RUNPOD_TZ", raising=False)
    yield


@pytest.fixture
def fake_api(monkeypatch):
    state = {"status": "EXITED"}
    actions: list[str] = []

    async def _status():
        actions.append("status"); return state["status"]

    async def _start():
        actions.append("start"); state["status"] = "RUNNING"; return {"ok": True}

    async def _stop():
        actions.append("stop"); state["status"] = "EXITED"; return {"ok": True}

    monkeypatch.setattr(rps, "get_pod_status", _status)
    monkeypatch.setattr(rps, "start_pod", _start)
    monkeypatch.setattr(rps, "stop_pod", _stop)
    return type("C", (), {"state": state, "actions": actions})


# ── stop-only mode (production default) ──────────────────────────────────

@pytest.mark.asyncio
async def test_stop_only_never_starts_in_window(fake_api):
    """Inside the window, stopped pod — stop-only must NOT start it."""
    fake_api.state["status"] = "EXITED"
    now = datetime(2026, 6, 5, 11, 0, tzinfo=LONDON)  # inside 10-18
    action = await rps.ensure_state(now)
    assert action == "noop"
    assert "start" not in fake_api.actions


@pytest.mark.asyncio
async def test_stop_only_force_stops_unclaimed_running_pod(fake_api):
    """A pod left RUNNING with no claim is force-stopped — even inside the
    window (the forgotten-pod / cost-safety case)."""
    fake_api.state["status"] = "RUNNING"
    now = datetime(2026, 6, 5, 11, 0, tzinfo=LONDON)
    action = await rps.ensure_state(now)
    assert action == "stopped_stray"
    assert "stop" in fake_api.actions


@pytest.mark.asyncio
async def test_active_claim_protects_running_pod(fake_api):
    """A live work-claim must prevent the stop sweep (training in progress)."""
    rps.claim_pod(ttl_s=3600, reason="v0.5 SFT")
    fake_api.state["status"] = "RUNNING"
    now = datetime(2026, 6, 5, 11, 0, tzinfo=LONDON)
    action = await rps.ensure_state(now)
    assert action == "claim_hold"
    assert "stop" not in fake_api.actions


@pytest.mark.asyncio
async def test_expired_claim_no_longer_protects(fake_api, monkeypatch, tmp_path):
    """Once the claim TTL passes, the forgotten pod is stopped again."""
    path = tmp_path / "claim.json"
    path.write_text(json.dumps({"until": time.time() - 10, "reason": "stale"}), encoding="utf-8")
    fake_api.state["status"] = "RUNNING"
    now = datetime(2026, 6, 5, 11, 0, tzinfo=LONDON)
    action = await rps.ensure_state(now)
    assert action == "stopped_stray"


def test_claim_release_roundtrip():
    rec = rps.claim_pod(ttl_s=60, reason="x")
    assert rec["until"] > time.time()
    active, got = rps._claim_active()
    assert active and got["reason"] == "x"
    assert rps.release_pod() is True
    active2, _ = rps._claim_active()
    assert active2 is False


def test_status_reports_stop_only_mode():
    s = rps.get_status()
    assert s["mode"] == "stop_only"
    assert s["autostart"] is False


# ── window mode (shadow phase) still works ───────────────────────────────

@pytest.mark.asyncio
async def test_window_mode_starts_when_enabled(fake_api, monkeypatch):
    monkeypatch.setenv("ARIA_RUNPOD_AUTOSTART", "1")
    fake_api.state["status"] = "EXITED"
    now = datetime(2026, 6, 5, 11, 0, tzinfo=LONDON)
    assert await rps.ensure_state(now) == "started"
    assert "start" in fake_api.actions
