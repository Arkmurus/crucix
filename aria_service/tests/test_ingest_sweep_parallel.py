"""Regression guard for /api/aria/ingest neural-learning parallelization.

Live observation 2026-04-27 17:35:23-17:35:34: a single sweep with 5
signals + 4 news items fired 9 sequential DeepSeek calls and held the
ingest connection open for 12 seconds. Each call took ~1.3s on
DeepSeek; sequential summed to a 12s blocking window for the seenode
sweep loop, risking proxy timeouts.

Fix: gather all learn_from_text calls under an asyncio.Semaphore(5)
so up to 5 LLM extractions run concurrently. Per-item try/except so
one slow/bad item can't head-of-line-block the rest.

This test verifies the parallelization actually parallelizes: 10 mock
LLM calls each sleeping 100ms must complete in well under 1 second
total (with sem=5 and 10 calls, ~200ms expected; sequential would be
1000ms).
"""
from __future__ import annotations

import asyncio
import time
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client():
    """Build the test client lazily so module-level imports don't pay
    the full lifespan cost when this file is just collected."""
    from aria_service.main import app
    return TestClient(app)


def _bearer_headers() -> dict:
    """Match the require_aria_token expectation. The dev token is fine
    because tests bypass any production HMAC env var."""
    import os
    token = os.getenv("ARIA_API_TOKEN", "dev-token")
    return {"Authorization": f"Bearer {token}"}


def test_ingest_parallelizes_neural_learn_calls(client, monkeypatch):
    """The 12-call burst from the live log must collapse to ~Tslow rather
    than Nslow×Tslow. With 10 items and sem=5, two batches of 5 ⇒ ~2×slow."""
    import os
    monkeypatch.setenv("ARIA_API_TOKEN", "test-token-parallel")

    call_log: list[float] = []
    SLOW_MS = 100

    async def fake_learn(text, source="conversation", confidence="ASSESSED", llm=None):
        call_log.append(time.monotonic())
        await asyncio.sleep(SLOW_MS / 1000.0)
        return {"neurons_activated": 1, "connections_formed": 0}

    # Patch every collaborator the route touches so the test only measures
    # the parallelism of the learn loop, not the rest of the pipeline.
    with patch("aria_service.main.neural_memory.learn_from_text", side_effect=fake_learn), \
         patch("aria_service.main.intel_ledger.ingest_sweep_signals",
               new=AsyncMock(return_value=0)), \
         patch("aria_service.main.competitors.scan_for_moves",
               new=AsyncMock(return_value=0)), \
         patch("aria_service.main.proactive.anomaly_watch",
               new=AsyncMock(return_value=0)):

        payload = {
            "signals": [{"text": f"signal {i}"} for i in range(10)],
            "news": [],
        }
        t0 = time.monotonic()
        r = client.post(
            "/api/aria/ingest",
            json=payload,
            headers={"Authorization": "Bearer test-token-parallel"},
        )
        elapsed = time.monotonic() - t0

    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is True
    assert body["neurons_activated"] == 10
    # 10 items, sem=5, each takes 100ms ⇒ ~200ms parallel; sequential 1000ms.
    # Generous 600ms ceiling absorbs FastAPI/test-client overhead while still
    # failing if someone reverts to a serial loop.
    assert elapsed < 0.6, (
        f"ingest took {elapsed:.2f}s — expected <0.6s for 10×{SLOW_MS}ms "
        "calls under sem=5. Did the parallelization regress to serial?"
    )
    assert len(call_log) == 10


def test_ingest_one_failed_item_does_not_kill_others(client, monkeypatch):
    """If one learn_from_text raises, the other 9 must still count."""
    monkeypatch.setenv("ARIA_API_TOKEN", "test-token-resilient")

    call_count = {"n": 0}

    async def flaky_learn(text, source="conversation", confidence="ASSESSED", llm=None):
        call_count["n"] += 1
        if "boom" in text:
            raise RuntimeError("simulated extraction failure")
        await asyncio.sleep(0.01)
        return {"neurons_activated": 1, "connections_formed": 0}

    with patch("aria_service.main.neural_memory.learn_from_text", side_effect=flaky_learn), \
         patch("aria_service.main.intel_ledger.ingest_sweep_signals",
               new=AsyncMock(return_value=0)), \
         patch("aria_service.main.competitors.scan_for_moves",
               new=AsyncMock(return_value=0)), \
         patch("aria_service.main.proactive.anomaly_watch",
               new=AsyncMock(return_value=0)):

        payload = {
            "signals": [
                {"text": "signal 1"},
                {"text": "boom"},          # this one raises
                {"text": "signal 3"},
            ],
            "news": [
                {"title": "headline 1", "summary": "summary"},
                {"title": "boom-news", "summary": ""},  # this one raises
            ],
        }
        r = client.post(
            "/api/aria/ingest",
            json=payload,
            headers={"Authorization": "Bearer test-token-resilient"},
        )

    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert call_count["n"] == 5
    # 5 items in flight, 2 raised, 3 succeeded → 3 neurons counted.
    assert body["neurons_activated"] == 3


def test_ingest_with_no_neural_payload_returns_zero(client, monkeypatch):
    """Empty signals + news must not error — the gather list is empty."""
    monkeypatch.setenv("ARIA_API_TOKEN", "test-token-empty")
    with patch("aria_service.main.neural_memory.learn_from_text",
               new=AsyncMock(return_value={"neurons_activated": 99})), \
         patch("aria_service.main.intel_ledger.ingest_sweep_signals",
               new=AsyncMock(return_value=2)), \
         patch("aria_service.main.competitors.scan_for_moves",
               new=AsyncMock(return_value=1)), \
         patch("aria_service.main.proactive.anomaly_watch",
               new=AsyncMock(return_value=0)):

        r = client.post(
            "/api/aria/ingest",
            json={"some_other_key": "value"},
            headers={"Authorization": "Bearer test-token-empty"},
        )

    assert r.status_code == 200
    body = r.json()
    assert body["neurons_activated"] == 0
    assert body["ledger_signals_added"] == 2
    assert body["competitor_moves_added"] == 1
