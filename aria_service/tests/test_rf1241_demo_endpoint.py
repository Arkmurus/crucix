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


def test_download_endpoint_returns_bat_file():
    """The download endpoint should return a .bat file."""
    resp = client.get("/download")
    assert resp.status_code == 200
    assert "application/octet-stream" in resp.headers.get("content-type", "")
    assert "ARIA_Launcher" in resp.headers.get("content-disposition", "")
    assert "@echo off" in resp.text
    assert "aria-intel.fly.dev" in resp.text or "github.com/Arkmurus" in resp.text


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
    # Verify aria.bat content
    bat_content = zf.read([n for n in names if n.endswith("aria.bat")][0]).decode()
    assert "@echo off" in bat_content
    assert "aria_service.main:app" in bat_content


def test_demo_page_served_at_root():
    """The demo HTML page should be served at the root URL."""
    resp = client.get("/")
    assert resp.status_code == 200
    assert "text/html" in resp.headers.get("content-type", "")
    assert "ARIA" in resp.text
    assert "Coder Playground" in resp.text
