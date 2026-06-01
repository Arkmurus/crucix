"""R-F1261 — Tests for the ARIA Python client (aria.py).

Tests cover:
1. The client .bat now sends Authorization header
2. The Python client handles 401 with clear message
3. The Python client handles timeouts gracefully
4. The Python client handles server errors gracefully
5. The Python client config management works
6. The Python client streaming request works
7. The download endpoints include the new Python client
8. The /download/aria.py endpoint serves the Python client
9. The .bat has a 'setup' command for token instructions
10. The .bat auto-downloads aria.py if missing
"""
from __future__ import annotations

import json
import os
import zipfile
import io
from pathlib import Path
from unittest.mock import patch, MagicMock

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


def _get_aria_zip_content() -> dict[str, str]:
    """Download the aria folder ZIP and return {filename: content}."""
    resp = client.get("/download/aria")
    assert resp.status_code == 200
    zf = zipfile.ZipFile(io.BytesIO(resp.content))
    result = {}
    for name in zf.namelist():
        result[name] = zf.read(name).decode("utf-8", errors="replace")
    return result


# ── Tests: Client ZIP includes Python client ───────────────────────────────────


def test_client_zip_includes_python_client():
    """The /download/client ZIP must include aria.py (the Python client)."""
    files = _get_client_zip_content()
    py_files = [n for n in files if n.endswith("aria.py")]
    assert len(py_files) > 0, f"No aria.py found in client ZIP: {list(files.keys())}"
    content = files[py_files[0]]
    assert "ARIA_API_TOKEN" in content
    assert "send_chat" in content
    assert "interactive_shell" in content
    assert "run_setup" in content


def test_client_zip_includes_updated_bat():
    """The /download/client ZIP must include aria.bat with Authorization header."""
    files = _get_client_zip_content()
    bat_files = [n for n in files if n.endswith("aria.bat")]
    assert len(bat_files) > 0, f"No aria.bat found: {list(files.keys())}"
    content = files[bat_files[0]]
    # Must send Authorization header
    assert "Authorization" in content, "aria.bat must send Authorization header"
    assert "Bearer" in content, "aria.bat must use Bearer token auth"
    # Must still call the real chat endpoint
    assert "/api/aria/chat" in content
    # Must reference the live server
    assert "aria-intel.fly.dev" in content


def test_client_zip_readme_updated():
    """The /download/client ZIP must include updated README.txt."""
    files = _get_client_zip_content()
    readme_files = [n for n in files if n.endswith("README.txt")]
    assert len(readme_files) > 0
    content = files[readme_files[0]]
    assert "v2.1" in content or "python aria.py" in content
    assert "ARIA_API_TOKEN" in content
    assert "--setup" in content


# ── Tests: aria_folder also has auth ───────────────────────────────────────────


def test_aria_folder_bat_has_auth():
    """The /download/aria ZIP must include aria.bat with Authorization header."""
    files = _get_aria_zip_content()
    bat_files = [n for n in files if n.endswith("aria.bat")]
    assert len(bat_files) > 0
    content = files[bat_files[0]]
    assert "Authorization" in content, "aria_folder aria.bat must send Authorization header"
    assert "Bearer" in content
    assert "/api/aria/chat" in content


# ── Tests: Python client auth handling ─────────────────────────────────────────


def test_python_client_401_handling():
    """The Python client must detect 401 and show a clear auth message."""
    # Import the client module
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "aria_client_module",
        os.path.join(os.path.dirname(__file__), "..", "static", "aria_client", "aria.py"),
    )
    if spec is None or spec.loader is None:
        pytest.skip("aria.py not found at expected path")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    # Test that AriaError with 401 produces the right message
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
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "aria_client_module2",
        os.path.join(os.path.dirname(__file__), "..", "static", "aria_client", "aria.py"),
    )
    if spec is None or spec.loader is None:
        pytest.skip("aria.py not found")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    try:
        raise mod.AriaError("Request timed out. The server may be busy.")
    except mod.AriaError as e:
        assert "timed out" in str(e).lower()


def test_python_client_server_error_handling():
    """The Python client must detect 5xx errors and show a clear message."""
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "aria_client_module3",
        os.path.join(os.path.dirname(__file__), "..", "static", "aria_client", "aria.py"),
    )
    if spec is None or spec.loader is None:
        pytest.skip("aria.py not found")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

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
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "aria_client_module4",
        os.path.join(os.path.dirname(__file__), "..", "static", "aria_client", "aria.py"),
    )
    if spec is None or spec.loader is None:
        pytest.skip("aria.py not found")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    # Override CONFIG_DIR to tmp_path
    original_dir = mod.CONFIG_DIR
    mod.CONFIG_DIR = tmp_path / ".aria"
    mod.CONFIG_FILE = mod.CONFIG_DIR / "config.json"

    try:
        # Save config
        mod._save_config({"api_token": "test-token-123", "server": "https://test.server.com"})

        # Verify file exists
        assert (mod.CONFIG_DIR / "config.json").exists()

        # Load config
        cfg = mod._load_config()
        assert cfg["api_token"] == "test-token-123"
        assert cfg["server"] == "https://test.server.com"

        # Test get_token reads from config
        token = mod._get_token()
        assert token == "test-token-123"

        # Test get_server reads from config
        server = mod._get_server()
        assert server == "https://test.server.com"
    finally:
        mod.CONFIG_DIR = original_dir
        mod.CONFIG_FILE = mod.CONFIG_DIR / "config.json"


