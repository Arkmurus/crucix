"""R-F1335 — RunPod scheduler (ARIA's own GPU reasoning window) + eval export.

Capability under test: pod ON inside 10:00-18:00 Europe/London, OFF
outside; reconcile decisions hit the right RunPod REST endpoints; brain
wiring fires on both branches; unconfigured = harmless no-op. Plus the
500-Q eval export the activation runbook needs (its old command never
existed).
"""
import asyncio
import json
import os
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

from aria_service.intel import runpod_scheduler as rps


LONDON = ZoneInfo("Europe/London")


@pytest.fixture(autouse=True)
def _configured_env(monkeypatch):
    monkeypatch.setenv("RUNPOD_API_KEY", "test-key")
    monkeypatch.setenv("ARIA_RUNPOD_POD_ID", "pod-test-123")
    monkeypatch.setenv("ARIA_RUNPOD_SCHEDULE_ENABLED", "1")
    monkeypatch.delenv("ARIA_RUNPOD_START_HOUR", raising=False)
    monkeypatch.delenv("ARIA_RUNPOD_STOP_HOUR", raising=False)
    monkeypatch.delenv("ARIA_RUNPOD_TZ", raising=False)
    yield


# ── window logic (UK time, DST-safe) ─────────────────────────────────────


@pytest.mark.parametrize("hour,minute,expect", [
    (9, 59, False),   # one minute before open
    (10, 0, True),    # opens at 10:00
    (13, 30, True),   # mid-window
    (17, 59, True),   # last minute
    (18, 0, False),   # closes at 18:00
    (23, 0, False),
    (2, 0, False),
])
def test_window_boundaries_bst(hour, minute, expect):
    # June date = BST (UTC+1) — the operator's "UK base timing"
    now = datetime(2026, 6, 5, hour, minute, tzinfo=LONDON)
    assert rps.in_window(now) is expect


def test_window_handles_gmt_winter():
    # January date = GMT. 10:00 GMT must still open the window.
    assert rps.in_window(datetime(2026, 1, 15, 10, 0, tzinfo=LONDON)) is True
    assert rps.in_window(datetime(2026, 1, 15, 9, 59, tzinfo=LONDON)) is False


def test_window_converts_from_utc():
    # 09:30 UTC in June == 10:30 BST → inside the window.
    now_utc = datetime(2026, 6, 5, 9, 30, tzinfo=ZoneInfo("UTC"))
    assert rps.in_window(now_utc) is True
    # 17:30 UTC in June == 18:30 BST → outside.
    assert rps.in_window(datetime(2026, 6, 5, 17, 30, tzinfo=ZoneInfo("UTC"))) is False


# ── reconcile decisions (capability: drives the real ensure_state) ───────


class _Calls:
    def __init__(self):
        self.actions = []


@pytest.fixture
def fake_api(monkeypatch):
    """Patch the module's REST layer; record which endpoints get hit."""
    calls = _Calls()
    state = {"status": "EXITED"}

    async def _fake_status():
        calls.actions.append("status")
        return state["status"]

    async def _fake_start():
        calls.actions.append("start")
        state["status"] = "RUNNING"
        return {"ok": True}

    async def _fake_stop():
        calls.actions.append("stop")
        state["status"] = "EXITED"
        return {"ok": True}

    monkeypatch.setattr(rps, "get_pod_status", _fake_status)
    monkeypatch.setattr(rps, "start_pod", _fake_start)
    monkeypatch.setattr(rps, "stop_pod", _fake_stop)
    calls.state = state
    return calls


@pytest.mark.asyncio
async def test_in_window_and_stopped_starts_pod(fake_api):
    now = datetime(2026, 6, 5, 11, 0, tzinfo=LONDON)
    fake_api.state["status"] = "EXITED"
    assert await rps.ensure_state(now) == "started"
    assert fake_api.actions == ["status", "start"]


@pytest.mark.asyncio
async def test_in_window_and_running_noop(fake_api):
    now = datetime(2026, 6, 5, 11, 0, tzinfo=LONDON)
    fake_api.state["status"] = "RUNNING"
    assert await rps.ensure_state(now) == "noop"
    assert fake_api.actions == ["status"]


