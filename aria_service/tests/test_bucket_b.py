"""Tests for the Bucket B quick wins (2026-04-17):

  - Companies House PSC-reverse lookup (search_officers / psc_reverse_lookup)
  - OpenSanctions family/associate edge enrichment
  - SIPRI CSV ingest

HTTP calls are monkeypatched so these tests run offline against the
redis_store in-memory fallback.
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

import pytest

from aria_service.intel import redis_store as rs


@pytest.fixture(autouse=True)
def _force_memory_backend():
    rs._client = None
    rs._mem_store.clear()
    yield
    rs._mem_store.clear()


def _run(coro):
    return asyncio.run(coro)


# ═══════════════════════════════════════════════════════════════════════════
# Companies House — PSC-reverse
# ═══════════════════════════════════════════════════════════════════════════

class TestCompaniesHousePscReverse:
    def test_psc_reverse_lookup_resolves_name_to_companies(self, monkeypatch):
        from aria_service.intel import companies_house as ch

        async def fake_get(path: str):
            if path.startswith("/search/officers"):
                return {"items": [{
                    "title": "SMITH, John",
                    "address_snippet": "1 High Street, London",
                    "description": "Born 1975",
                    "date_of_birth": {"month": 6, "year": 1975},
                    "links": {"self": "/officers/abc123xyz/appointments"},
                }]}
            if path.startswith("/officers/abc123xyz/appointments"):
                return {"items": [
                    {
                        "appointed_to": {
                            "company_name": "ACME CO LTD",
                            "company_number": "01234567",
                            "company_status": "active",
                        },
                        "officer_role": "director",
                        "appointed_on": "2020-01-15",
                        "resigned_on": None,
                    },
                    {
                        "appointed_to": {
                            "company_name": "ACME HOLDINGS LTD",
                            "company_number": "07654321",
                            "company_status": "dissolved",
                        },
                        "officer_role": "director",
                        "appointed_on": "2015-06-01",
                        "resigned_on": "2019-12-30",
                    },
                ]}
            return None

        monkeypatch.setattr(ch, "_get", fake_get)
        result = _run(ch.psc_reverse_lookup("John Smith"))
        assert result["companies_total"] == 2
        assert result["appointments_current"] == 1
        assert result["candidates"][0]["officer_id"] == "abc123xyz"
        assert any(a["company_number"] == "01234567" for a in result["appointments"])

    def test_psc_reverse_lookup_handles_no_matches(self, monkeypatch):
        from aria_service.intel import companies_house as ch

        async def fake_get(path: str):
            if path.startswith("/search/officers"):
                return {"items": []}
            return None

        monkeypatch.setattr(ch, "_get", fake_get)
        result = _run(ch.psc_reverse_lookup("Zzzz Nonexistent"))
        assert result["candidates"] == []
        assert result["appointments"] == []
        assert "No matching" in result["summary"]

    def test_psc_reverse_lookup_flags_multiple_candidates(self, monkeypatch):
        from aria_service.intel import companies_house as ch

        async def fake_get(path: str):
            if path.startswith("/search/officers"):
                return {"items": [
                    {"title": "SMITH, John", "links": {"self": "/officers/id1/appointments"}},
                    {"title": "SMITH, John A", "links": {"self": "/officers/id2/appointments"}},
                    {"title": "SMITH, John B", "links": {"self": "/officers/id3/appointments"}},
                ]}
            if path.startswith("/officers/"):
                return {"items": []}
            return None

        monkeypatch.setattr(ch, "_get", fake_get)
        result = _run(ch.psc_reverse_lookup("John Smith", max_officers=3))
        assert len(result["candidates"]) == 3
        assert "Multiple candidates" in result["summary"]


# ═══════════════════════════════════════════════════════════════════════════
# OpenSanctions — family/associate enrichment
# ═══════════════════════════════════════════════════════════════════════════

class TestSanctionsRelationshipEnrichment:
    def test_enrich_attaches_inherited_risk(self, monkeypatch):
        from aria_service.intel import sanctions as s
        from aria_service.intel.sanctions import _SourceQuery

        async def fake_search(query: str, limit: int = 5):
            if "ivan" in query.lower():
                return _SourceQuery([{
                    "id": "Q12345",
                    "properties": {"name": ["Ivan Ivanov"], "topics": ["sanction"]},
                    "datasets": ["eu_fsf", "ofac_sdn"],
                    "caption": "Ivan Ivanov",
                }], True, "ok")
            return _SourceQuery([], True, "ok")

        monkeypatch.setattr(s, "_opensanctions_search", fake_search)
        screen = {
            "matches": [{
                "name": "Target Entity",
                "score": 0.95,
                "relationships": [
                    {"kind": "spouseOf", "target": "Ivan Ivanov"},
                ],
            }],
        }
        out = _run(s.enrich_with_relationships(screen))
        assert out["relationships_enriched"] is True
        assert out["inherited_risk_count"] == 1
        match = out["matches"][0]
        assert match.get("inherited_risk")
        inherited = match["inherited_risk"][0]
        assert inherited["kind"] == "spouseOf"
        assert inherited["target_name"] == "Ivan Ivanov"
        assert "eu_fsf" in inherited["target_lists"]

    def test_enrich_skips_unmatched_targets(self, monkeypatch):
        from aria_service.intel import sanctions as s

        async def fake_search(query: str, limit: int = 5):
            return []  # no hits — nothing to inherit

        monkeypatch.setattr(s, "_opensanctions_search", fake_search)
        screen = {
            "matches": [{
                "name": "Target",
                "score": 0.9,
                "relationships": [
                    {"kind": "associateOf", "target": "Someone Unknown"},
                ],
            }],
        }
        out = _run(s.enrich_with_relationships(screen))
        assert out["inherited_risk_count"] == 0
        match = out["matches"][0]
        assert "inherited_risk" not in match

    def test_enrich_caps_targets_per_match(self, monkeypatch):
        from aria_service.intel import sanctions as s

        call_count = {"n": 0}

        async def fake_search(query: str, limit: int = 5):
            call_count["n"] += 1
            return []

        monkeypatch.setattr(s, "_opensanctions_search", fake_search)
        relationships = [
            {"kind": "associateOf", "target": f"Associate {i}"} for i in range(10)
        ]
        screen = {"matches": [{"name": "T", "score": 0.9, "relationships": relationships}]}
        _run(s.enrich_with_relationships(screen, max_targets=3))
        assert call_count["n"] == 3

    def test_enrich_empty_screen_is_noop(self):
        from aria_service.intel import sanctions as s
        assert _run(s.enrich_with_relationships({})) == {}
        assert _run(s.enrich_with_relationships({"matches": []})) == {"matches": []}


# ═══════════════════════════════════════════════════════════════════════════
# SIPRI ingest
# ═══════════════════════════════════════════════════════════════════════════

class TestSipriIngest:
    def test_ingest_csv_bytes_parses_rows(self, monkeypatch):
        from aria_service.intel import sipri_ingest as si
        from aria_service.intel import corpus_ingest as ci

        ingested_rows: list[dict] = []

        # R-F3376 — ingest now REQUIRES a rights declaration. The stub must accept
        # it or every row TypeErrors into sipri's try/except and `ingested` is 0.
        async def fake_ingest_corpus_document(text, *, filename, tier, source_class,
                                              region="", cplp_relevant=False,
                                              confidence="", publication_date="",
                                              notes="", rights="", rights_note="",
                                              extra_metadata=None):
            assert rights, "sipri_ingest must declare rights (R-F3376)"
            ingested_rows.append({
                "text": text, "filename": filename, "tier": tier, "rights": rights,
                "source_class": source_class, "region": region,
                "cplp_relevant": cplp_relevant,
                "extra_metadata": extra_metadata or {},
            })
            return {"chunks": 1, "source": filename}

        monkeypatch.setattr(ci, "ingest_corpus_document", fake_ingest_corpus_document)

        csv_blob = (
            b"Year,Recipient,Supplier,Weapon designation,Weapon category,"
            b"Number delivered,Number ordered,Status,Comments\n"
            b"2024,Angola,Turkey,Bayraktar TB2,UAV,6,6,Delivered,Test\n"
            b"2023,Nigeria,China,VT-4,Main battle tank,20,40,Delivered,Partial\n"
            b"2022,Brazil,Sweden,Gripen E,Fighter aircraft,4,36,Delivered,\n"
        )
        result = _run(si.ingest_csv_bytes(csv_blob, since_year=2020))
        assert result["ingested"] == 3
        assert result["by_category"]["aircraft"] == 2
        assert result["by_category"]["armoured"] == 1
        assert "Angola" in result["by_recipient_top10"]
        # Tier + source_class propagate
        assert all(r["tier"] == "A" for r in ingested_rows)
        assert all(r["source_class"] == "SIPRI Arms Transfers Database" for r in ingested_rows)
        # CPLP flag correctly attached to Angola + Brazil
        angola_row = next(r for r in ingested_rows if "Angola" in r["region"])
        assert angola_row["cplp_relevant"] is True

    def test_since_year_skips_older_rows(self, monkeypatch):
        from aria_service.intel import sipri_ingest as si
        from aria_service.intel import corpus_ingest as ci

        async def fake_ingest(text, **kwargs):
            return {"chunks": 1}

        monkeypatch.setattr(ci, "ingest_corpus_document", fake_ingest)

        csv_blob = (
            b"Year,Recipient,Supplier,Weapon designation,Weapon category,Status\n"
            b"2015,Angola,Russia,T-72B,Main battle tank,Delivered\n"
            b"2024,Angola,Turkey,Bayraktar TB2,UAV,Delivered\n"
        )
        result = _run(si.ingest_csv_bytes(csv_blob, since_year=2020))
        assert result["ingested"] == 1
        assert result["skipped_stale"] == 1

    def test_fetch_and_ingest_degrades_cleanly(self, monkeypatch):
        from aria_service.intel import sipri_ingest as si

        # Force all HTTP attempts to fail
        class _FailClient:
            def __init__(self, *a, **kw): pass
            async def __aenter__(self): return self
            async def __aexit__(self, *a): return False
            async def get(self, *a, **kw):
                raise RuntimeError("network down")

        monkeypatch.setattr("httpx.AsyncClient", _FailClient)
        result = _run(si.fetch_and_ingest_sipri())
        assert result["ingested"] == 0
        assert "operator must download" in result["message"].lower()

    def test_classify_category_aircraft(self):
        from aria_service.intel import sipri_ingest as si
        assert si._classify_category("UAV", "Bayraktar TB2") == "aircraft"
        assert si._classify_category("Fighter aircraft", "Gripen E") == "aircraft"
        assert si._classify_category("Main battle tank", "VT-4") == "armoured"
        assert si._classify_category("Frigate", "MEKO A-200") == "naval"
        assert si._classify_category("Random", "Mystery widget") == "other"

    def test_cplp_relevant(self):
        from aria_service.intel import sipri_ingest as si
        assert si._cplp_relevant("Angola") is True
        assert si._cplp_relevant("Brazil") is True
        assert si._cplp_relevant("Poland") is False


# ═══════════════════════════════════════════════════════════════════════════
# Smoke: new modules are importable
# ═══════════════════════════════════════════════════════════════════════════

def test_sipri_ingest_importable():
    import aria_service.intel.sipri_ingest  # noqa
