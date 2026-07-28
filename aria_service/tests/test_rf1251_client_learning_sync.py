"""R-F1251 — Tests for the ARIA client learning sync endpoints.

R-F3326 — these called /learning/sync UNAUTHENTICATED and had been failing 401
ever since R-F1347 (2026-06-05) put `Depends(require_aria_token)` on it. That was
deliberate security hardening: R-F1251 had made an unauthenticated brain WRITE
endpoint public, and R-F1347 removed it from the public bypass (see the comment at
routes/aria.py:392).

So the ENDPOINT is right and these tests were wrong. Making them green by dropping
the dependency would have reopened the hole - the reason to check which side is
actually wrong before touching either. No production caller exists outside the
route, so nothing was broken live.

The auth requirement is now asserted explicitly below, so a future change that
makes this endpoint public again fails here instead of shipping.
"""
from __future__ import annotations

import os
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from aria_service.main import app


client = TestClient(app)


_TEST_TOKEN = "rf3326-test-token"


@pytest.fixture(autouse=True)
def _token(monkeypatch):
    """Give require_aria_token something to accept.

    _accepted_tokens() (routes/aria.py:349) reads the token env vars at CALL time
    and keeps only truthy ones, so an unset environment accepts nothing and every
    request is 401.

    monkeypatch, NOT os.environ at module scope: a module-level env write is a
    process-global mutation no fixture undoes, which is the R-F2801 anti-pattern
    documented in test_rf1498's header (it leaked into every later test in the run).
    """
    monkeypatch.setenv("ARIA_API_TOKEN", _TEST_TOKEN)


def _auth() -> dict:
    """Match require_aria_token."""
    return {"Authorization": f"Bearer {_TEST_TOKEN}"}


def test_learning_sync_accepts_interactions():
    """POST /api/aria/learning/sync should accept interaction data."""
    resp = client.post("/api/aria/learning/sync", json={
        "interactions": [
            {
                "query": "What is Python?",
                "response": "Python is a programming language.",
                "model_used": "deepseek-chat",
                "tokens_used": 50,
                "cost": 0.00005,
                "platform": "Windows",
            }
        ],
        "client_id": "test_client_001",
        "client_version": "2.0.0",
    }, headers=_auth())
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"
    assert data["received"] == 1


def test_learning_sync_empty_interactions():
    """POST /api/aria/learning/sync should handle empty interactions."""
    resp = client.post("/api/aria/learning/sync", json={
        "interactions": [],
        "client_id": "test_client_001",
        "client_version": "2.0.0",
    }, headers=_auth())
    assert resp.status_code == 200
    data = resp.json()
    assert data["received"] == 0


def test_learning_sync_no_interactions_key():
    """POST /api/aria/learning/sync should handle missing interactions key."""
    resp = client.post("/api/aria/learning/sync", json={
        "client_id": "test_client_001",
    }, headers=_auth())
    assert resp.status_code == 200
    data = resp.json()
    assert data["received"] == 0


def test_learning_updates_returns_patterns():
    """GET /api/aria/learning/updates should return patterns."""
    resp = client.get("/api/aria/learning/updates", params={
        "client_id": "test_client_001",
        "client_version": "2.0.0",
    }, headers=_auth())
    assert resp.status_code == 200
    data = resp.json()
    assert "patterns" in data
    assert "version" in data
    assert data["version"] == "2.0.0"
    assert data["client_id"] == "test_client_001"


def test_learning_updates_no_params():
    """GET /api/aria/learning/updates should work without params."""
    resp = client.get("/api/aria/learning/updates", headers=_auth())
    assert resp.status_code == 200
    data = resp.json()
    assert "patterns" in data
    assert "count" in data


def test_client_chat_via_client_endpoint():
    """The client should be able to chat via the /api/aria/client/chat endpoint.

    This tests the proxying from the client endpoint to the real chat engine.
    """
    from aria_service.routes.aria import chat_ep

    mock_response = {
        "response": "ARIA intelligence at work!",
        "session_id": "client_test",
        "tool_used": "web_search",
    }

    with patch("aria_service.routes.aria.chat_ep", new=AsyncMock(return_value=mock_response)):
        resp = client.post("/api/aria/client/chat", json={
            "message": "Research quantum computing",
            "user": "test",
        }, headers=_auth())
        assert resp.status_code == 200
        data = resp.json()
        assert data["response"] == "ARIA intelligence at work!"
        assert data["tool_used"] == "web_search"


def test_client_analyse_via_client_endpoint():
    """The client should be able to analyse code via the /api/aria/client/analyse endpoint."""
    from aria_service.routes.aria import chat_ep

    mock_response = {
        "response": "Analysis: The code divides by zero.",
        "session_id": "client_analyse",
    }

    with patch("aria_service.routes.aria.chat_ep", new=AsyncMock(return_value=mock_response)):
        resp = client.post("/api/aria/client/analyse", json={
            "code": "def foo():\n    return 1/0\n",
        }, headers=_auth())
        assert resp.status_code == 200
        data = resp.json()
        assert "analysis" in data
        assert "fixes" in data


def test_rf3326_the_endpoint_still_requires_auth():
    """R-F1347's property, pinned. /learning/sync is a brain WRITE.

    It was public under R-F1251 and R-F1347 closed that. If a future change makes
    it unauthenticated again, this fails rather than silently shipping an open
    write path into the brain.
    """
    resp = client.post("/api/aria/learning/sync", json={"interactions": []})
    assert resp.status_code == 401, (
        f"/learning/sync must reject unauthenticated writes; got {resp.status_code}"
    )
