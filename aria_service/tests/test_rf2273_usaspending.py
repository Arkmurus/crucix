"""R-F2273 — USASpending procurement leg: US federal contract summary for a DD."""
from __future__ import annotations
import asyncio
from aria_service.intel.sources import usaspending as us


class _R:
    status_code = 200
    def json(self):
        return {"results": [
            {"Award ID": "N001", "Recipient Name": "LOCKHEED MARTIN CORP", "Award Amount": "35135514910.2",
             "Awarding Agency": "Department of Defense", "Period of Performance Start Date": "2017-01-01"},
            {"Award ID": "D002", "Recipient Name": "LOCKHEED MARTIN CORP", "Award Amount": "48063763681.32",
             "Awarding Agency": "Department of Energy", "Period of Performance Start Date": "1994-01-01"},
        ]}


class _C:
    def __init__(self, *a, **k): pass
    async def __aenter__(self): return self
    async def __aexit__(self, *a): return False
    async def post(self, *a, **k): return _R()


def test_usaspending_summarises_federal_contracts(monkeypatch):
    monkeypatch.setattr(us.httpx, "AsyncClient", _C)
    r = asyncio.run(us.lookup("Lockheed Martin"))
    assert r is not None
    assert r["adapter"] == "usaspending"
    assert r["award_count"] == 2
    assert round(r["total_value_usd"]) == round(35135514910.2 + 48063763681.32)
    assert "Department of Defense" in r["top_agencies"]
    assert r["awards"][0]["award_id"] == "N001"


def test_no_awards_returns_none_not_error(monkeypatch):
    class _Empty(_C):
        async def post(self, *a, **k):
            class R:
                status_code = 200
                def json(self): return {"results": []}
            return R()
    monkeypatch.setattr(us.httpx, "AsyncClient", _Empty)
    assert asyncio.run(us.lookup("No Federal Contracts Ltd")) is None
    assert asyncio.run(us.lookup("ab")) is None  # too short
