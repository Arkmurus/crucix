"""R-F1125 — Capability tests for the live health regression suite.

Tests that live_health_check.py correctly:
1. Checks app health endpoints
2. Validates build_rev matches expected SHA
3. Reports failures correctly
"""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import httpx
import pytest

# Import directly from the script module
_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
import sys
sys.path.insert(0, str(_REPO_ROOT / "scripts"))
import importlib
import scripts.live_health_check as _lhc
APPS = _lhc.APPS
# R-F1478 renamed the module global _EXPECTED_SHA -> _FILE_SHA and made the expected
# sha an explicit arg to check_app_health(client, app, config, expected_sha) instead
# of a module global the lambdas close over (race-proofing — see R-F1478).
_FILE_SHA = _lhc._FILE_SHA
check_app_health = _lhc.check_app_health
fetch_json = _lhc.fetch_json


# ── Tests for fetch_json ────────────────────────────────────────────────────

class TestFetchJson:
    """Proves fetch_json handles various response types."""

    def test_returns_parsed_json(self):
        """JSON responses are parsed correctly."""
        mock_resp = MagicMock(spec=httpx.Response)
        mock_resp.text = '{"status": "alive", "build_rev": "sha abc12345"}'
        mock_resp.raise_for_status = MagicMock()

        with patch.object(httpx.Client, "get", return_value=mock_resp):
            client = httpx.Client()
            result = fetch_json(client, "https://example.com/health")

        assert result is not None
        assert result["status"] == "alive"
        assert result["build_rev"] == "sha abc12345"

    def test_returns_raw_text_for_non_json(self):
        """Non-JSON responses are returned as raw text."""
        mock_resp = MagicMock(spec=httpx.Response)
        mock_resp.text = "ok"
        mock_resp.raise_for_status = MagicMock()

        with patch.object(httpx.Client, "get", return_value=mock_resp):
            client = httpx.Client()
            result = fetch_json(client, "https://example.com/healthz")

        assert result == "ok"

    def test_returns_none_on_http_error(self):
        """HTTP errors return None."""
        with patch.object(
            httpx.Client, "get",
            side_effect=httpx.HTTPStatusError("404", request=MagicMock(), response=MagicMock()),
        ):
            client = httpx.Client()
            result = fetch_json(client, "https://example.com/health")

        assert result is None


# ── Tests for check_app_health ──────────────────────────────────────────────

class TestCheckAppHealth:
    """Proves check_app_health validates app status correctly."""

    def test_intel_health_pass(self):
        """Intel health check passes when build_rev matches."""
        config = APPS["intel"]
        data = {"status": "alive", "build_rev": "sha abc12345"}

        with patch("scripts.live_health_check.fetch_json", return_value=data):
            with httpx.Client() as client:
                result = check_app_health(client, "intel", config, "abc12345")

        assert result is True

    def test_intel_health_fail_wrong_sha(self):
        """Intel health check fails when build_rev doesn't match."""
        config = APPS["intel"]
        data = {"status": "alive", "build_rev": "sha wrongsha"}

        with patch("scripts.live_health_check.fetch_json", return_value=data):
            with httpx.Client() as client:
                result = check_app_health(client, "intel", config, "abc12345")

        assert result is False

    def test_intel_health_fail_unreachable(self):
        """Intel health check fails when app is unreachable."""
        config = APPS["intel"]

        with patch("scripts.live_health_check.fetch_json", return_value=None):
            with httpx.Client() as client:
                result = check_app_health(client, "intel", config, "abc12345")

        assert result is False

    def test_web_health_pass(self):
        """Web health check passes on matching revision."""
        config = APPS["web"]
        data = {"status": "ok", "build_rev": "sha abc12345"}

        with patch("scripts.live_health_check.fetch_json", return_value=data):
            with httpx.Client() as client:
                result = check_app_health(client, "web", config, "abc12345")

        assert result is True

    def test_wa_health_pass(self):
        """WA health check passes on connected status."""
        config = APPS["wa"]
        data = {"status": "connected", "build_rev": "sha abc12345"}

        with patch("scripts.live_health_check.fetch_json", return_value=data):
            with httpx.Client() as client:
                result = check_app_health(client, "wa", config, "abc12345")

        assert result is True
