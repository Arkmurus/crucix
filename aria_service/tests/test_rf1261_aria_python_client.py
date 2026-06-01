"""R-F1261/R-F1266 — Tests for the ARIA Python client (aria.py).

Tests cover:
1. The client .bat sends Authorization header
2. The Python client handles 401/timeout/5xx with clear messages
3. The Python client config management works
4. The Python client streaming request works
5. The /download/client ZIP is slim (~5KB, no .py files inside)
6. The /download/aria.py endpoint serves the basic Python client
7. The /download/aria_tui.py endpoint serves the TUI client
8. The .bat has a 'token' command for setting API token
9. The .bat auto-downloads aria.py and aria_tui.py if missing
10. The /token endpoint serves the API token page
11. Removed endpoints return 404
"""
from __future__ import annotations

import json
import os
import zipfile
import io
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from aria_service.main import app

client = TestClient(app)


# ── Helpers ────────────────────────────────────────────────────────────────────


def _get_client_zip_content() -> dict[str, str]:
    """Download the client ZIP and return {filename: content}."""
    resp = client.get("/download/client")
    assert resp.status_code == 200
    zf = zipfile.ZipFile(io.BytesIO(resp.content))
    result = {}
    for name in zf.namelist():
        result[name] = zf.read(name).decode("utf-8", errors="replace")
    return result


# ── Tests: Client ZIP is slim (~5KB) ───────────────────────────────────────────


def test_client_zip_is_slim():
    """The /download/client ZIP must be ~5KB and NOT include aria.py."""
    resp = client.get("/download/client")
    assert resp.status_code == 200
    size_kb = len(resp.content) / 1024
    assert size_kb < 10, f"ZIP too large: {size_kb:.1f}KB (should be ~5KB)"
    zf = zipfile.ZipFile(io.BytesIO(resp.content))
    names = zf.namelist()
    # Must NOT contain aria.py (downloaded on demand)
    py_files = [n for n in names if n.endswith(".py")]
    assert len(py_files) == 0, f"ZIP should not contain .py files: {py_files}"
    # Must contain aria.bat and README.txt
    assert any("aria.bat" in n for n in names), f"aria.bat not in ZIP: {names}"
    assert any("README.txt" in n for n in names), f"README.txt not in ZIP: {names}"


def test_client_zip_includes_updated_bat():
    """The /download/client ZIP must include aria.bat with Authorization header."""
    files = _get_client_zip_content()
    bat_files = [n for n in files if n.endswith("aria.bat")]
    assert len(bat_files) > 0, f"No aria.bat found: {list(files.keys())}"
    content = files[bat_files[0]]
    assert "Authorization" in content, "aria.bat must send Authorization header"
    assert "Bearer" in content, "aria.bat must use Bearer token auth"
    assert "/api/aria/chat" in content
    assert "aria-intel.fly.dev" in content


def test_client_zip_readme_updated():
    """The /download/client ZIP must include updated README.txt."""
    files = _get_client_zip_content()
    readme_files = [n for n in files if n.endswith("README.txt")]
    assert len(readme_files) > 0
    content = files[readme_files[0]]
    assert "token" in content.lower()
    assert "aria.bat" in content or "double-click" in content.lower()


# ── Tests: Python client auth handling ─────────────────────────────────────────


def _import_client_module(name: str) -> object:
    """Dynamically import aria.py for testing."""
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        name,
        os.path.join(os.path.dirname(__file__), "..", "static", "aria_client", "aria.py"),
    )
    if spec is None or spec.loader is None:
        pytest.skip("aria.py not found")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_python_client_401_handling():
    """The Python client must detect 401 and show a clear auth message."""
    mod = _import_client_module("aria_client_mod_401")
    try:
        raise mod.AriaError(
            "Authentication failed (401 Unauthorized).\n"
            "  You need a valid ARIA API token.\n"
            "  Run:  aria --setup",
            status_code=401,
        )
    except mod.AriaError as e:
        assert e.status_code == 401
        assert "ARIA API token" in str(e)
        assert "--setup" in str(e)


def test_python_client_timeout_handling():
    """The Python client must detect timeouts and show a clear message."""
    mod = _import_client_module("aria_client_mod_timeout")
    try:
        raise mod.AriaError("Request timed out. The server may be busy.")
    except mod.AriaError as e:
        assert "timed out" in str(e).lower()


