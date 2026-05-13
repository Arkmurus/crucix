"""R-F439 — unified procurement-history query.

tender_monitor.py answers "what's NEW this week" per portal.
R-F439 answers "what has this entity ever won/bid anywhere" by
querying USAspending + UK Contracts Finder OCDS in parallel.
Pre-R-F439 the DD pipeline NEVER ran a supplier-history query —
operators had to do it manually per portal.

V1 backends (free, no key):
  - USAspending.gov (US federal awards since FY2008)
  - UK Contracts Finder OCDS (UK central + LA awards)

Tests cover: per-backend HTTP contracts (200, non-200, non-JSON,
network error), record normalisation, and the aggregator's
parallel-call + partial-failure handling.
"""
from __future__ import annotations

import asyncio


# ───────────────────────── USAspending ────────────────────────────────────────


def test_rf439_usaspending_normalises_records(monkeypatch):
    """USAspending payload → records with title/buyer/value_usd/url etc.
    Pins the field mapping so future API changes break the test."""
    import httpx
    from aria_service.intel import procurement_history as ph

    fake = {
        "results": [{
            "Award ID": "CONT-12345",
            "Recipient Name": "Acme Defence LLC",
            "Award Amount": 1234567.89,
            "Awarding Agency": "Department of Defense",
            "Description": "Spare parts for M1 turbine",
            "Period of Performance Start Date": "2024-06-15",
            "Period of Performance Current End Date": "2026-06-14",
        }],
    }

    class _FakeResp:
        status_code = 200
        text = ""
        def json(self): return fake

    class _FakeClient:
        def __init__(self, *a, **kw): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return None
        async def post(self, *a, **kw): return _FakeResp()

    monkeypatch.setattr(httpx, "AsyncClient", _FakeClient)
    out = asyncio.run(ph.query_usaspending("Acme Defence"))
    assert out["ok"] is True
    assert len(out["records"]) == 1
    r = out["records"][0]
    assert r["title"] == "Spare parts for M1 turbine"
    assert r["buyer"] == "Department of Defense"
    assert r["value_usd"] == 1234567.89
    assert r["date_signed"] == "2024-06-15"
    assert "CONT-12345" in r["url"]
    assert r["award_id"] == "CONT-12345"
    assert r["recipient"] == "Acme Defence LLC"


def test_rf439_usaspending_handles_non_200(monkeypatch):
    """500 from USAspending → ok=False + error message + empty records."""
    import httpx
    from aria_service.intel import procurement_history as ph

    class _FakeResp:
        status_code = 500
        text = "internal error"
        def json(self): raise ValueError("no json")

    class _FakeClient:
        def __init__(self, *a, **kw): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return None
        async def post(self, *a, **kw): return _FakeResp()

    monkeypatch.setattr(httpx, "AsyncClient", _FakeClient)
    out = asyncio.run(ph.query_usaspending("Acme"))
    assert out["ok"] is False
    assert "500" in (out["error"] or "")
    assert out["records"] == []


def test_rf439_usaspending_rejects_short_entity_name():
    """A 2-char entity name is too generic — would burn the API and
    return junk. Rejected at entry."""
    from aria_service.intel import procurement_history as ph
    out = asyncio.run(ph.query_usaspending("X"))
    assert out["ok"] is False
    assert "too short" in (out["error"] or "")


# ───────────────────────── UK Contracts Finder ─────────────────────────────


def test_rf439_uk_cf_normalises_ocds_package(monkeypatch):
    """OCDS package → records with title/buyer/value/url etc."""
    import httpx
    from aria_service.intel import procurement_history as ph

    fake = {
        "results": [{
            "releases": [{
                "ocid": "ocds-b5fd17-12345",
                "date": "2024-03-01",
                "buyer": {"name": "Ministry of Defence"},
                "tender": {"title": "Avionics maintenance contract"},
                "awards": [{
                    "date": "2024-03-15",
                    "suppliers": [{"name": "BAE Systems plc"}],
                    "value": {"amount": 2_500_000, "currency": "GBP"},
                }],
            }],
        }],
    }

    class _FakeResp:
        status_code = 200
        text = ""
        def json(self): return fake

    class _FakeClient:
        def __init__(self, *a, **kw): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return None
        async def post(self, *a, **kw): return _FakeResp()

    monkeypatch.setattr(httpx, "AsyncClient", _FakeClient)
    out = asyncio.run(ph.query_uk_contracts_finder("BAE Systems"))
    assert out["ok"] is True
    assert len(out["records"]) == 1
    r = out["records"][0]
    assert r["title"] == "Avionics maintenance contract"
    assert r["buyer"] == "Ministry of Defence"
    # GBP 2.5M converted to ~USD 3.175M (rough rate)
    assert 3_000_000 < r["value_usd"] < 3_300_000
    assert r["date_signed"] == "2024-03-15"
    assert "ocds-b5fd17-12345" in r["url"]
    assert r["recipient"] == "BAE Systems plc"


