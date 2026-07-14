"""R-F2597 — web_integrity_agent false 0% success.

The agent probed real endpoints with STALE `expected` field names, so every cycle
failed on a 200 response ("Missing expected field") → no clean cycle → the watched
`web_integrity_agent` metric sat at 0% success (1375 fails) while the pages served
fine. Live-verified real fields: unread-count → `unread_count` (not `count`);
cost/monthly/status → `spent_usd`/`cap_usd` (not `total`/`monthly_cap`).

These drive the actual check_endpoint expected-field logic with the REAL response
shape and assert it PASSES — §23-discriminating: with the pre-R-F2597 stale defs
the same responses fail on "Missing expected field".
"""
from __future__ import annotations

import asyncio

from aria_service.intel import web_integrity_agent as W


class _Resp:
    def __init__(self, data, code=200):
        self.status_code = code
        self._data = data

    @property
    def is_success(self):
        return 200 <= self.status_code < 300

    def json(self):
        return self._data


class _Client:
    _resp = _Resp({})

    def __init__(self, *a, **k):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def get(self, url, headers=None):
        return _Client._resp

    async def post(self, url, json=None, headers=None):
        return _Client._resp


def _ep(path):
    return next(e for e in W.WEB_ENDPOINTS if e["path"] == path)


def test_rf2597_unread_count_passes_on_real_field(monkeypatch):
    import httpx
    _Client._resp = _Resp({"unread_count": 3, "user_id": "x", "since_hours": 24})
    monkeypatch.setattr(httpx, "AsyncClient", _Client)
    check = asyncio.run(W.check_endpoint(_ep("/api/aria/dd/watchlist/alerts/unread-count")))
    assert check.passed is True, check.errors
    assert not check.errors


def test_rf2597_cost_status_passes_on_real_fields(monkeypatch):
    import httpx
    _Client._resp = _Resp({
        "month": "2026-07", "spent_usd": 12.0, "cap_usd": 300.0,
        "remaining_usd": 288.0, "utilisation_pct": 4.0, "warn_only": False,
    })
    monkeypatch.setattr(httpx, "AsyncClient", _Client)
    check = asyncio.run(W.check_endpoint(_ep("/api/aria/cost/monthly/status")))
    assert check.passed is True, check.errors


def test_rf2597_expected_defs_are_the_real_fields():
    # Re-drift guard: the corrected field names must be present, the stale ones gone.
    assert _ep("/api/aria/dd/watchlist/alerts/unread-count")["expected"] == {"unread_count"}
    assert _ep("/api/aria/cost/monthly/status")["expected"] == {"spent_usd", "cap_usd"}


if __name__ == "__main__":
    import sys
    import pytest
    sys.exit(pytest.main([__file__, "-v"]))