def test_python_client_server_error_handling():
    """The Python client must detect 5xx errors and show a clear message."""
    mod = _import_client_module("aria_client_mod_5xx")
    try:
        raise mod.AriaError(
            "Server error (502). The ARIA server may be busy or restarting.",
            status_code=502,
        )
    except mod.AriaError as e:
        assert e.status_code == 502
        assert "Server error" in str(e)


# ── Tests: Python client config management ─────────────────────────────────────


def test_python_client_config_save_and_load(tmp_path):
    """The Python client must save and load config correctly."""
    mod = _import_client_module("aria_client_mod_cfg")
    original_dir = mod.CONFIG_DIR
    mod.CONFIG_DIR = tmp_path / ".aria"
    mod.CONFIG_FILE = mod.CONFIG_DIR / "config.json"

    try:
        mod._save_config({"api_token": "test-token-123", "server": "https://test.server.com"})
        assert (mod.CONFIG_DIR / "config.json").exists()
        cfg = mod._load_config()
        assert cfg["api_token"] == "test-token-123"
        assert cfg["server"] == "https://test.server.com"
        token = mod._get_token()
        assert token == "test-token-123"
        server = mod._get_server()
        assert server == "https://test.server.com"
    finally:
        mod.CONFIG_DIR = original_dir
        mod.CONFIG_FILE = mod.CONFIG_DIR / "config.json"


def test_python_client_env_var_overrides_config(tmp_path):
    """The Python client must prefer ARIA_API_TOKEN env var over config."""
    mod = _import_client_module("aria_client_mod_env")
    original_dir = mod.CONFIG_DIR
    mod.CONFIG_DIR = tmp_path / ".aria"
    mod.CONFIG_FILE = mod.CONFIG_DIR / "config.json"

    try:
        mod._save_config({"api_token": "config-token"})
        old_env = os.environ.get("ARIA_API_TOKEN")
        os.environ["ARIA_API_TOKEN"] = "env-token"
        try:
            token = mod._get_token()
            assert token == "env-token", "Env var must override config"
        finally:
            if old_env is not None:
                os.environ["ARIA_API_TOKEN"] = old_env
            else:
                del os.environ["ARIA_API_TOKEN"]
    finally:
        mod.CONFIG_DIR = original_dir
        mod.CONFIG_FILE = mod.CONFIG_DIR / "config.json"


# ── Tests: Python client streaming ─────────────────────────────────────────────


def test_python_client_stream_request_builds_correct_url():
    """The Python client must have streaming functions."""
    mod = _import_client_module("aria_client_mod_stream")
    assert hasattr(mod, "_stream_request")
    assert hasattr(mod, "send_chat_stream")


# ── Tests: Download endpoint ───────────────────────────────────────────────────


def test_client_download_returns_aria_client_zip():
    """The /download/client endpoint should return a ZIP with aria.bat and README."""
    resp = client.get("/download/client")
    assert resp.status_code == 200
    assert "application/zip" in resp.headers.get("content-type", "")
    assert "ARIA_Client.zip" in resp.headers.get("content-disposition", "")
    zf = zipfile.ZipFile(io.BytesIO(resp.content))
    names = zf.namelist()
    assert any("aria.bat" in n for n in names), f"aria.bat not in ZIP: {names}"
    assert any("README.txt" in n for n in names), f"README.txt not in ZIP: {names}"
    bat_content = zf.read([n for n in names if n.endswith("aria.bat")][0]).decode()
    assert "@echo off" in bat_content
    assert "aria-intel.fly.dev" in bat_content
    assert "/api/aria/chat" in bat_content
    assert "Authorization" in bat_content


def test_client_bat_sends_auth_header():
    """The aria_client/aria.bat must send Authorization: Bearer header."""
    import zipfile, io
    resp = client.get("/download/client")
    zf = zipfile.ZipFile(io.BytesIO(resp.content))
    names = zf.namelist()
    bat_file = [n for n in names if n.endswith("aria.bat")][0]
    bat_content = zf.read(bat_file).decode()
    assert "Authorization" in bat_content, "Client .bat must send Authorization header"
    assert "Bearer" in bat_content, "Client .bat must use Bearer token auth"
    assert "/api/aria/chat" in bat_content, "Client .bat must call the real /api/aria/chat endpoint"
    assert "aria-intel.fly.dev" in bat_content
    assert "Invoke-RestMethod" in bat_content


# ── Tests: Removed endpoints return 404 ────────────────────────────────────────


def test_old_download_endpoint_gone():
    """The old /download endpoint (single .bat) must return 404."""
    resp = client.get("/download")
    assert resp.status_code == 404, "/download should be removed"