@pytest.mark.asyncio
async def test_out_of_window_and_running_stops_pod(fake_api):
    now = datetime(2026, 6, 5, 19, 0, tzinfo=LONDON)
    fake_api.state["status"] = "RUNNING"
    assert await rps.ensure_state(now) == "stopped"
    assert fake_api.actions == ["status", "stop"]


@pytest.mark.asyncio
async def test_out_of_window_and_stopped_noop(fake_api):
    now = datetime(2026, 6, 5, 19, 0, tzinfo=LONDON)
    fake_api.state["status"] = "EXITED"
    assert await rps.ensure_state(now) == "noop"
    assert fake_api.actions == ["status"]


@pytest.mark.asyncio
async def test_unconfigured_is_harmless(monkeypatch, fake_api):
    monkeypatch.delenv("RUNPOD_API_KEY", raising=False)
    assert await rps.ensure_state() == "disabled"
    assert fake_api.actions == []  # never touches the API
    assert rps.get_status()["configured"] is False


@pytest.mark.asyncio
async def test_api_failure_wires_gap_and_reports_error(monkeypatch):
    async def _boom():
        raise RuntimeError("runpod 503")

    monkeypatch.setattr(rps, "get_pod_status", _boom)
    gaps = []
    monkeypatch.setattr(
        "aria_service.intel.engine_wiring.wire_failure",
        lambda **kw: gaps.append(kw),
    )
    now = datetime(2026, 6, 5, 11, 0, tzinfo=LONDON)
    assert await rps.ensure_state(now) == "error"
    assert gaps and gaps[0]["module"] == "runpod_scheduler"  # §21a: failure reaches the brain
    assert "503" in rps.get_status()["last"]["error"]


def test_rest_paths_are_pod_scoped(monkeypatch):
    """The REST layer must hit /pods/{id}(/start|/stop) on the configured base."""
    seen = []

    class _Resp:
        status_code = 200
        text = "{}"
        def raise_for_status(self): pass
        def json(self): return {"desiredStatus": "RUNNING"}

    class _Client:
        def __init__(self, **kw): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def request(self, method, url, headers=None):
            seen.append((method, url))
            assert headers["Authorization"] == "Bearer test-key"
            return _Resp()

    import httpx
    monkeypatch.setattr(httpx, "AsyncClient", _Client)
    asyncio.run(rps.get_pod_status())
    asyncio.run(rps.start_pod())
    asyncio.run(rps.stop_pod())
    base = "https://rest.runpod.io/v1"
    assert seen == [
        ("GET", f"{base}/pods/pod-test-123"),
        ("POST", f"{base}/pods/pod-test-123/start"),
        ("POST", f"{base}/pods/pod-test-123/stop"),
    ]


# ── eval export (runbook prerequisite) ───────────────────────────────────


def test_export_500q_jsonl(tmp_path):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts" / "train"))
    from export_eval_500q import export

    out = tmp_path / "aria_eval_500q.jsonl"
    stats = export(out)
    assert stats["written"] >= 500, f"golden set should be >=500, got {stats['written']}"
    lines = out.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == stats["written"]
    sample = [json.loads(l) for l in lines[:50]]
    for row in sample:
        assert row["question"]
        assert isinstance(row["expected_keywords"], list)
        assert row["topic"]
    # keyword coverage must be strong across the set, and thin entries
    # are REPORTED (no silent quality cliff)
    assert stats["thin_keyword_entries"] <= stats["written"] * 0.10, (
        f"{stats['thin_keyword_entries']} entries have <3 keywords — "
        "keyword derivation too weak for a meaningful eval"
    )


def test_derive_keywords_quality():
    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts" / "train"))
    from export_eval_500q import derive_keywords

    kws = derive_keywords(
        "[CONFIRMED] The OFAC SDN list is maintained by the US Treasury. "
        "ITAR controls defence exports; range approx 150 km."
    )
    assert "OFAC" in kws and "ITAR" in kws
    assert not any("[CONFIRMED]" in k for k in kws)
