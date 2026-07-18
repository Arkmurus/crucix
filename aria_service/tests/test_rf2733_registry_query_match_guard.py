"""R-F2733 — DD Grade-A: query-match guard on the Saudi/Ghana registry scrapers.

_lookup_saudi_arabia / _lookup_ghana attached a scraped registration number / name
to the SUBJECT whenever the regex matched ANYTHING — with no check that the page
actually corroborated the query. A portal returning a different company (or a generic
page) would fabricate a subject identifier (the R-F2695 / R-F2703 honesty class). The
guard only attaches a scraped id/name when it corroborates the query; otherwise it
falls to the honest stub and records a "no confirmed match" gap.

These drive the REAL adapters with a mocked portal response.
"""
from __future__ import annotations

import asyncio

import aria_service.intel.registry_adapters as ra


class _FakeResp:
    def __init__(self, status: int, text: str):
        self.status_code = status
        self.text = text


class _FakeClient:
    def __init__(self, resp):
        self._resp = resp

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def get(self, *a, **k):
        return self._resp


def _patch_portal(monkeypatch, html: str, status: int = 200):
    monkeypatch.setattr(ra.httpx, "AsyncClient", lambda *a, **k: _FakeClient(_FakeResp(status, html)))


# ── Saudi (queried by CR number — the strong anchor) ────────────────────────

def test_rf2733_saudi_non_matching_page_does_not_fabricate_id(monkeypatch):
    # Portal returns a page for a DIFFERENT company (CR 9999999999) than queried.
    _patch_portal(monkeypatch,
                  "CR Number:<td>9999999999</td> Company Name:<td>SOME OTHER CO</td>")
    res = asyncio.run(ra._lookup_saudi_arabia("Target Co", "1010012345"))
    assert res["adapter"] == "saudi_moci_stub", "a non-matching page must NOT be attached as a real hit"
    assert res["profile"]["company_number"] == "1010012345", "the subject keeps its OWN queried id, not the page's"
    assert any("did NOT match" in g for g in res.get("data_gaps", [])), "the mismatch must be surfaced honestly"


def test_rf2733_saudi_matching_cr_is_attached(monkeypatch):
    _patch_portal(monkeypatch,
                  "CR Number:<td>1010012345</td> Company Name:<td>TARGET TRADING CO</td> "
                  "Status:<td>Active</td>")
    res = asyncio.run(ra._lookup_saudi_arabia("Target Co", "1010012345"))
    assert res["adapter"] == "saudi_moci", "a CR that matches the query is a real, attachable hit"
    assert res["profile"]["company_number"] == "1010012345"


# ── Ghana (queried by name — token overlap) ─────────────────────────────────

def test_rf2733_ghana_unrelated_name_not_attached(monkeypatch):
    _patch_portal(monkeypatch,
                  "Company Name:<td>ZENITH CORPORATION</td> Registration Number:<td>CS777777</td>")
    res = asyncio.run(ra._lookup_ghana("Acme Ventures", None))
    assert res["adapter"] == "ghana_rgd_stub", "a page whose name does not overlap the query must not attach"
    assert any("did NOT match" in g for g in res.get("data_gaps", []))


def test_rf2733_ghana_matching_name_is_attached(monkeypatch):
    _patch_portal(monkeypatch,
                  "Company Name:<td>ACME VENTURES LIMITED</td> Registration Number:<td>CS123456</td>")
    res = asyncio.run(ra._lookup_ghana("Acme Ventures", None))
    assert res["adapter"] == "ghana_rgd", "a name that overlaps the query is a real hit"
    assert res["profile"]["company_number"] == "CS123456"


# ── the guard unit ──────────────────────────────────────────────────────────

def test_rf2733_guard_contract():
    g = ra._scrape_confirms_query
    assert g(None, "1010012345", "X", "1010012345") is True
    assert g(None, "1010012345", "X", "9999999999") is False
    assert g(None, "1010012345", "X", "") is False           # absence is inconclusive
    assert g("Acme Trading Ltd", None, "Acme Trading Co", None) is True
    assert g("Global Holdings Ltd", None, "Prime Holdings Ltd", None) is False  # only generic overlap
    assert g("Acme Ltd", None, "", None) is False
