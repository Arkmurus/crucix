"""R-F973 — ingest_sweep is wired to the brain on success AND failure.

ingest_sweep is the largest Node→brain data path. Pre-R-F973 it returned
counts to the Node tier but emitted NO brain signal on success, and its
parse-failure branches were logger.warning-only (DARK per CLAUDE.md §21a) —
ARIA had no callable signal that a sweep was ingested, nor that one failed.

Capability: a valid sweep absorbs to brain_hook (success); a malformed body
records a capability gap (failure). Both branches now reach the brain.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client():
    from aria_service.main import app
    return TestClient(app)


def test_ingest_sweep_success_records_brain_signal(client, monkeypatch):
    monkeypatch.setenv("ARIA_API_TOKEN", "test-token-rf973")
    signal_calls: list[tuple] = []

    async def fake_signal(module, success=True, sector=""):
        signal_calls.append((module, success))

    with patch("aria_service.main.neural_memory.learn_from_text",
               new=AsyncMock(return_value={"neurons_activated": 1})), \
         patch("aria_service.main.intel_ledger.ingest_sweep_signals",
               new=AsyncMock(return_value=3)), \
         patch("aria_service.main.competitors.scan_for_moves",
               new=AsyncMock(return_value=1)), \
         patch("aria_service.main.proactive.anomaly_watch",
               new=AsyncMock(return_value=0)), \
         patch("aria_service.intel.brain_hook._record_signal", side_effect=fake_signal):
        r = client.post(
            "/api/aria/ingest",
            json={"signals": [{"text": "x"}], "news": [],
                  "meta": {"sourcesOk": 5, "sourcesQueried": 6}},
            headers={"Authorization": "Bearer test-token-rf973"},
        )

    assert r.status_code == 200, r.text
    assert ("ingest_sweep", True) in signal_calls, (
        f"expected a successful ingest_sweep brain signal, got {signal_calls!r}"
    )


def test_ingest_sweep_async_ack_schedules_background_processing(client, monkeypatch):
    """Node sweep ingest should not hold the request open for ledger/neural work.

    The async header is the production path from aria-web. It must acknowledge
    after payload validation and schedule the real ingest processor through the
    background supervisor.
    """
    monkeypatch.setenv("ARIA_API_TOKEN", "test-token-rf2397")
    scheduled: list[str] = []

    async def fake_background(data):
        scheduled.append(data["signals"][0]["text"])

    def fake_bg_task(task, name="", factory=None):
        scheduled.append(name or task.get_name())
        return task

    with patch("aria_service.main._process_sweep_ingest_background", side_effect=fake_background), \
         patch("aria_service.main._bg_task", side_effect=fake_bg_task):
        r = client.post(
            "/api/aria/ingest",
            json={"signals": [{"text": "async sweep signal"}], "news": []},
            headers={
                "Authorization": "Bearer test-token-rf2397",
                "X-ARIA-Ingest-Async": "1",
            },
        )

    assert r.status_code == 200, r.text
    assert r.json() == {"ok": True, "accepted": True, "mode": "async"}
    assert "sweep_ingest_async" in scheduled
    from aria_service.main import app
    assert app.state.current_data["signals"][0]["text"] == "async sweep signal"


def test_ingest_sweep_anomaly_failure_signals_unsuccessful(client, monkeypatch):
    """A partial failure (anomaly_watch raised) must still reach the brain,
    flagged success=False — not be swallowed by the warning-only catch."""
    monkeypatch.setenv("ARIA_API_TOKEN", "test-token-rf973c")
    signal_calls: list[tuple] = []

    async def fake_signal(module, success=True, sector=""):
        signal_calls.append((module, success))

    with patch("aria_service.main.neural_memory.learn_from_text",
               new=AsyncMock(return_value={"neurons_activated": 1})), \
         patch("aria_service.main.intel_ledger.ingest_sweep_signals",
               new=AsyncMock(return_value=0)), \
         patch("aria_service.main.competitors.scan_for_moves",
               new=AsyncMock(return_value=0)), \
         patch("aria_service.main.proactive.anomaly_watch",
               new=AsyncMock(side_effect=RuntimeError("boom"))), \
         patch("aria_service.intel.brain_hook._record_signal", side_effect=fake_signal):
        r = client.post(
            "/api/aria/ingest",
            json={"signals": [{"text": "x"}], "news": []},
            headers={"Authorization": "Bearer test-token-rf973c"},
        )

    assert r.status_code == 200, r.text
    assert ("ingest_sweep", False) in signal_calls, (
        f"anomaly failure should record an unsuccessful signal, got {signal_calls!r}"
    )


def test_ingest_sweep_bad_body_records_capability_gap(client, monkeypatch):
    """A non-dict JSON body (live symptom: WA-shaped payload) was logged and
    400'd silently. It must now record a file_parse capability gap so the
    coder/brain can see sweeps are failing to ingest."""
    monkeypatch.setenv("ARIA_API_TOKEN", "test-token-rf973b")
    gap_calls: list[dict] = []

    async def fake_gap(**kwargs):
        gap_calls.append(kwargs)
        return {}

    with patch("aria_service.intel.capability_gaps.record_gap", side_effect=fake_gap):
        r = client.post(
            "/api/aria/ingest",
            json=["not", "a", "dict"],   # JSON array → expected_dict_body branch
            headers={"Authorization": "Bearer test-token-rf973b"},
        )

    assert r.status_code == 400, r.text
    assert any(c.get("gap_type") == "file_parse" for c in gap_calls), (
        f"malformed sweep body should record a file_parse gap, got {gap_calls!r}"
    )
