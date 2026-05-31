"""R-F1241 — Tests for the ARIA demo endpoint."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from aria_service.main import app


client = TestClient(app)


def test_demo_endpoint_returns_plan_and_code():
    """The demo endpoint should return a plan and generated code."""
    resp = client.post("/api/aria/coder/demo", json={
        "description": "Add error handling to process_item to catch exceptions",
        "code": "def process_item(data):\n    result = data[\"value\"] * 2\n    return result\n",
    })
    assert resp.status_code == 200
    data = resp.json()
    assert "plan" in data
    assert "code" in data
    assert data["plan"]["title"] is not None
    assert data["plan"]["approach"] is not None
    assert len(data["code"]) > 0


def test_demo_endpoint_requires_description():
    """The demo endpoint should reject requests without a description."""
    resp = client.post("/api/aria/coder/demo", json={})
    assert resp.status_code == 400


def test_demo_endpoint_works_with_default_code():
    """The demo endpoint should work with default code when none provided."""
    resp = client.post("/api/aria/coder/demo", json={
        "description": "Add error handling to process_item",
    })
    assert resp.status_code == 200
    data = resp.json()
    assert "code" in data
    assert len(data["code"]) > 0


def test_demo_page_served_at_root():
    """The demo HTML page should be served at the root URL."""
    resp = client.get("/")
    assert resp.status_code == 200
    assert "text/html" in resp.headers.get("content-type", "")
    assert "ARIA" in resp.text
    assert "Coder Playground" in resp.text
