"""R-F473 — /read-document latency instrumentation.

Other-agent audit (next_session_pickup_2026_05_15 follow-up): the
/read-document endpoint takes 80s end-to-end on some payloads and
per-stage timing was uncaptured. Instead of guessing the lever (base64
decode / file-type detect / per-format parser / OCR fallback / fact
extraction), R-F473 instruments the endpoint with in-process counters
+ a WARNING log when total > 30s.

This test pins:
  1. The instrumentation helper increments tallies correctly
  2. Total / max / slow_calls counters track honestly
  3. The /api/aria/read-document/stats endpoint returns the expected
     shape
"""
from __future__ import annotations

import pytest


def test_rf473_record_helper_increments():
    """The helper increments total_calls, total_seconds, and slow_calls
    correctly. Fast calls don't trip the slow counter; slow ones do."""
    from aria_service.routes import aria as _aria
    # Snapshot baselines
    base_total = _aria._R473_RD_TOTAL_CALLS
    base_slow = _aria._R473_RD_SLOW_CALLS
    base_seconds = _aria._R473_RD_TOTAL_S
    base_max = _aria._R473_RD_MAX_S

    # Fast call — should NOT bump slow_calls
    _aria._r473_record_read_doc(1.5, "fast.pdf", 1000, "pdf")
    assert _aria._R473_RD_TOTAL_CALLS == base_total + 1
    assert _aria._R473_RD_SLOW_CALLS == base_slow, "fast call must not bump slow"
    assert _aria._R473_RD_TOTAL_S - base_seconds == pytest.approx(1.5, rel=0.01)

    # Slow call — should bump slow_calls AND max_s
    _aria._r473_record_read_doc(45.0, "slow_giant.pdf", 8_500_000, "pdf")
    assert _aria._R473_RD_SLOW_CALLS == base_slow + 1, "slow call (>30s) must bump slow"
    assert _aria._R473_RD_MAX_S >= 45.0, "max_s must track the slowest call"
    # Filename of the slowest call gets stored
    if base_max <= 45.0:
        assert _aria._R473_RD_MAX_FILENAME == "slow_giant.pdf"


def _auth_header() -> dict:
    """Return the Authorization header needed by the router auth dependency."""
    import os as _os
    token = _os.environ.get("ARIA_API_TOKEN") or _os.environ.get("ARIA_INTERNAL_TOKEN") or ""
    if token:
        return {"Authorization": f"Bearer {token}"}
    return {}


def test_rf473_stats_endpoint_shape():
    """/read-document/stats returns the expected fields."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from aria_service.routes.aria import router as aria_router
    app = FastAPI()
    app.include_router(aria_router)

    # Drive at least one call so divide-by-zero is exercised either way
    from aria_service.routes import aria as _aria
    _aria._r473_record_read_doc(2.0, "test.pdf", 1234, "pdf")

    with TestClient(app) as client:
        r = client.get("/api/aria/read-document/stats", headers=_auth_header())
    assert r.status_code == 200, f"stats endpoint must respond 200, got {r.status_code}"
    data = r.json()
    expected_keys = {
        "total_calls", "total_seconds", "average_s",
        "max_s", "max_filename", "slow_calls", "slow_rate",
        "slow_threshold_s",
    }
    missing = expected_keys - set(data.keys())
    assert not missing, f"R-F473 stats shape missing keys: {missing}"
    assert data["slow_threshold_s"] == 30.0


def test_rf473_zero_calls_safe():
    """The endpoint must not divide-by-zero before any calls have run.
    Fresh process state should return zeros honestly, not crash."""
    # Force a clean slate by re-importing in a sub-namespace
    import importlib
    from aria_service.routes import aria as _aria
    _aria._R473_RD_TOTAL_CALLS = 0
    _aria._R473_RD_TOTAL_S = 0.0
    _aria._R473_RD_SLOW_CALLS = 0
    _aria._R473_RD_MAX_S = 0.0
    _aria._R473_RD_MAX_FILENAME = ""

    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    app = FastAPI()
    app.include_router(_aria.router)

    with TestClient(app) as client:
        r = client.get("/api/aria/read-document/stats", headers=_auth_header())
    assert r.status_code == 200
    data = r.json()
    assert data["total_calls"] == 0
    assert data["average_s"] == 0
    assert data["slow_rate"] == 0


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
