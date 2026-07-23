"""R-F2939 — CZ and SK adapters use official JSON APIs and return the REAL name.

Both were live-but-broken: the or.justice.cz (CZ) and orsr.sk (SK) HTML scrapes had
drifted, so the name regex matched nothing and the code fell back to
`company_name = f"IČO {ico}"` — the LABEL as the company name. Live 2026-07-23 a CZ DD
showed entity_name "IČO " with an empty registration number; SK showed "IČO 31322832".

Migrated to the government JSON APIs (CZ ARES, SK RPO). These tests drive the PARSERS
against real API-shaped payloads (no network) and assert the exact failure — the label
as the name — can no longer happen.
"""
from __future__ import annotations

from aria_service.intel import registry_adapters as ra


# ── CZ ARES ────────────────────────────────────────────────────────────────

# ARES VR returns name/ico as HISTORY arrays; the current value is the entry with no
# datumVymazu. This is exactly the shape that made the first parse grab the whole array.
_ARES_SKODA = {
    "zaznamy": [{
        "obchodniJmeno": [
            {"datumZapisu": "2023-03-31", "hodnota": "Škoda Auto a.s."},
            {"datumZapisu": "1998-02-06", "datumVymazu": "2023-03-31", "hodnota": "ŠKODA AUTO a.s."},
        ],
        "ico": [{"datumZapisu": "1990-11-20", "hodnota": "00177041"}],
        "datumZapisu": "1990-11-20",
        "stavSubjektu": "AKTIVNI",
        "adresy": [{"adresa": {"textovaAdresa": "tř. Václava Klementa 869, 29301 Mladá Boleslav"}}],
        "statutarniOrgany": [{
            "clenoveOrganu": [
                {"datumZapisu": "2023-05-30", "nazevAngazma": "Člen statutárního orgánu",
                 "fyzickaOsoba": {"jmeno": "HOLGER", "prijmeni": "PETERS"}},
                {"datumZapisu": "2010-01-01", "datumVymazu": "2020-01-01",
                 "fyzickaOsoba": {"jmeno": "FORMER", "prijmeni": "PERSON"}},
            ],
        }],
    }],
}


def test_rf2939_cz_current_name_not_the_history_array():
    r = ra._parse_ares_vr(_ARES_SKODA, "00177041")
    assert r is not None
    p = r["profile"]
    assert p["company_name"] == "Škoda Auto a.s.", f"got {p['company_name']!r}"
    assert not p["company_name"].startswith("IČO"), "the label leaked in as the name"
    assert p["company_number"] == "00177041"
    assert p["date_of_creation"] == "1990-11-20"
    assert "Mladá Boleslav" in p["registered_office_address"]
    assert r["registry_status"] == "verified"
    assert r["adapter"] == "czech_ares"


def test_rf2939_cz_excludes_former_officers():
    r = ra._parse_ares_vr(_ARES_SKODA, "00177041")
    names = [o["name"] for o in r["officers"]]
    assert "HOLGER PETERS" in names
    assert "FORMER PERSON" not in names, "a removed (datumVymazu) officer was included as current"


def test_rf2939_cz_no_name_returns_none_never_fabricates():
    empty = {"zaznamy": [{"ico": [{"hodnota": "123"}]}]}
    assert ra._parse_ares_vr(empty, "123") is None


def test_rf2939_ares_current_picks_the_active_entry():
    hist = [
        {"datumZapisu": "2020-01-01", "datumVymazu": "2023-01-01", "hodnota": "OLD NAME"},
        {"datumZapisu": "2023-01-01", "hodnota": "CURRENT NAME"},
    ]
    assert ra._ares_current(hist) == "CURRENT NAME"
    assert ra._ares_current("plain string") == "plain string"
    assert ra._ares_current([]) == ""


# ── SK RPO ─────────────────────────────────────────────────────────────────

_RPO_SLOVNAFT = {
    "id": 1003617,
    "identifiers": [{"value": "31322832", "validFrom": "1992-05-01"}],
    "fullNames": [
        {"value": "SLOVNAFT, a.s.", "validFrom": "2006-06-16"},
        {"value": "SLOVNAFT , a.s.", "validFrom": "1992-05-01", "validTo": "2006-06-16"},
    ],
    "addresses": [{
        "validFrom": "2006-06-16", "street": "Vlčie hrdlo", "buildingNumber": "1",
        "postalCodes": ["82412"], "municipality": {"value": "Bratislava"},
    }],
    "establishment": "1992-05-01",
}


