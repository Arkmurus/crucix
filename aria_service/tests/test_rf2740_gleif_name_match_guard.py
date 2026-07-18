"""R-F2740 — GLEIF must not attach a non-matching best hit to the subject.

gleif.lookup does a fulltext name search and _best_match returns the top-SCORING
record — but score can come from ACTIVE status ALONE, so if none of the returned
records match the queried name, the top one (its LEI + national registry id) was
still attached to the subject: a fabricated identity that also mis-drives the
national-registry (KRS/SIREN) resolution. Now the best record must confirm the query
name before it is attached.
"""
from __future__ import annotations

import asyncio

import aria_service.intel.sources.gleif as g


def _rec(name: str, lei: str, status: str = "ACTIVE") -> dict:
    return {"attributes": {"lei": lei, "registration": {},
                           "entity": {"legalName": {"name": name}, "status": status,
                                      "jurisdiction": "GB"}}}


class _Resp:
    def __init__(self, data, status=200):
        self.status_code = status
        self._data = data

    def json(self):
        return self._data


class _Client:
    def __init__(self, resp):
        self._resp = resp

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def get(self, *a, **k):
        return self._resp


def _patch(monkeypatch, records):
    monkeypatch.setattr(g.httpx, "AsyncClient", lambda *a, **k: _Client(_Resp({"data": records})))
    # neutralise the circuit breaker so the test is deterministic
    class _CB:
        def is_open(self): return False
        def record_failure(self, **k): pass
        def record_success(self, **k): pass
    monkeypatch.setattr(g, "get_breaker", lambda *a, **k: _CB())


def test_rf2740_non_matching_best_hit_not_attached(monkeypatch):
    # fulltext returned active companies, NONE matching the query name
    _patch(monkeypatch, [_rec("ZENITH TRADING LTD", "LEI_ZEN"), _rec("ORION GLOBAL LLC", "LEI_ORI")])
    assert asyncio.run(g.lookup("Acme Ventures", "GB")) is None, \
        "a best hit that does not match the query name must NOT attach an LEI"


def test_rf2740_matching_best_hit_attached(monkeypatch):
    _patch(monkeypatch, [_rec("ZENITH TRADING LTD", "LEI_ZEN"), _rec("ACME VENTURES LIMITED", "LEI_ACME")])
    res = asyncio.run(g.lookup("Acme Ventures", "GB"))
    assert res is not None and res["profile"]["lei"] == "LEI_ACME"


def test_rf2740_name_confirms_contract():
    assert g._name_confirms("Acme Ventures Ltd", "ACME VENTURES LIMITED") is True
    assert g._name_confirms("Acme Ltd", "Zenith Corp") is False
    # generic suffixes alone are not a match
    assert g._name_confirms("Global Holdings Ltd", "Prime Holdings Ltd") is False
    assert g._name_confirms("Acme Ltd", "") is False