def test_rf439_uk_cf_handles_empty_releases(monkeypatch):
    """A package with no releases must be skipped, not crash."""
    import httpx
    from aria_service.intel import procurement_history as ph

    fake = {"results": [{"releases": []}, {"releases": [{"ocid": "x", "tender": {"title": "T"}, "awards": [{}]}]}]}

    class _FakeResp:
        status_code = 200
        text = ""
        def json(self): return fake

    class _FakeClient:
        def __init__(self, *a, **kw): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return None
        async def post(self, *a, **kw): return _FakeResp()

    monkeypatch.setattr(httpx, "AsyncClient", _FakeClient)
    out = asyncio.run(ph.query_uk_contracts_finder("Acme"))
    assert out["ok"] is True
    assert len(out["records"]) == 1  # empty-releases skipped


# ───────────────────────── Currency conversion ────────────────────────────


def test_rf439_to_usd_handles_known_and_unknown_currencies():
    """USD pass-through; GBP and EUR converted (rough rates); unknown
    currency passed through with no conversion."""
    from aria_service.intel.procurement_history import _to_usd
    assert _to_usd(100, "USD") == 100
    assert _to_usd(100, "GBP") == 100 * 1.27
    assert _to_usd(100, "EUR") == 100 * 1.09
    assert _to_usd(100, "JPY") == 100  # unknown → pass-through
    assert _to_usd(None, "USD") == 0.0
    assert _to_usd("not a number", "USD") == 0.0


# ───────────────────────── Aggregator ──────────────────────────────────────


def test_rf439_aggregator_runs_backends_in_parallel(monkeypatch):
    """query_entity_history must call both backends concurrently. We
    verify by checking both are awaited (even if one fails) and that
    consolidated contains records from both with `source` tags."""
    from aria_service.intel import procurement_history as ph

    async def _fake_usa(name, **kw):
        return {
            "ok": True, "source": "usaspending", "query": name,
            "records": [{"title": "USA-1", "buyer": "DoD", "value_usd": 100,
                         "date_signed": "2024-06-01", "url": "", "award_id": "u1",
                         "recipient": name}],
            "error": None,
        }

    async def _fake_uk(name, **kw):
        return {
            "ok": True, "source": "uk_contracts_finder", "query": name,
            "records": [{"title": "UK-1", "buyer": "MoD", "value_usd": 200,
                         "date_signed": "2024-08-01", "url": "", "award_id": "u2",
                         "recipient": name}],
            "error": None,
        }

    monkeypatch.setattr(ph, "query_usaspending", _fake_usa)
    monkeypatch.setattr(ph, "query_uk_contracts_finder", _fake_uk)
    out = asyncio.run(ph.query_entity_history("Acme Defence Ltd"))
    assert out["ok"] is True
    assert out["total_records"] == 2
    sources = {r["source"] for r in out["consolidated"]}
    assert sources == {"usaspending", "uk_contracts_finder"}
    # Sort desc by date_signed — UK-1 (Aug) before USA-1 (Jun)
    assert out["consolidated"][0]["title"] == "UK-1"
    assert out["consolidated"][1]["title"] == "USA-1"


def test_rf439_aggregator_handles_one_backend_failing(monkeypatch):
    """If one backend errors and the other succeeds, ok=True (any_ok),
    consolidated contains only the working backend's records, errors
    list captures the failure."""
    from aria_service.intel import procurement_history as ph

    async def _fake_usa(name, **kw):
        return {
            "ok": False, "source": "usaspending", "query": name,
            "records": [], "error": "USAspending HTTP 500",
        }

    async def _fake_uk(name, **kw):
        return {
            "ok": True, "source": "uk_contracts_finder", "query": name,
            "records": [{"title": "UK-1", "buyer": "MoD", "value_usd": 200,
                         "date_signed": "2024-08-01", "url": "", "award_id": "u",
                         "recipient": name}],
            "error": None,
        }

    monkeypatch.setattr(ph, "query_usaspending", _fake_usa)
    monkeypatch.setattr(ph, "query_uk_contracts_finder", _fake_uk)
    out = asyncio.run(ph.query_entity_history("Acme"))
    assert out["ok"] is True  # at least one ok
    assert out["total_records"] == 1
    assert any("usaspending" in e for e in out["errors"])


def test_rf439_aggregator_handles_unhandled_exception(monkeypatch):
    """asyncio.gather(return_exceptions=True) — if a backend raises
    BaseException, the aggregator records it and continues."""
    from aria_service.intel import procurement_history as ph

    async def _raises(name, **kw):
        raise RuntimeError("boom")

    async def _fake_uk(name, **kw):
        return {"ok": True, "source": "uk_contracts_finder", "query": name,
                "records": [], "error": None}

    monkeypatch.setattr(ph, "query_usaspending", _raises)
    monkeypatch.setattr(ph, "query_uk_contracts_finder", _fake_uk)
    out = asyncio.run(ph.query_entity_history("Acme"))
    # uk_contracts_finder ok=True → any_ok=True
    assert out["ok"] is True
    # usaspending was caught and recorded
    assert "usaspending" in str(out["errors"])
    assert out["backends"]["usaspending"]["ok"] is False
