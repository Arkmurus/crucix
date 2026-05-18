"""R-F686 (2026-05-18) — self-diagnostic auth on endpoint probes.

Live fly logs 2026-05-18 10:01:24 showed the SELF-DIAGNOSTIC-15MIN
autonomous task firing 16+ outbound GETs to aria-intel.fly.dev/api/aria/*
without a bearer token, all returning 401 Unauthorized. The diagnostic
was treating 401 as PASS ("route exists") so it could never see real
endpoint health behind the auth wall — a silent observability gap.

R-F686 reads ARIA_INTERNAL_TOKEN (fallback ARIA_API_TOKEN) from env
and sends it as Bearer. With a valid token:
  - 200 is accepted as PASS even when the spec only listed 401
  - 401 becomes a FAIL (token misconfig / rotation alert)
  - 404, 405, 422 etc continue to be interpreted per existing rules

Without a token (graceful degrade), behaviour is unchanged: 401 is
still treated as "route exists, auth enforced" PASS.
"""
from __future__ import annotations

import asyncio
import os
import pytest


def _run(coro):
    return asyncio.run(coro)


class _FakeResponse:
    def __init__(self, status_code: int):
        self.status_code = status_code


class _FakeAsyncClient:
    """Captures the headers sent + returns a configurable status."""
    captured_headers: dict | None = None

    def __init__(self, status_code: int, capture_into: dict):
        self._status = status_code
        self._capture = capture_into

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def get(self, url, headers=None, **kw):
        self._capture["url"] = url
        self._capture["headers"] = dict(headers or {})
        return _FakeResponse(self._status)


def _stub_httpx(monkeypatch, status_code: int):
    captured: dict = {}

    class FakeHTTPX:
        @staticmethod
        def AsyncClient(*a, **kw):
            return _FakeAsyncClient(status_code, captured)

    import sys
    monkeypatch.setitem(sys.modules, "httpx", FakeHTTPX)
    return captured


def test_rf686_sends_bearer_when_token_set(monkeypatch):
    """When ARIA_INTERNAL_TOKEN is in env, the probe must send it as
    Authorization: Bearer <token>."""
    monkeypatch.setenv("ARIA_INTERNAL_TOKEN", "secret-internal-tok")
    monkeypatch.delenv("ARIA_API_TOKEN", raising=False)
    captured = _stub_httpx(monkeypatch, status_code=200)

    from aria_service.intel import self_diagnostic
    status, note = _run(self_diagnostic._check_endpoint("/api/aria/some/route"))
    assert status == "PASS", note
    assert captured["headers"].get("Authorization") == "Bearer secret-internal-tok"


def test_rf686_falls_back_to_api_token(monkeypatch):
    """ARIA_API_TOKEN should be used if ARIA_INTERNAL_TOKEN is unset."""
    monkeypatch.delenv("ARIA_INTERNAL_TOKEN", raising=False)
    monkeypatch.setenv("ARIA_API_TOKEN", "public-api-tok")
    captured = _stub_httpx(monkeypatch, status_code=200)

    from aria_service.intel import self_diagnostic
    _run(self_diagnostic._check_endpoint("/api/aria/x"))
    assert captured["headers"].get("Authorization") == "Bearer public-api-tok"


def test_rf686_no_header_when_neither_token_set(monkeypatch):
    """Without any token in env, no Authorization header is sent —
    graceful degrade to the old 401-as-PASS behaviour."""
    monkeypatch.delenv("ARIA_INTERNAL_TOKEN", raising=False)
    monkeypatch.delenv("ARIA_API_TOKEN", raising=False)
    captured = _stub_httpx(monkeypatch, status_code=401)

    from aria_service.intel import self_diagnostic
    status, note = _run(self_diagnostic._check_endpoint("/api/aria/x"))
    # 401 with no token = old behaviour = PASS
    assert status == "PASS"
    assert "Authorization" not in captured["headers"]


def test_rf686_401_with_token_becomes_FAIL(monkeypatch):
    """If we sent a bearer and STILL got 401, the token is wrong —
    surface as FAIL so the operator knows to check the secret."""
    monkeypatch.setenv("ARIA_INTERNAL_TOKEN", "stale-token")
    monkeypatch.delenv("ARIA_API_TOKEN", raising=False)
    _stub_httpx(monkeypatch, status_code=401)

    from aria_service.intel import self_diagnostic
    status, note = _run(self_diagnostic._check_endpoint("/api/aria/x"))
    assert status == "FAIL", f"expected FAIL on 401-with-token: {(status, note)}"
    assert "token" in note.lower()


def test_rf686_200_with_token_accepted_even_if_spec_only_listed_401(monkeypatch):
    """The existing specs were authored when 401 was the expected
    response. With a token, 200 must be accepted as PASS even when the
    spec lists only (401,)."""
    monkeypatch.setenv("ARIA_INTERNAL_TOKEN", "tok")
    monkeypatch.delenv("ARIA_API_TOKEN", raising=False)
    _stub_httpx(monkeypatch, status_code=200)

    from aria_service.intel import self_diagnostic
    status, note = _run(
        self_diagnostic._check_endpoint(
            "/api/aria/x", unauth_ok_codes=(401,),
        )
    )
    assert status == "PASS", (status, note)
    assert "200" in note


def test_rf686_405_still_acceptable_for_post_only_routes(monkeypatch):
    """GET-on-POST-only endpoints return 405 by design; the diagnostic
    must keep accepting that, with or without a token."""
    monkeypatch.setenv("ARIA_INTERNAL_TOKEN", "tok")
    _stub_httpx(monkeypatch, status_code=405)

    from aria_service.intel import self_diagnostic
    status, _ = _run(
        self_diagnostic._check_endpoint(
            "/api/aria/publisher/fetch", unauth_ok_codes=(401, 405),
        )
    )
    assert status == "PASS"


def test_rf686_404_still_FAIL(monkeypatch):
    """Regression guard — a missing route must still be FAIL regardless
    of auth state."""
    monkeypatch.setenv("ARIA_INTERNAL_TOKEN", "tok")
    _stub_httpx(monkeypatch, status_code=404)

    from aria_service.intel import self_diagnostic
    status, note = _run(self_diagnostic._check_endpoint("/api/aria/missing"))
    assert status == "FAIL"
    assert "404" in note


def test_rf686_500_still_WARN(monkeypatch):
    monkeypatch.setenv("ARIA_INTERNAL_TOKEN", "tok")
    _stub_httpx(monkeypatch, status_code=503)

    from aria_service.intel import self_diagnostic
    status, _ = _run(self_diagnostic._check_endpoint("/api/aria/x"))
    assert status == "WARN"
