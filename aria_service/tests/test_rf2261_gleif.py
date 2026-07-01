"""R-F2261 — GLEIF global LEI-identity fallback in registry_adapters.

Free API-first structured registry data (datacenter-tolerant) used when a national
registry adapter returns nothing OR the jurisdiction has no adapter — fills the
"foreign entity, registry returned nothing" gap. Returns lookup_entity's contract shape.
"""
from __future__ import annotations
import asyncio
from pathlib import Path

import aria_service.intel.sources.gleif as gleif


class _Resp:
    status_code = 200
    def json(self):
        return {"data": [
            {"attributes": {"lei": "213800S8OBDOZMCMUW34", "entity": {
                "legalName": {"name": "QINETIQ GROUP PLC"}, "jurisdiction": "GB", "status": "ACTIVE",
                "legalAddress": {"city": "Farnborough", "country": "GB"}},
                "registration": {"initialRegistrationDate": "2012-06-01T00:00:00Z"}}},
            {"attributes": {"lei": "OTHER", "entity": {
                "legalName": {"name": "QINETIQ SOMETHING ELSE"}, "jurisdiction": "JE", "status": "INACTIVE",
                "legalAddress": {"city": "St Helier", "country": "JE"}}, "registration": {}}},
        ]}


class _Client:
    def __init__(self, *a, **k): pass
    async def __aenter__(self): return self
    async def __aexit__(self, *a): return False
    async def get(self, *a, **k): return _Resp()


def test_gleif_returns_registry_contract_shape(monkeypatch):
    monkeypatch.setattr(gleif.httpx, "AsyncClient", _Client)
    r = asyncio.run(gleif.lookup("QinetiQ Group PLC", "GB"))
    assert r is not None
    assert set(r.keys()) >= {"profile", "officers", "psc", "source_url", "adapter"}
    assert r["adapter"] == "gleif"
    # _best_match: exact-name + ACTIVE wins over the JE inactive one
    assert r["profile"]["company_name"] == "QINETIQ GROUP PLC"
    assert r["profile"]["company_number"] == "213800S8OBDOZMCMUW34"   # LEI as registry id
    assert r["profile"]["jurisdiction"] == "GB"
    # keys MUST match what dd_orchestrator reads off a registry profile
    assert r["profile"]["company_status"] == "active"
    assert "registered_office_address" in r["profile"] and "date_of_creation" in r["profile"]
    assert r["officers"] == [] and "gleif.org" in r["source_url"]


def test_short_query_and_no_records_return_none(monkeypatch):
    assert asyncio.run(gleif.lookup("ab")) is None  # too short
    class _Empty(_Client):
        async def get(self, *a, **k):
            class R:
                status_code = 200
                def json(self): return {"data": []}
            return R()
    monkeypatch.setattr(gleif.httpx, "AsyncClient", _Empty)
    assert asyncio.run(gleif.lookup("Nonexistent Entity Xyz")) is None


def test_lookup_entity_wires_the_gleif_fallback():
    src = (Path(__file__).resolve().parent.parent / "intel" / "registry_adapters.py").read_text(encoding="utf-8")
    assert "_gleif_global_fallback" in src
    assert "from .sources import gleif" in src
    # both fallback points: unsupported jurisdiction + adapter-returned-None
    assert src.count("_gleif_global_fallback(name, iso2, registration_number)") >= 2
