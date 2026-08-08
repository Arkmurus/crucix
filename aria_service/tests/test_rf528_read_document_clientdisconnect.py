"""R-F528 — /read-document must handle ClientDisconnect cleanly.

Live evidence 2026-05-15 morning: when the operator-side timed out
a /read-document upload, the endpoint's `await request.json()`
raised `starlette.requests.ClientDisconnect` which propagated as an
ASGI ERROR in the logs (uvicorn's 5xx error budget got polluted).

Same pattern as R-F454 for /brain/absorb. R-F528 catches the
disconnect at the body-read step and returns 499 ("client closed
request") with INFO-level log entry instead of an ERROR.

Tests pin:
  1. Source check — the wrapper is in place
  2. Behavior — synthetic ClientDisconnect → 499 response
"""
from __future__ import annotations

import asyncio
import inspect

import pytest

from aria_service.routes import aria as aria_routes

# R-F3773/§16 — NOT inspect.getsource: it slices at line numbers captured AT
# IMPORT, so a mid-run edit silently returns a DIFFERENT function's body. A CLASS
# target scopes the lookup to that class's own body (R-F3771).
from ._source_probe import function_source


def test_rf528_source_has_clientdisconnect_handler():
    """Source-text check: /read-document endpoint contains a
    try/except around request.json() that catches ClientDisconnect
    and returns 499."""
    src = function_source(aria_routes, "read_document_ep")
    assert "ClientDisconnect" in src, (
        "R-F528: /read-document must import + catch ClientDisconnect "
        "from starlette.requests"
    )
    assert "499" in src, (
        "R-F528: must return HTTP 499 on client-disconnect "
        "(matches R-F454 brain/absorb pattern)"
    )
    assert "R-F528" in src, (
        "R-F528: marker comment must be in place for future grep"
    )


def test_rf528_client_disconnect_returns_499(monkeypatch):
    """Drive the endpoint with a Request whose .json() raises
    ClientDisconnect. Endpoint must return 499 + log INFO (not ERROR
    + propagate as 500)."""
    from starlette.requests import ClientDisconnect

    class FakeRequest:
        async def json(self):
            raise ClientDisconnect()
        # endpoint may not access other attrs after the disconnect

    fake = FakeRequest()
    response = asyncio.run(aria_routes.read_document_ep(fake))
    # Should be a Response object with status 499
    assert hasattr(response, "status_code"), (
        f"R-F528: expected Response object, got {type(response).__name__}"
    )
    assert response.status_code == 499, (
        f"R-F528: expected 499, got {response.status_code}"
    )


def test_rf528_normal_body_still_processes(monkeypatch):
    """Sanity: a happy-path body (no disconnect) still flows through
    the endpoint normally (until the next failure point — we only
    check that the disconnect handler is NOT incorrectly triggered
    by normal JSON parsing)."""
    # Drive a minimal valid body and assert the endpoint doesn't
    # short-circuit at the ClientDisconnect branch. Since the
    # endpoint does extensive downstream work we just check it
    # doesn't immediately return a 499 Response object.
    class HappyRequest:
        async def json(self):
            return {"content": "", "filename": "test.txt", "source": "test"}

    # We expect downstream processing to either return a real reply
    # or raise — what matters is it's NOT the 499 short-circuit.
    try:
        response = asyncio.run(aria_routes.read_document_ep(HappyRequest()))
        # If we got here, the response should NOT be the 499 short-circuit
        sc = getattr(response, "status_code", None)
        assert sc != 499, (
            f"R-F528: happy-path body should not trigger ClientDisconnect "
            f"short-circuit. Got status_code={sc}"
        )
    except Exception as e:
        # Some other downstream error is fine — confirms we passed the
        # ClientDisconnect handler successfully.
        assert "ClientDisconnect" not in str(e), (
            f"R-F528: happy-path raised ClientDisconnect, should not have: {e}"
        )
