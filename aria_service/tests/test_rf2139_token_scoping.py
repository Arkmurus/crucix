"""R-F2139 — Per-service token scoping ON by default.

When ARIA_OPERATOR_TOKEN is set, control/destructive routes require the
OPERATOR token. The shared service token (WA listener, web proxy, CLI) can
chat/read/telemetry but cannot drive the control plane.

Tests require_aria_token directly since the TestClient bypasses auth.
"""
from __future__ import annotations

import pytest


def _make_request(path: str, token: str):
    """Create a mock FastAPI Request with the given path and auth header."""
    from starlette.requests import Request
    scope = {
        "type": "http",
        "method": "GET",
        "path": path,
        "headers": [(b"authorization", f"Bearer {token}".encode())],
        "query_string": b"",
        "scheme": "http",
        "server": ("test", 80),
    }
    return Request(scope)


# Shared token values for tests
_API_TOKEN = "api-token-abc"
_OP_TOKEN = "op-secret-123"
_SERVICE_TOKEN = "service-token-xyz"


def test_rf2139_service_token_blocked_on_control_route(monkeypatch):
    """Service token (not operator) → 403 on control/destructive routes."""
    from aria_service.routes import aria as aria_routes
    from fastapi import HTTPException

    # Set both API and operator tokens so the service token is accepted
    # but fails the operator check
    monkeypatch.setenv("ARIA_API_TOKEN", _API_TOKEN)
    monkeypatch.setenv("ARIA_INTERNAL_TOKEN", _SERVICE_TOKEN)
    monkeypatch.setenv("ARIA_OPERATOR_TOKEN", _OP_TOKEN)
    monkeypatch.delenv("ARIA_TOKEN_SCOPING", raising=False)

    # Use the internal/service token — it's accepted by auth but is NOT the operator token
    req = _make_request("/api/aria/autonomous/status", _SERVICE_TOKEN)

    with pytest.raises(HTTPException) as exc:
        aria_routes.require_aria_token(req)
    assert exc.value.status_code == 403, (
        f"Expected 403 for service token on control route, got {exc.value.status_code}"
    )


def test_rf2139_operator_token_allowed_on_control_route(monkeypatch):
    """Operator token → no exception on control/destructive routes."""
    from aria_service.routes import aria as aria_routes

    monkeypatch.setenv("ARIA_API_TOKEN", _API_TOKEN)
    monkeypatch.setenv("ARIA_INTERNAL_TOKEN", _SERVICE_TOKEN)
    monkeypatch.setenv("ARIA_OPERATOR_TOKEN", _OP_TOKEN)
    monkeypatch.delenv("ARIA_TOKEN_SCOPING", raising=False)

    req = _make_request("/api/aria/autonomous/status", _OP_TOKEN)

    # Should not raise
    aria_routes.require_aria_token(req)


def test_rf2139_service_token_allowed_on_read_route(monkeypatch):
    """Service token → no exception on chat/read/telemetry routes."""
    from aria_service.routes import aria as aria_routes

    monkeypatch.setenv("ARIA_API_TOKEN", _API_TOKEN)
    monkeypatch.setenv("ARIA_INTERNAL_TOKEN", _SERVICE_TOKEN)
    monkeypatch.setenv("ARIA_OPERATOR_TOKEN", _OP_TOKEN)
    monkeypatch.delenv("ARIA_TOKEN_SCOPING", raising=False)

    req = _make_request("/api/aria/chat", _SERVICE_TOKEN)

    # Should not raise — chat is not a control route
    aria_routes.require_aria_token(req)


def test_rf2139_scoping_disabled_via_env(monkeypatch):
    """ARIA_TOKEN_SCOPING=0 disables scoping even with operator token set."""
    from aria_service.routes import aria as aria_routes

    monkeypatch.setenv("ARIA_API_TOKEN", _API_TOKEN)
    monkeypatch.setenv("ARIA_INTERNAL_TOKEN", _SERVICE_TOKEN)
    monkeypatch.setenv("ARIA_OPERATOR_TOKEN", _OP_TOKEN)
    monkeypatch.setenv("ARIA_TOKEN_SCOPING", "0")

    req = _make_request("/api/aria/autonomous/status", _SERVICE_TOKEN)

    # Should not raise — scoping is disabled
    aria_routes.require_aria_token(req)


def test_rf2139_no_operator_token_no_scoping(monkeypatch):
    """No ARIA_OPERATOR_TOKEN set → no scoping, service token works everywhere."""
    from aria_service.routes import aria as aria_routes

    monkeypatch.setenv("ARIA_API_TOKEN", _API_TOKEN)
    monkeypatch.setenv("ARIA_INTERNAL_TOKEN", _SERVICE_TOKEN)
    monkeypatch.delenv("ARIA_OPERATOR_TOKEN", raising=False)
    monkeypatch.delenv("ARIA_TOKEN_SCOPING", raising=False)

    req = _make_request("/api/aria/autonomous/status", _SERVICE_TOKEN)

    # Should not raise — no operator token means scoping is inactive
    aria_routes.require_aria_token(req)
