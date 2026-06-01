"""R-F1251 — Tests for the ARIA client learning sync endpoints."""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from aria_service.main import app


client = TestClient(app)


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
    })
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
    })
    assert resp.status_code == 200
    data = resp.json()
    assert data["received"] == 0


def test_learning_sync_no_interactions_key():
    """POST /api/aria/learning/sync should handle missing interactions key."""
    resp = client.post("/api/aria/learning/sync", json={
        "client_id": "test_client_001",
    })
    assert resp.status_code == 200
    data = resp.json()
    assert data["received"] == 0


def test_learning_updates_returns_patterns():
    """GET /api/aria/learning/updates should return patterns."""
    resp = client.get("/api/aria/learning/updates", params={
        "client_id": "test_client_001",
        "client_version": "2.0.0",
    })
    assert resp.status_code == 200
    data = resp.json()
    assert "patterns" in data
    assert "version" in data
    assert data["version"] == "2.0.0"
    assert data["client_id"] == "test_client_001"


def test_learning_updates_no_params():
    """GET /api/aria/learning/updates should work without params."""
    resp = client.get("/api/aria/learning/updates")
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
        })
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
        })
        assert resp.status_code == 200
        data = resp.json()
        assert "analysis" in data
        assert "fixes" in data
