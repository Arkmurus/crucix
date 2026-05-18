"""R-F690 (2026-05-18) — top-level /health/live alias.

Live fly logs 2026-05-18 10:43:28 / 10:47:15 / 10:52:53 showed
monitoring callers (172.16.14.10 — seenode proxy / operator
dashboard) hitting `/health/live` WITHOUT the `/api/aria/` prefix
and getting 404. The canonical /api/aria/health/live (R-F372) is
the fly.io load-balancer probe path; Kubernetes-style monitors
default to /health/live.

R-F690 mounts a top-level alias on the FastAPI app (sibling of the
existing /health endpoint at main.py:1436) that returns the same
payload shape as the canonical handler — no Redis, no stats,
in-process only.
"""
from __future__ import annotations


def test_rf690_top_level_health_live_returns_200():
    from fastapi.testclient import TestClient
    from aria_service.main import app
    client = TestClient(app)
    r = client.get("/health/live")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body.get("status") == "alive"
    assert "build_rev" in body


def test_rf690_canonical_path_still_works():
    """Regression guard — the /api/aria/health/live path that fly.io
    actually probes must keep working alongside the new alias."""
    from fastapi.testclient import TestClient
    from aria_service.main import app
    client = TestClient(app)
    r = client.get("/api/aria/health/live")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body.get("status") == "alive"


def test_rf690_alias_and_canonical_return_same_shape():
    from fastapi.testclient import TestClient
    from aria_service.main import app
    client = TestClient(app)
    a = client.get("/health/live").json()
    b = client.get("/api/aria/health/live").json()
    assert set(a.keys()) == set(b.keys()), (
        f"R-F690: payload-shape drift: alias={set(a)} canonical={set(b)}"
    )
    assert a["status"] == b["status"] == "alive"


def test_rf690_route_is_mounted_at_app_level_no_auth():
    """The top-level alias must NOT require auth (Kubernetes probes
    don't send bearer tokens). It's mounted directly on `app`, not
    on the auth-protected router."""
    from aria_service.main import app
    paths = {r.path for r in app.routes if hasattr(r, "path")}
    assert "/health/live" in paths, (
        f"R-F690: top-level /health/live not mounted on app. "
        f"app routes: {sorted(p for p in paths if 'health' in p)}"
    )
