"""R-F1241 — Tests for the ARIA demo endpoint and client endpoints.

R-F3329 — four of these had been red with 401 ever since R-F1347 (2026-06-05)
put `Depends(require_aria_token)` on /api/aria/client/chat and
/api/aria/client/analyse. Its own comment says why: "was unauth full-LLM spend"
(main.py:4828, 4856). Both endpoints proxy straight into the real chat engine,
so unauthenticated meant anyone could burn the LLM budget.

So the ENDPOINTS are right and these tests were stale, the same shape as
R-F3326. Making them green by dropping the dependency would have reopened an
open door onto metered spend, which is why establishing WHICH SIDE IS WRONG
comes before touching either.

Live-probed 2026-07-28 rather than assumed, because the failures were first read
as a TestClient artifact:

    POST /api/aria/client/chat      (no token) -> 401
    POST /api/aria/client/analyse   (no token) -> 401
    POST /api/aria/coder/demo       (no token) -> 200

TestClient and production agree exactly. The endpoints in this file sit on BOTH
sides of that line: /coder/demo is deliberately public (it runs the coder with
no LLM), while /client/* is deliberately gated. Nothing pinned either half, so
the boundary was invisible to anyone reading a red 401 — which is how the
cluster came to be diagnosed as a harness problem. Both directions are now
asserted at the bottom of this file.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from aria_service.main import app


client = TestClient(app)


_TEST_TOKEN = "rf3329-test-token"


@pytest.fixture(autouse=True)
def _token(monkeypatch):
    """Give require_aria_token something to accept.

    `_accepted_tokens()` (routes/aria.py:349) reads the token env vars at CALL
    time and keeps only truthy ones, so an unset environment accepts nothing and
    every gated request is 401.

    monkeypatch, NOT an os.environ write at module scope: the latter is a
    process-global mutation no fixture undoes (the R-F2801 anti-pattern that
    test_rf1498's header records as leaking into every later test in the run).
    """
    monkeypatch.setenv("ARIA_API_TOKEN", _TEST_TOKEN)


def _auth() -> dict:
    """Match require_aria_token."""
    return {"Authorization": f"Bearer {_TEST_TOKEN}"}


def test_demo_endpoint_returns_plan_and_code():
    """The demo endpoint should return a plan and generated code."""
    resp = client.post("/api/aria/coder/demo", json={
        "description": "Add error handling to process_item to catch exceptions",
        "code": "def process_item(data):\n    result = data[\"value\"] * 2\n    return result\n",
    })
    assert resp.status_code == 200
    data = resp.json()
    assert "plan" in data
    assert "code" in data
    assert data["plan"]["title"] is not None
    assert data["plan"]["approach"] is not None
    assert len(data["code"]) > 0


def test_demo_endpoint_requires_description():
    """The demo endpoint should reject requests without a description."""
    resp = client.post("/api/aria/coder/demo", json={})
    assert resp.status_code == 400


def test_demo_endpoint_works_with_default_code():
    """The demo endpoint should work with default code when none provided."""
    resp = client.post("/api/aria/coder/demo", json={
        "description": "Add error handling to process_item",
    })
    assert resp.status_code == 200
    data = resp.json()
    assert "code" in data
    assert len(data["code"]) > 0


def test_client_download_returns_aria_client_zip():
    """The /download/client endpoint should return a ZIP with aria.bat."""
    resp = client.get("/download/client")
    assert resp.status_code == 200
    assert "application/zip" in resp.headers.get("content-type", "")
    assert "ARIA_Client.zip" in resp.headers.get("content-disposition", "")
    import zipfile, io
    zf = zipfile.ZipFile(io.BytesIO(resp.content))
    names = zf.namelist()
    assert any("aria.bat" in n for n in names), f"aria.bat not in ZIP: {names}"
    bat_content = zf.read([n for n in names if n.endswith("aria.bat")][0]).decode()
    assert "@echo off" in bat_content
    assert "aria-intel.fly.dev" in bat_content
    # Verify it calls the real /api/aria/chat endpoint, not the canned one
    assert "/api/aria/chat" in bat_content


def test_client_chat_proxies_to_real_engine():
    """The client chat endpoint should proxy to the real ARIA chat engine.

    We mock chat_ep to verify the proxying works without needing the
    full LLM stack.
    """
    mock_response = {"response": "Hello from ARIA's real engine!", "session_id": "client_test"}

    with patch("aria_service.routes.aria.chat_ep", new=AsyncMock(return_value=mock_response)):
        resp = client.post("/api/aria/client/chat",
                           json={"message": "hello", "user": "test"}, headers=_auth())
        assert resp.status_code == 200
        data = resp.json()
        assert data["response"] == "Hello from ARIA's real engine!"


def test_client_chat_requires_message():
    """The client chat endpoint should reject empty messages."""
    resp = client.post("/api/aria/client/chat", json={}, headers=_auth())
    assert resp.status_code == 400


def test_client_analyse_proxies_to_real_engine():
    """The client analyse endpoint should proxy to the real ARIA chat engine."""
    mock_response = {
        "response": "Analysis: The code has a missing error handler.\nFix: Add try/except.",
        "session_id": "client_analyse",
    }

    with patch("aria_service.routes.aria.chat_ep", new=AsyncMock(return_value=mock_response)):
        resp = client.post("/api/aria/client/analyse", json={
            "code": "def foo():\n    pass\n"
        }, headers=_auth())
        assert resp.status_code == 200
        data = resp.json()
        assert "analysis" in data
        assert "fixes" in data
        assert len(data["analysis"]) > 0


def test_client_analyse_requires_code():
    """The client analyse endpoint should reject empty code."""
    resp = client.post("/api/aria/client/analyse", json={}, headers=_auth())
    assert resp.status_code == 400


def test_demo_page_served_at_root():
    """The demo HTML page should be served at the root URL."""
    resp = client.get("/")
    assert resp.status_code == 200
    assert "text/html" in resp.headers.get("content-type", "")
    assert "ARIA" in resp.text
    assert "Coder Playground" in resp.text


def test_client_bat_calls_real_chat_endpoint():
    """The aria_client/aria.bat should call /api/aria/chat, not the canned endpoint."""
    import zipfile, io
    resp = client.get("/download/client")
    zf = zipfile.ZipFile(io.BytesIO(resp.content))
    names = zf.namelist()
    bat_file = [n for n in names if n.endswith("aria.bat")][0]
    bat_content = zf.read(bat_file).decode()
    # Must call the real chat endpoint
    assert "/api/aria/chat" in bat_content, (
        "Client .bat must call the real /api/aria/chat endpoint, "
        "not the canned /api/aria/client/chat"
    )
    # Must reference the live server
    assert "aria-intel.fly.dev" in bat_content
    # Must use Invoke-RestMethod (not just canned responses)
    assert "Invoke-RestMethod" in bat_content


# ── R-F3329: the public/authed boundary, pinned in BOTH directions ───────────
#
# The four tests above were red for seven weeks and the failure was first read
# as a TestClient artifact. It was not: production returns exactly the same
# codes. What was missing was any statement of which of these endpoints is
# supposed to be open, so a 401 could not be told apart from a regression by
# reading the suite. These two tests are that statement.


@pytest.mark.parametrize("path,body", [
    ("/api/aria/client/chat", {"message": "hello"}),
    ("/api/aria/client/analyse", {"code": "def foo():\n    pass\n"}),
])
def test_rf3329_client_endpoints_still_require_auth(path, body):
    """R-F1347's property, pinned.

    Both endpoints proxy into the real chat engine, so an unauthenticated
    caller spends the LLM budget. If a future change drops the dependency (the
    obvious way to make a red 401 go green), this fails instead of shipping an
    open door onto metered spend.
    """
    resp = client.post(path, json=body)
    assert resp.status_code == 401, (
        f"{path} must reject unauthenticated callers (R-F1347: was unauth "
        f"full-LLM spend); got {resp.status_code}"
    )


def test_rf3329_demo_endpoint_stays_public():
    """The other half: /coder/demo is deliberately open, and must stay open.

    It runs the autonomous coder with NO LLM, and it is what the public demo
    page calls. Live-verified unauthenticated 200 on 2026-07-28. Asserted with
    an empty body, so the 400 proves the handler RAN: an auth gate would answer
    401 before the missing-description check is ever reached. Gating it "for
    consistency" with its /client/* neighbours would break the demo page.
    """
    resp = client.post("/api/aria/coder/demo", json={})
    assert resp.status_code == 400, (
        "/api/aria/coder/demo must stay public (no auth dependency); "
        f"got {resp.status_code}"
    )