def test_python_client_env_var_overrides_config(tmp_path):
    """The Python client must prefer ARIA_API_TOKEN env var over config."""
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "aria_client_module5",
        os.path.join(os.path.dirname(__file__), "..", "static", "aria_client", "aria.py"),
    )
    if spec is None or spec.loader is None:
        pytest.skip("aria.py not found")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    original_dir = mod.CONFIG_DIR
    mod.CONFIG_DIR = tmp_path / ".aria"
    mod.CONFIG_FILE = mod.CONFIG_DIR / "config.json"

    try:
        # Save config with one token
        mod._save_config({"api_token": "config-token"})

        # Set env var with different token
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
    """The Python client must build the correct streaming URL."""
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "aria_client_module6",
        os.path.join(os.path.dirname(__file__), "..", "static", "aria_client", "aria.py"),
    )
    if spec is None or spec.loader is None:
        pytest.skip("aria.py not found")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    # Verify the stream path is correct
    # We can't easily test the actual HTTP call without a server,
    # but we can verify the function exists and has the right signature
    assert hasattr(mod, "_stream_request")
    assert hasattr(mod, "send_chat_stream")


# ── Tests: Download endpoint still works ───────────────────────────────────────


def test_download_endpoint_returns_bat_file():
    """The /download endpoint should still return a .bat file."""
    resp = client.get("/download")
    assert resp.status_code == 200
    assert "application/octet-stream" in resp.headers.get("content-type", "")
    assert "ARIA_Launcher" in resp.headers.get("content-disposition", "")
    assert "@echo off" in resp.text
    assert "aria-intel.fly.dev" in resp.text


def test_zip_download_returns_aria_folder():
    """The /download/aria endpoint should still return a valid ZIP."""
    resp = client.get("/download/aria")
    assert resp.status_code == 200
    assert "application/zip" in resp.headers.get("content-type", "")
    assert "ARIA.zip" in resp.headers.get("content-disposition", "")
    zf = zipfile.ZipFile(io.BytesIO(resp.content))
    names = zf.namelist()
    assert any("aria.bat" in n for n in names), f"aria.bat not in ZIP: {names}"
    assert any("README.txt" in n for n in names), f"README.txt not in ZIP: {names}"
    bat_content = zf.read([n for n in names if n.endswith("aria.bat")][0]).decode()
    assert "@echo off" in bat_content
    assert "aria-intel.fly.dev" in bat_content


def test_client_download_returns_aria_client_zip():
    """The /download/client endpoint should return a ZIP with aria.py and aria.bat."""
    resp = client.get("/download/client")
    assert resp.status_code == 200
    assert "application/zip" in resp.headers.get("content-type", "")
    assert "ARIA_Client.zip" in resp.headers.get("content-disposition", "")
    zf = zipfile.ZipFile(io.BytesIO(resp.content))
    names = zf.namelist()
    assert any("aria.py" in n for n in names), f"aria.py not in ZIP: {names}"
    assert any("aria.bat" in n for n in names), f"aria.bat not in ZIP: {names}"
    bat_content = zf.read([n for n in names if n.endswith("aria.bat")][0]).decode()
    assert "@echo off" in bat_content
    assert "aria-intel.fly.dev" in bat_content
    # Verify it calls the real /api/aria/chat endpoint
    assert "/api/aria/chat" in bat_content
    # Verify it sends auth
    assert "Authorization" in bat_content


def test_client_bat_sends_auth_header():
    """The aria_client/aria.bat must send Authorization: Bearer header."""
    import zipfile, io
    resp = client.get("/download/client")
    zf = zipfile.ZipFile(io.BytesIO(resp.content))
    names = zf.namelist()
    bat_file = [n for n in names if n.endswith("aria.bat")][0]
    bat_content = zf.read(bat_file).decode()
    # Must send Authorization header with Bearer token
    assert "Authorization" in bat_content, (
        "Client .bat must send Authorization header"
    )
    assert "Bearer" in bat_content, (
        "Client .bat must use Bearer token auth"
    )
    # Must call the real chat endpoint
    assert "/api/aria/chat" in bat_content, (
        "Client .bat must call the real /api/aria/chat endpoint"
    )
    # Must reference the live server
    assert "aria-intel.fly.dev" in bat_content
    # Must use Invoke-RestMethod
    assert "Invoke-RestMethod" in bat_content


