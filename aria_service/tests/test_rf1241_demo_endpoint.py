"""R-F1241 — Tests for the ARIA demo endpoint and client endpoints."""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

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


def test_download_endpoint_returns_bat_file():
    """The download endpoint should return a .bat file."""
    resp = client.get("/download")
    assert resp.status_code == 200
    assert "application/octet-stream" in resp.headers.get("content-type", "")
    assert "ARIA_Launcher" in resp.headers.get("content-disposition", "")
    assert "@echo off" in resp.text
    assert "aria-intel.fly.dev" in resp.text


def test_zip_download_returns_aria_folder():
    """The /download/aria endpoint should return a ZIP with aria.bat."""
    resp = client.get("/download/aria")
    assert resp.status_code == 200
    assert "application/zip" in resp.headers.get("content-type", "")
    assert "ARIA.zip" in resp.headers.get("content-disposition", "")
    # Verify it's a valid ZIP with aria.bat inside
    import zipfile, io
    zf = zipfile.ZipFile(io.BytesIO(resp.content))
    names = zf.namelist()
    assert any("aria.bat" in n for n in names), f"aria.bat not in ZIP: {names}"
    assert any("README.txt" in n for n in names), f"README.txt not in ZIP: {names}"
    # Verify aria.bat content — should reference the live server
    bat_content = zf.read([n for n in names if n.endswith("aria.bat")][0]).decode()
    assert "@echo off" in bat_content
    assert "aria-intel.fly.dev" in bat_content


def test_client_download_returns_aria_client_zip():
    """The /download/client endpoint should return a ZIP with aria.bat."""
    resp = client.get("/download/client")
    assert resp.status_code == 200
    assert "application/zip" in resp.headers.get("content-type", "")
    assert "ARIA_Client.zip" in resp.headers.get("content-disposition", "")
    import zipfile, io
    zf = zipfile.ZipFile(io.BytesIO(resp.content))
    names = zf.namelist()
    assert any("aria.bat" in n for n in names), f"aria.bat not in ZIP: {names}"
    bat_content = zf.read([n for n in names if n.endswith("aria.bat")][0]).decode()
    assert "@echo off" in bat_content
    assert "aria-intel.fly.dev" in bat_content
    # Verify it calls the real /api/aria/chat endpoint, not the canned one
    assert "/api/aria/chat" in bat_content


def test_client_chat_proxies_to_real_engine():
    """The client chat endpoint should proxy to the real ARIA chat engine.

    We mock chat_ep to verify the proxying works without needing the
    full LLM stack.
    """
    mock_response = {"response": "Hello from ARIA's real engine!", "session_id": "client_test"}

    with patch("aria_service.routes.aria.chat_ep", new=AsyncMock(return_value=mock_response)):
        resp = client.post("/api/aria/client/chat", json={"message": "hello", "user": "test"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["response"] == "Hello from ARIA's real engine!"


def test_client_chat_requires_message():
    """The client chat endpoint should reject empty messages."""
    resp = client.post("/api/aria/client/chat", json={})
    assert resp.status_code == 400


def test_client_analyse_proxies_to_real_engine():
    """The client analyse endpoint should proxy to the real ARIA chat engine."""
    mock_response = {
        "response": "Analysis: The code has a missing error handler.\nFix: Add try/except.",
        "session_id": "client_analyse",
    }

    with patch("aria_service.routes.aria.chat_ep", new=AsyncMock(return_value=mock_response)):
        resp = client.post("/api/aria/client/analyse", json={
            "code": "def foo():\n    pass\n"
        })
        assert resp.status_code == 200
        data = resp.json()
        assert "analysis" in data
        assert "fixes" in data
        assert len(data["analysis"]) > 0


def test_client_analyse_requires_code():
    """The client analyse endpoint should reject empty code."""
    resp = client.post("/api/aria/client/analyse", json={})
    assert resp.status_code == 400


def test_demo_page_served_at_root():
    """The demo HTML page should be served at the root URL."""
    resp = client.get("/")
    assert resp.status_code == 200
    assert "text/html" in resp.headers.get("content-type", "")
    assert "ARIA" in resp.text
    assert "Coder Playground" in resp.text


def test_client_bat_calls_real_chat_endpoint():
    """The aria_client/aria.bat should call /api/aria/chat, not the canned endpoint."""
    import zipfile, io
    resp = client.get("/download/client")
    zf = zipfile.ZipFile(io.BytesIO(resp.content))
    names = zf.namelist()
    bat_file = [n for n in names if n.endswith("aria.bat")][0]
    bat_content = zf.read(bat_file).decode()
    # Must call the real chat endpoint
    assert "/api/aria/chat" in bat_content, (
        "Client .bat must call the real /api/aria/chat endpoint, "
        "not the canned /api/aria/client/chat"
    )
    # Must reference the live server
    assert "aria-intel.fly.dev" in bat_content
    # Must use Invoke-RestMethod (not just canned responses)
    assert "Invoke-RestMethod" in bat_content


def test_aria_folder_bat_calls_real_chat_endpoint():
    """The aria_folder/aria.bat should call /api/aria/chat, not run locally."""
    import zipfile, io
    resp = client.get("/download/aria")
    zf = zipfile.ZipFile(io.BytesIO(resp.content))
    names = zf.namelist()
    bat_file = [n for n in names if n.endswith("aria.bat")][0]
    bat_content = zf.read(bat_file).decode()
    # Must call the real chat endpoint
    assert "/api/aria/chat" in bat_content, (
        "aria_folder .bat must call the real /api/aria/chat endpoint"
    )
    # Must reference the live server
    assert "aria-intel.fly.dev" in bat_content
    # Must NOT try to download the full crucix repo
    assert "github.com/Arkmurus/crucix" not in bat_content, (
        "aria_folder .bat should not download the full repo — "
        "it should connect to the live server instead"
    )
