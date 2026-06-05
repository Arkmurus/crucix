"""R-F1349 — Zoom webhook must NOT be bypassable by omitting the signature.

Deep-hunt P0 (verified live: HTTP 200 with no signature): the guard
`if _WEBHOOK_SECRET and signature:` skipped verification whenever the
x-zm-signature header was absent (empty → falsy), letting an attacker reach
the download/SSRF path unauthenticated. Fix: secret set → signature REQUIRED.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest


@pytest.fixture(scope="module")
def client():
    os.environ.setdefault("HF_HUB_OFFLINE", "1")
    os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")
    os.environ.setdefault("ARIA_INDEX_QUEUE_DISABLED", "1")
    from fastapi.testclient import TestClient
    from aria_service.main import app
    from aria_service.intel import zoom_integration as zoom
    zoom._WEBHOOK_SECRET = "rf1349-test-secret"   # enable verification
    return TestClient(app, raise_server_exceptions=False)


def test_webhook_rejects_missing_signature(client):
    # No x-zm-signature header → must be 401 (was 200 = SSRF bypass).
    r = client.post("/api/aria/zoom/webhook", json={"event": "recording.completed"})
    assert r.status_code == 401, (
        f"Zoom webhook returned {r.status_code} with NO signature — bypass still open"
    )


def test_webhook_rejects_bogus_signature(client):
    r = client.post(
        "/api/aria/zoom/webhook",
        json={"event": "recording.completed"},
        headers={"x-zm-signature": "v0=deadbeef", "x-zm-request-timestamp": "123"},
    )
    assert r.status_code == 401, (
        f"Zoom webhook accepted a bogus signature ({r.status_code})"
    )