def test_rf2939_sk_current_name_and_clean_address():
    entity = ra._sk_best_entity([_RPO_SLOVNAFT], "SLOVNAFT", "31322832")
    r = ra._parse_sk_rpo(entity)
    p = r["profile"]
    assert p["company_name"] == "SLOVNAFT, a.s.", f"got {p['company_name']!r}"
    assert not p["company_name"].startswith("IČO")
    assert p["company_number"] == "31322832"
    assert p["date_of_creation"] == "1992-05-01"
    # The municipality {value:...} object must be flattened, not stringified as a dict.
    assert "Bratislava" in p["registered_office_address"]
    assert "{" not in p["registered_office_address"], "an address object leaked in as a dict repr"
    assert r["adapter"] == "slovakia_rpo"
    assert r["registry_status"] == "verified"


def test_rf2939_sk_ico_search_requires_an_exact_ico_match():
    # If we searched by IČO, only an entity carrying that IČO may be returned.
    wrong = {"identifiers": [{"value": "99999999"}], "fullNames": [{"value": "OTHER a.s."}]}
    assert ra._sk_best_entity([wrong], "SLOVNAFT", "31322832") is None


def test_rf2939_sk_ambiguous_name_returns_none_never_a_wrong_match():
    # Two different entities, no exact-normalised name match -> refuse (a wrong DD match
    # is worse than no match).
    a = {"identifiers": [{"value": "1"}], "fullNames": [{"value": "ACME ALPHA s.r.o."}]}
    b = {"identifiers": [{"value": "2"}], "fullNames": [{"value": "ACME BETA s.r.o."}]}
    assert ra._sk_best_entity([a, b], "ACME", "") is None


def test_rf2939_sk_normalised_name_matches_through_legal_suffix():
    # "SLOVNAFT" must resolve to "SLOVNAFT, a.s." (suffix + punctuation ignored).
    entity = ra._sk_best_entity([_RPO_SLOVNAFT], "SLOVNAFT", "")
    assert entity is _RPO_SLOVNAFT


# ── §3c — invoke the ENTRY POINTS, not just the parsers ─────────────────────
# The first cut of this file tested _parse_ares_vr / _parse_sk_rpo directly and passed
# while _lookup_czech raised NameError (_CZ_ARES_BASE had been collateral-deleted), so
# a CZ lookup silently fell through to a WRONG GLEIF entity in production. These tests
# drive the actual lookup entry points with a mocked transport, so a missing constant
# or a broken request path fails here, not in a DD report.

import asyncio
import types


class _FakeResp:
    def __init__(self, status, payload):
        self.status_code = status
        self._payload = payload
        self.content = b"{}"
    def json(self):
        return self._payload


class _FakeClient:
    """Minimal httpx.AsyncClient stand-in routing by URL substring."""
    def __init__(self, routes):
        self._routes = routes
    async def __aenter__(self):
        return self
    async def __aexit__(self, *a):
        return False
    def _match(self, url):
        for frag, resp in self._routes.items():
            if frag in url:
                return resp
        return _FakeResp(404, {})
    async def get(self, url, params=None, **kw):
        return self._match(url)
    async def post(self, url, json=None, **kw):
        return self._match(url)


def _patch_httpx(monkeypatch, routes):
    fake = _FakeClient(routes)
    monkeypatch.setattr(ra.httpx, "AsyncClient", lambda *a, **k: fake)


def test_rf2939_cz_entry_point_uses_ares_and_returns_the_company(monkeypatch):
    _patch_httpx(monkeypatch, {"ekonomicke-subjekty-vr/00177041": _FakeResp(200, _ARES_SKODA)})
    r = asyncio.run(ra._lookup_czech("SKODA AUTO", "00177041"))
    assert r is not None, "_lookup_czech returned None (NameError/constant regression would do this)"
    assert r["adapter"] == "czech_ares"
    assert r["profile"]["company_name"] == "Škoda Auto a.s."


def test_rf2939_sk_entry_point_uses_rpo_and_returns_the_company(monkeypatch):
    _patch_httpx(monkeypatch, {"/search": _FakeResp(200, {"results": [_RPO_SLOVNAFT]})})
    r = asyncio.run(ra._lookup_slovakia("SLOVNAFT", "31322832"))
    assert r is not None
    assert r["adapter"] == "slovakia_rpo"
    assert r["profile"]["company_name"] == "SLOVNAFT, a.s."


def test_rf2939_both_base_url_constants_are_defined():
    """The exact regression: a referenced module constant that was deleted."""
    assert isinstance(getattr(ra, "_CZ_ARES_BASE", None), str) and ra._CZ_ARES_BASE
    assert isinstance(getattr(ra, "_SK_RPO_BASE", None), str) and ra._SK_RPO_BASE
