"""R-F2736 — complete the registry query-match-guard class: kenya + israel.

R-F2733 closed the parse-and-attach fabrication for saudi/ghana. _lookup_kenya
(HTML) and _lookup_israel (data.gov.il CKAN `q=` top-hit) had the IDENTICAL residual
— attach a scraped/searched identifier to the SUBJECT without confirming it matches
the query. This applies the same `_scrape_confirms_query` guard so no adapter can
fabricate a subject identifier from a non-matching page/record.
"""
from __future__ import annotations

import asyncio

import aria_service.intel.registry_adapters as ra


class _HtmlResp:
    def __init__(self, text: str, status: int = 200):
        self.status_code = status
        self.text = text


class _JsonResp:
    def __init__(self, payload: dict, status: int = 200):
        self.status_code = status
        self._payload = payload

    def json(self):
        return self._payload


class _Client:
    def __init__(self, resp):
        self._resp = resp

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def get(self, *a, **k):
        return self._resp


def _patch(monkeypatch, resp):
    monkeypatch.setattr(ra.httpx, "AsyncClient", lambda *a, **k: _Client(resp))


# ── Kenya (HTML) ────────────────────────────────────────────────────────────

def test_rf2736_kenya_unrelated_page_not_attached(monkeypatch):
    _patch(monkeypatch, _HtmlResp("Company Name:<td>ZENITH HOLDINGS</td> Registration Number:<td>PVT-9</td>"))
    res = asyncio.run(ra._lookup_kenya("Acme Ventures", None))
    assert res["adapter"] == "kenya_brs_stub"
    assert any("did NOT match" in g for g in res.get("data_gaps", []))


def test_rf2736_kenya_matching_name_attached(monkeypatch):
    _patch(monkeypatch, _HtmlResp("Company Name:<td>ACME VENTURES LIMITED</td> Status:<td>Active</td>"))
    res = asyncio.run(ra._lookup_kenya("Acme Ventures", None))
    assert res["adapter"] == "kenya_brs"


# ── Israel (CKAN JSON top-hit) ──────────────────────────────────────────────

def test_rf2736_israel_top_hit_not_matching_not_attached(monkeypatch):
    _patch(monkeypatch, _JsonResp({"result": {"records": [{"company_name": "ZENITH", "company_id": "514000000"}]}}))
    res = asyncio.run(ra._lookup_israel("Acme Ventures", None))
    assert res["adapter"] == "israel_registrar_stub", "a non-matching top CKAN hit must not be attached"
    assert any("did NOT match" in g for g in res.get("data_gaps", []))


def test_rf2736_israel_matching_reg_attached(monkeypatch):
    _patch(monkeypatch, _JsonResp({"result": {"records": [{"company_name": "ACME LTD", "company_id": "514123456"}]}}))
    res = asyncio.run(ra._lookup_israel("Acme", "514123456"))
    assert res["adapter"] == "israel_registrar_datagovil"
    assert res["profile"]["company_number"] == "514123456"
