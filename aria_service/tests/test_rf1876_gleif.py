"""R-F1876 — GLEIF authoritative enrichment (direct-source-first DD R2).

Network-independent: a fake httpx client returns a canned GLEIF JSON record.
Proves (1) search_lei parses GLEIF's JSON:API shape into ARIA's flat record,
and (2) best_match is CONSERVATIVE — it accepts the right entity but REJECTS a
different one, which is what stops GLEIF injecting another company's
"authoritative" data (the failure mode behind the 2026-04-09 fabrication).
"""
from __future__ import annotations

import asyncio

import aria_service.intel.gleif as gleif


_GLEIF_JSON = {
    "data": [
        {
            "id": "529900T8BM49AURSDO55",
            "attributes": {
                "lei": "529900T8BM49AURSDO55",
                "entity": {
                    "legalName": {"name": "MODIRUM GESPI UNIPESSOAL LDA"},
                    "jurisdiction": "PT",
                    "status": "ACTIVE",
                    "registeredAs": "516394494",
                    "legalForm": {"id": "5RDO"},
                    "category": "GENERAL",
                    "legalAddress": {
                        "addressLines": ["Rua Actor Isidoro 9"],
                        "city": "Lisboa", "postalCode": "1900-019", "country": "PT",
                    },
                },
                "registration": {"status": "ISSUED"},
            },
        }
    ]
}


class _FakeResp:
    def __init__(self, payload, status=200):
        self._payload = payload
        self.status_code = status

    def json(self):
        return self._payload


class _FakeClient:
    def __init__(self, payload):
        self._payload = payload

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def get(self, url, params=None):
        return _FakeResp(self._payload)


def test_search_lei_parses_gleif_record(monkeypatch):
    import httpx
    monkeypatch.setattr(httpx, "AsyncClient", lambda *a, **k: _FakeClient(_GLEIF_JSON))
    hits = asyncio.run(gleif.search_lei("Modirum Gespi", country="PT"))
    assert len(hits) == 1
    h = hits[0]
    assert h["lei"] == "529900T8BM49AURSDO55"
    assert h["legal_name"] == "MODIRUM GESPI UNIPESSOAL LDA"
    assert h["jurisdiction"] == "PT"
    assert h["status"] == "ACTIVE"
    assert h["registered_as"] == "516394494"
    assert "Lisboa" in h["legal_address"]
    assert h["source_url"].endswith("529900T8BM49AURSDO55")


def test_best_match_accepts_right_entity():
    hits = [{"legal_name": "MODIRUM GESPI UNIPESSOAL LDA"}]
    # legal-form suffix differences must not block the match
    assert gleif.best_match("Modirum Gespi", hits) is hits[0]
    assert gleif.best_match("Modirum Gespi Lda", hits) is hits[0]


def test_best_match_rejects_different_entity():
    # A DIFFERENT company must NOT match — this is the fabrication guard.
    hits = [{"legal_name": "MODICORP HOLDINGS PLC"}]
    assert gleif.best_match("Modirum Gespi", hits) is None
    # partial-but-insufficient overlap also rejected
    hits2 = [{"legal_name": "Gespi Logistics International"}]
    assert gleif.best_match("Modirum Gespi", hits2) is None


def test_search_lei_empty_query_and_failure(monkeypatch):
    assert asyncio.run(gleif.search_lei("")) == []
    import httpx

    class _BoomClient(_FakeClient):
        async def get(self, url, params=None):
            raise RuntimeError("network down")

    monkeypatch.setattr(httpx, "AsyncClient", lambda *a, **k: _BoomClient(None))
    assert asyncio.run(gleif.search_lei("Acme Corp")) == []  # best-effort, no raise
