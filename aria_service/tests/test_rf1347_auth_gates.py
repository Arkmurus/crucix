"""R-F1347 — close 5 confirmed-live auth holes (full ecosystem audit 2026-06-05).

P0/P1 holes verified live before the fix:
  /token                       -> 200, rendered the master bearer token publicly
  /api/aria/client/chat        -> 400 (past auth) unauth full-LLM spend
  /api/aria/client/analyse     -> 400 (past auth) unauth full-LLM spend
  /api/aria/learning/sync      -> 200 unauth brain WRITE (memory poison)
  /api/aria/learning/updates   -> 200 unauth gap/mistake-ledger leak

All five now require_aria_token. This capability test drives the real ASGI
app (no lifespan needed — auth is a per-request dependency) and asserts each
returns 401 without a bearer and is NOT 401 with the right bearer.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

_TOKEN = "rf1347-test-token-aaaaaaaaaaaaaaaaaaaa"


@pytest.fixture(scope="module")
def client():
    # Enforcement is active only when a token secret is set.
    os.environ["ARIA_API_TOKEN"] = _TOKEN
    os.environ.setdefault("HF_HUB_OFFLINE", "1")
    os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")
    os.environ.setdefault("ARIA_INDEX_QUEUE_DISABLED", "1")
    from fastapi.testclient import TestClient
    from aria_service.main import app
    # No `with` → lifespan does not run; routes + dependencies still resolve.
    return TestClient(app, raise_server_exceptions=False)


GATED = [
    ("get", "/token"),
    ("post", "/api/aria/client/chat"),
    ("post", "/api/aria/client/analyse"),
    ("post", "/api/aria/learning/sync"),
    ("get", "/api/aria/learning/updates"),
]


def _call(client, method, path, headers=None):
    # GET takes no json body; POST sends an empty dict.
    kw = {"headers": headers} if headers else {}
    if method == "post":
        kw["json"] = {}
    return getattr(client, method)(path, **kw)


@pytest.mark.parametrize("method,path", GATED)
def test_endpoint_requires_auth(client, method, path):
    r = _call(client, method, path)
    assert r.status_code == 401, (
        f"{method.upper()} {path} returned {r.status_code} without a bearer "
        f"— SECURITY HOLE (must be 401)"
    )


@pytest.mark.parametrize("method,path", GATED)
def test_endpoint_passes_auth_gate_with_token(client, method, path):
    # With the right bearer the auth gate must NOT 401. (Any other status —
    # 400 for a bad body, 200, 500 — proves the request got PAST auth.)
    r = _call(client, method, path, headers={"Authorization": f"Bearer {_TOKEN}"})
    assert r.status_code != 401, (
        f"{method.upper()} {path} 401'd even WITH the correct token — gate misconfigured"
    )