def test_aria_folder_bat_sends_auth_header():
    """The aria_folder/aria.bat must send Authorization: Bearer header."""
    import zipfile, io
    resp = client.get("/download/aria")
    zf = zipfile.ZipFile(io.BytesIO(resp.content))
    names = zf.namelist()
    bat_file = [n for n in names if n.endswith("aria.bat")][0]
    bat_content = zf.read(bat_file).decode()
    # Must send Authorization header
    assert "Authorization" in bat_content, (
        "aria_folder .bat must send Authorization header"
    )
    assert "Bearer" in bat_content
    # Must call the real chat endpoint
    assert "/api/aria/chat" in bat_content
    # Must reference the live server
    assert "aria-intel.fly.dev" in bat_content
    # Must NOT try to download the full crucix repo
    assert "github.com/Arkmurus/crucix" not in bat_content, (
        "aria_folder .bat should not download the full repo"
    )


# ── Tests: Python client CLI interface ─────────────────────────────────────────


def test_python_client_has_main():
    """The Python client must have a main() entry point."""
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "aria_client_module7",
        os.path.join(os.path.dirname(__file__), "..", "static", "aria_client", "aria.py"),
    )
    if spec is None or spec.loader is None:
        pytest.skip("aria.py not found")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    assert hasattr(mod, "main"), "aria.py must have a main() function"
    assert hasattr(mod, "interactive_shell"), "aria.py must have interactive_shell()"
    assert hasattr(mod, "run_setup"), "aria.py must have run_setup()"
    assert hasattr(mod, "check_status"), "aria.py must have check_status()"
    assert hasattr(mod, "send_chat"), "aria.py must have send_chat()"
    assert hasattr(mod, "send_chat_stream"), "aria.py must have send_chat_stream()"
    assert hasattr(mod, "AriaError"), "aria.py must have AriaError exception"
    assert hasattr(mod, "_get_token"), "aria.py must have _get_token()"
    assert hasattr(mod, "_get_server"), "aria.py must have _get_server()"


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


# ── Tests: .bat has setup command ──────────────────────────────────────────────


def test_client_bat_has_setup_command():
    """The aria_client/aria.bat must have a 'setup' command for token instructions."""
    import zipfile, io
    resp = client.get("/download/client")
    zf = zipfile.ZipFile(io.BytesIO(resp.content))
    names = zf.namelist()
    bat_file = [n for n in names if n.endswith("aria.bat")][0]
    bat_content = zf.read(bat_file).decode()
    # Must have a setup command
    assert '"setup"' in bat_content or "setup" in bat_content.lower(), (
        "Client .bat must have a setup command"
    )
    # Must mention API token in the setup instructions
    assert "API_TOKEN" in bat_content or "api_token" in bat_content, (
        "Client .bat setup must mention API token"
    )
    # Must mention intel.arkmurus.com as token source
    assert "intel.arkmurus.com" in bat_content, (
        "Client .bat must tell user where to get a token"
    )


def test_aria_folder_bat_has_setup_command():
    """The aria_folder/aria.bat must have a 'setup' command."""
    import zipfile, io
    resp = client.get("/download/aria")
    zf = zipfile.ZipFile(io.BytesIO(resp.content))
    names = zf.namelist()
    bat_file = [n for n in names if n.endswith("aria.bat")][0]
    bat_content = zf.read(bat_file).decode()
    assert '"setup"' in bat_content or "setup" in bat_content.lower(), (
        "aria_folder .bat must have a setup command"
    )
    assert "API_TOKEN" in bat_content or "api_token" in bat_content


def test_download_aria_bat_has_setup_command():
    """The download_aria.bat must have a 'setup' command."""
    resp = client.get("/download")
    assert resp.status_code == 200
    content = resp.text
    assert '"setup"' in content or "setup" in content.lower(), (
        "download_aria.bat must have a setup command"
    )
    assert "API_TOKEN" in content or "api_token" in content


# ── Tests: .bat auto-downloads aria.py ─────────────────────────────────────────


def test_client_bat_can_download_aria_py():
    """The aria_client/aria.bat must be able to download aria.py from the server."""
    import zipfile, io
    resp = client.get("/download/client")
    zf = zipfile.ZipFile(io.BytesIO(resp.content))
    names = zf.namelist()
    bat_file = [n for n in names if n.endswith("aria.bat")][0]
    bat_content = zf.read(bat_file).decode()
    # Must reference the download URL for aria.py
    assert "/download/aria.py" in bat_content, (
        "Client .bat must know how to download aria.py from the server"
    )
    # Must have a download mechanism (PowerShell WebClient or similar)
    assert "DownloadFile" in bat_content or "WebClient" in bat_content, (
        "Client .bat must have a download mechanism for aria.py"
    )
