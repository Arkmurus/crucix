"""R-F2737 — close the registry parse-and-attach fabrication class.

R-F2733/2736 guarded saudi/ghana/kenya/israel. An audit of ALL `_lookup_*` adapters
found 5 more that do a `q=`/name SEARCH, parse the result HTML, and attach an
identifier to the SUBJECT with no query-match guard: gibraltar, turkey, nigeria,
india, hungary. (Gibraltar was worst: it returned a "hit" for ANY page — even a
no-results page — with scraped officers attached.) Each now attaches only when the
result corroborates the query. Hungary's exact-key cégjegyzékszám URL is trusted
(the key IS the corroboration); only its NAME search is guarded.
"""
from __future__ import annotations

import asyncio

import aria_service.intel.registry_adapters as ra


class _Resp:
    def __init__(self, text: str, status: int = 200):
        self.status_code = status
        self.text = text


class _Client:
    def __init__(self, resp):
        self._resp = resp

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def get(self, *a, **k):
        return self._resp


def _patch(monkeypatch, html: str):
    monkeypatch.setattr(ra.httpx, "AsyncClient", lambda *a, **k: _Client(_Resp(html)))


# (fn, non_matching_html, matching_html, matched_adapter)
_CASES = [
    (ra._lookup_nigeria, "Company Name:<td>ZENITH LTD</td> RC Number:<td>RC99</td>",
     "Company Name:<td>ACME VENTURES LTD</td> RC Number:<td>RC12345</td>", "nigeria_cac"),
    (ra._lookup_india, "Company Name:<td>ZENITH</td> CIN:<td>U74999MH2020</td>",
     "Company Name:<td>ACME VENTURES LTD</td> CIN:<td>U74999MH2020PLC</td>", "india_mca"),
    (ra._lookup_gibraltar, "<h1>Search Results</h1> Company Number: 55555",
     "<b>ACME VENTURES LIMITED</b> Company Number: 12345", "gibraltar_ch"),
    (ra._lookup_turkey, "Unvan: ZENITH SANAYI",
     "Unvan: ACME VENTURES ANONIM", "turkey_mersis"),
    (ra._lookup_hungary, "Cégnév:<td>ZENITH KFT</td> Székhely:<td>Budapest</td>",
     "Cégnév:<td>ACME VENTURES KFT</td> Székhely:<td>Budapest</td>", "hungary_e_cegjegyzek"),
]


def test_rf2737_non_matching_page_never_attaches(monkeypatch):
    for fn, non_match_html, _m, _a in _CASES:
        _patch(monkeypatch, non_match_html)
        res = asyncio.run(fn("Acme Ventures", None))
        # unconfirmed → None or a stub, but NEVER a real registry hit for a wrong company
        assert res is None or res.get("adapter", "").endswith("_stub"), \
            f"{fn.__name__} attached a non-matching page: {res and res.get('adapter')}"


def test_rf2737_matching_page_attaches(monkeypatch):
    for fn, _n, match_html, adapter in _CASES:
        _patch(monkeypatch, match_html)
        res = asyncio.run(fn("Acme Ventures", None))
        assert res is not None and res.get("adapter") == adapter, \
            f"{fn.__name__} should attach a matching page, got {res and res.get('adapter')}"


def test_rf2737_hungary_exact_key_lookup_is_trusted(monkeypatch):
    """A cégjegyzékszám-keyed URL is an EXACT lookup — trusted even if the page name
    text differs (the key is the corroboration). Only NAME search needs the guard."""
    _patch(monkeypatch, "Cégnév:<td>SOME COMPANY KFT</td> Székhely:<td>Budapest</td>")
    res = asyncio.run(ra._lookup_hungary("", "01-10-046896"))
    assert res is not None and res["adapter"] == "hungary_e_cegjegyzek"


def test_rf2737_gibraltar_no_results_page_no_longer_fabricates(monkeypatch):
    """The worst case: gibraltar returned a hit for ANY page. A no-results page with an
    unrelated bold heading must NOT attach a fabricated company to the subject."""
    _patch(monkeypatch, "<b>No companies found matching your search</b>")
    assert asyncio.run(ra._lookup_gibraltar("Acme Ventures", None)) is None
