"""R-F3002 — FCA Financial Services Register adapter (dormant by default).

Friend/DD-practitioner review (#6): for a UK entity trading as 'Capital
Management', FCA authorisation status is the first question; SIC 64999 evidences
nothing. This adapter answers it against the official FCA Register — DORMANT until
FCA_API_EMAIL + FCA_API_KEY are set, and NEVER fabricates a status.
"""
import asyncio
from pathlib import Path
from unittest.mock import patch

import pytest

from aria_service.intel import fca_register as fca


def _run(coro):
    return asyncio.run(coro)


# ── dormancy: no fabricated result without credentials ────────────────────────

def test_rf3002_dormant_without_credentials(monkeypatch):
    monkeypatch.delenv("FCA_API_EMAIL", raising=False)
    monkeypatch.delenv("FCA_API_KEY", raising=False)
    assert fca.is_configured() is False
    out = _run(fca.lookup_firm("Silverbrook Capital Management"))
    assert out["configured"] is False
    assert out["matched"] is None          # never a clean/authorised claim
    assert "not set" in out["reason"].lower()


def test_rf3002_is_configured_requires_both_secrets(monkeypatch):
    monkeypatch.setenv("FCA_API_EMAIL", "x@y.com")
    monkeypatch.delenv("FCA_API_KEY", raising=False)
    assert fca.is_configured() is False    # one secret is not enough
    monkeypatch.setenv("FCA_API_KEY", "k")
    assert fca.is_configured() is True


def test_rf3002_status_mapping():
    assert fca._is_authorised_status("Authorised") is True
    assert fca._is_authorised_status("Registered") is True
    assert fca._is_authorised_status("No longer authorised") is False
    assert fca._is_authorised_status("Cancelled") is False
    assert fca._is_authorised_status("") is False


# ── configured path (mocked HTTP) — returns the real status, never invents it ─

class _FakeResp:
    def __init__(self, status_code=200, payload=None):
        self.status_code = status_code
        self._payload = payload or {}

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


class _FakeClient:
    def __init__(self, resp):
        self._resp = resp

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def get(self, url, params=None):
        return self._resp


def _client_factory(resp):
    def _factory(*a, **k):
        return _FakeClient(resp)
    return _factory


def test_rf3002_configured_match_returns_status(monkeypatch):
    monkeypatch.setenv("FCA_API_EMAIL", "x@y.com")
    monkeypatch.setenv("FCA_API_KEY", "k")
    resp = _FakeResp(200, {"Data": [
        {"Type": "firm", "Name": "EXAMPLE ASSET MANAGEMENT LTD",
         "Reference Number": "123456", "Status": "Authorised"},
    ]})
    with patch("httpx.AsyncClient", _client_factory(resp)):
        out = _run(fca.lookup_firm("Example Asset Management"))
    assert out["configured"] is True
    assert out["matched"] is True
    assert out["frn"] == "123456"
    assert out["status"] == "Authorised"
    assert out["is_authorised"] is True
    assert "firmReferenceNumber=123456" in out["detail_url"]


def test_rf3002_configured_auth_rejected_is_error_not_clean(monkeypatch):
    monkeypatch.setenv("FCA_API_EMAIL", "x@y.com")
    monkeypatch.setenv("FCA_API_KEY", "bad")
    with patch("httpx.AsyncClient", _client_factory(_FakeResp(401, {}))):
        out = _run(fca.lookup_firm("Example"))
    assert out["configured"] is True
    assert "error" in out and "auth rejected" in out["error"].lower()
    assert "matched" not in out or out.get("matched") is not True


# ── wiring lock: the GB identity branch calls it + discloses the dormant gap ──

def test_rf3002_wired_into_gb_identity_with_honest_gap():
    src = (Path(__file__).resolve().parents[1] / "intel" / "dd_orchestrator.py").read_text(encoding="utf-8")
    assert "from . import fca_register" in src
    assert "fca_register.lookup_firm" in src
    assert "FCA Register not checked" in src  # dormant → honest gap, not a fabricated status