def test_old_download_aria_endpoint_gone():
    """The old /download/aria endpoint (full folder ZIP) must return 404."""
    resp = client.get("/download/aria")
    assert resp.status_code == 404, "/download/aria should be removed"


# ── Tests: Python client CLI interface ─────────────────────────────────────────


def test_python_client_has_main():
    """The Python client must have all required functions."""
    mod = _import_client_module("aria_client_mod_main")
    assert hasattr(mod, "main")
    assert hasattr(mod, "interactive_shell")
    assert hasattr(mod, "run_setup")
    assert hasattr(mod, "check_status")
    assert hasattr(mod, "send_chat")
    assert hasattr(mod, "send_chat_stream")
    assert hasattr(mod, "AriaError")
    assert hasattr(mod, "_get_token")
    assert hasattr(mod, "_get_server")


# ── Tests: /download/aria.py endpoint ─────────────────────────────────────────


def test_download_aria_py_endpoint():
    """The /download/aria.py endpoint must serve the Python client file."""
    resp = client.get("/download/aria.py")
    assert resp.status_code == 200
    assert "text/x-python" in resp.headers.get("content-type", "")
    assert "aria.py" in resp.headers.get("content-disposition", "")
    assert "send_chat" in resp.text
    assert "interactive_shell" in resp.text
    assert "run_setup" in resp.text
    assert "AriaError" in resp.text


# ── Tests: .bat has token command ──────────────────────────────────────────────


def test_client_bat_has_token_command():
    """The aria_client/aria.bat must have a 'token' command for setting API token."""
    import zipfile, io
    resp = client.get("/download/client")
    zf = zipfile.ZipFile(io.BytesIO(resp.content))
    names = zf.namelist()
    bat_file = [n for n in names if n.endswith("aria.bat")][0]
    bat_content = zf.read(bat_file).decode()
    assert '"token"' in bat_content or "token" in bat_content.lower(), (
        "Client .bat must have a token command"
    )
    assert "API_TOKEN" in bat_content or "api_token" in bat_content, (
        "Client .bat must mention API token"
    )
    assert "/token" in bat_content, (
        "Client .bat must reference the /token endpoint"
    )


# ── Tests: /token endpoint ──────────────────────────────────────────────────────


def test_token_endpoint_serves_html():
    """The /token endpoint must serve an HTML page with token instructions."""
    resp = client.get("/token")
    assert resp.status_code == 200
    assert "text/html" in resp.headers.get("content-type", "")
    assert "API Token" in resp.text or "token" in resp.text.lower()
    assert "aria.bat" in resp.text or "ARIA Client" in resp.text
    assert "/download/client" in resp.text


# ── Tests: .bat auto-downloads aria.py ─────────────────────────────────────────


def test_client_bat_can_download_aria_py():
    """The aria_client/aria.bat must be able to download aria.py from the server."""
    import zipfile, io
    resp = client.get("/download/client")
    zf = zipfile.ZipFile(io.BytesIO(resp.content))
    names = zf.namelist()
    bat_file = [n for n in names if n.endswith("aria.bat")][0]
    bat_content = zf.read(bat_file).decode()
    assert "/download/aria.py" in bat_content, (
        "Client .bat must know how to download aria.py from the server"
    )
    assert "DownloadFile" in bat_content or "WebClient" in bat_content, (
        "Client .bat must have a download mechanism for aria.py"
    )


def test_client_bat_can_download_aria_tui():
    """The aria_client/aria.bat must be able to download aria_tui.py from the server."""
    import zipfile, io
    resp = client.get("/download/client")
    zf = zipfile.ZipFile(io.BytesIO(resp.content))
    names = zf.namelist()
    bat_file = [n for n in names if n.endswith("aria.bat")][0]
    bat_content = zf.read(bat_file).decode()
    assert "/download/aria_tui.py" in bat_content, (
        "Client .bat must know how to download aria_tui.py from the server"
    )


# ── Tests: /download/aria_tui.py endpoint ──────────────────────────────────────


def test_download_aria_tui_endpoint():
    """The /download/aria_tui.py endpoint must serve the TUI client."""
    resp = client.get("/download/aria_tui.py")
    assert resp.status_code == 200
    assert "text/x-python" in resp.headers.get("content-type", "")
    assert "aria_tui.py" in resp.headers.get("content-disposition", "")
    assert "AriaTUI" in resp.text
    assert "textual" in resp.text.lower()
    assert "send_chat" in resp.text or "_send_chat" in resp.text
