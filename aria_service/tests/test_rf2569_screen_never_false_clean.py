"""R-F2569 — /compliance/screen must NEVER certify CLEAR when the sanctions screen
did not actually run.

Live bug (2026-07-12): screening "Bank Rossiya" (a sanctioned bank) returned
status=CLEAR / result=PERMITTED because the sanctions backend call failed ("All
connection attempts failed") and the endpoint left the init default risk_level="clear",
which the verdict logic read as "no match" → CLEAR. That is a false clean on a
sanctioned entity — the never-false-clean USP violation.

Capability test drives the REAL compliance_screen_ep with a mocked backend.
"""
from __future__ import annotations

import asyncio
import types

import httpx
import pytest

from aria_service.routes import aria as A


def _req(entity: str = "Bank Rossiya"):
    return A.ComplianceScreenRequest(
        entity_name=entity, product_description="", destination_country="")


def _request():
    st = types.SimpleNamespace(app_url="http://unreachable.test:3117", internal_token="tok")
    return types.SimpleNamespace(app=types.SimpleNamespace(state=st))


class _Resp:
    def __init__(self, status, payload):
        self.status_code = status
        self._p = payload

    def json(self):
        return self._p


def _client_factory(behavior):
    class _C:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, url, json=None, headers=None, **k):
            if behavior == "fail":
                raise httpx.ConnectError("All connection attempts failed")
            if behavior == "500":
                return _Resp(500, {})
            if behavior == "clean":
                return _Resp(200, {"matched": False, "matches": [], "risk_level": "clear"})
            if behavior == "match":
                return _Resp(200, {"matched": True, "matches": [{"name": "Bank Rossiya"}],
                                   "risk_level": "high"})
    return _C


def _run(monkeypatch, behavior):
    monkeypatch.setattr(httpx, "AsyncClient", _client_factory(behavior))
    return asyncio.run(A.compliance_screen_ep(_req(), _request()))


def test_failed_screen_is_never_clear(monkeypatch):
    r = _run(monkeypatch, "fail")
    assert r["status"] != "CLEAR", "a failed sanctions screen must not be CLEAR"
    assert r["result"] != "PERMITTED"
    assert r["status"] == "REVIEW_REQUIRED"
    assert r["screened_against"]["Sanctions (entity)"] != "clear"


def test_backend_non_200_is_never_clear(monkeypatch):
    r = _run(monkeypatch, "500")
    assert r["status"] == "REVIEW_REQUIRED"


def test_successful_clean_screen_is_clear(monkeypatch):
    # A screen that ACTUALLY RAN and found nothing is legitimately CLEAR (no false positive).
    r = _run(monkeypatch, "clean")
    assert r["status"] == "CLEAR"


def test_match_is_blocked(monkeypatch):
    r = _run(monkeypatch, "match")
    assert r["status"] == "BLOCKED"


# ── sibling endpoint /compliance/sanctions ───────────────────────────────────
def _run_sanctions(monkeypatch, behavior, kb=""):
    monkeypatch.setattr(httpx, "AsyncClient", _client_factory(behavior))
    monkeypatch.setattr(A.knowledge, "search_knowledge", lambda n: kb)
    return asyncio.run(A.compliance_sanctions_ep(A.SanctionsRequest(name="Bank Rossiya"), _request()))


def test_sanctions_failed_screen_not_clear(monkeypatch):
    r = _run_sanctions(monkeypatch, "fail", kb="")
    assert r["clear"] is False, "a failed sanctions screen must not report clear=True"
    assert r["screening_unavailable"] is True


def test_sanctions_success_clean_is_clear(monkeypatch):
    r = _run_sanctions(monkeypatch, "clean", kb="")
    assert r["clear"] is True   # an authoritative screen that ran + found nothing is clear


if __name__ == "__main__":
    import sys
    sys.exit(pytest.main([__file__, "-v"]))
