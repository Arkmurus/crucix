"""R-F454 — brain/absorb ClientDisconnect handling.

Live evidence 2026-05-13 ~22:56 BST (next_session_pickup_2026_05_15
Session B addendum, brain-cascade diagnosis):
  - sentence-transformer batches at 12-35s each
  - seenode proxy times out mid-body-read
  - starlette.requests.ClientDisconnect raised on `await request.json()`
  - propagates as 500 → LB health-check flakes, brain breaker trips,
    dashboard banner reports "ARIA service offline" even though
    /health/live is 200
  - cascade unwinds the moment the disconnect is caught

This test pins the user-visible symptom: a client that disconnects
mid-body-read produces a 499 response, not a 500 → so the LB and
the brain breaker stay healthy, and the dashboard banner stays
honest about what's actually broken (slow embed, not "offline").
"""
from __future__ import annotations

import os as _os
import pytest
from starlette.requests import ClientDisconnect
from fastapi.testclient import TestClient


def _auth_header() -> dict:
    """Return the Authorization header needed by the router auth dependency."""
    token = _os.environ.get("ARIA_API_TOKEN") or _os.environ.get("ARIA_INTERNAL_TOKEN") or ""
    if token:
        return {"Authorization": f"Bearer {token}"}
    return {}


def _build_app():
    from fastapi import FastAPI
    from aria_service.routes.aria import router as aria_router
    app = FastAPI()
    app.include_router(aria_router)
    return app


def test_rf454_client_disconnect_returns_499_not_500(monkeypatch):
    """If `await request.json()` raises ClientDisconnect (mid-body proxy
    timeout), the endpoint must return 499 rather than letting it
    propagate as a 500."""
    # Patch starlette's Request.json on the class — TestClient builds
    # real Request objects, so a class-level patch is the cleanest
    # hook into the actual code path used by brain_absorb_ep.
    from starlette.requests import Request

    async def _disconnect(self):  # noqa: ARG001
        raise ClientDisconnect()

    monkeypatch.setattr(Request, "json", _disconnect)

    app = _build_app()
    with TestClient(app) as client:
        # raise_server_exceptions=False so TestClient doesn't re-raise the
        # exception from the endpoint — we want to inspect the response
        # FastAPI would have sent over the wire.
        client.raise_server_exceptions = False
        r = client.post(
            "/api/aria/brain/absorb",
            json={"module": "test", "summary": "test"},
            headers=_auth_header(),
        )

    assert r.status_code == 499, (
        f"R-F454: expected 499 on ClientDisconnect, got {r.status_code} "
        f"body={r.text!r}"
    )


def test_rf454_normal_request_still_202(monkeypatch):
    """Regression guard: the disconnect catch must not swallow normal
    requests. Healthy POSTs continue returning 202 Accepted."""
    async def _fake_absorb(**kwargs):  # noqa: ARG001
        return None

    from aria_service.intel import brain_hook
    monkeypatch.setattr(brain_hook, "absorb", _fake_absorb)

    app = _build_app()
    with TestClient(app) as client:
        r = client.post(
            "/api/aria/brain/absorb",
            json={"module": "test_module", "summary": "test summary"},
            headers=_auth_header(),
        )

    assert r.status_code == 202, (
        f"R-F454 regression guard: healthy POST must still 202, "
        f"got {r.status_code} body={r.text!r}"
    )


def test_rf454_disconnect_logged_at_info(monkeypatch, caplog):
    """The R-F454 path must log at INFO so operators can see disconnect
    rate in seenode/fly logs (silent disconnects make the breaker tune
    impossible to calibrate)."""
    import logging
    from starlette.requests import Request

    async def _disconnect(self):  # noqa: ARG001
        raise ClientDisconnect()

    monkeypatch.setattr(Request, "json", _disconnect)

    app = _build_app()
    with caplog.at_level(logging.INFO, logger="aria.routes"):
        with TestClient(app) as client:
            client.raise_server_exceptions = False
            client.post(
                "/api/aria/brain/absorb",
                json={"module": "test", "summary": "test"},
                headers=_auth_header(),
            )

    assert any("R-F454" in r.message and "disconnected" in r.message.lower()
               for r in caplog.records), (
        f"R-F454: expected INFO log with R-F454 marker, "
        f"got records: {[r.message for r in caplog.records]}"
    )


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
