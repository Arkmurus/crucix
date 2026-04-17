"""Tests for the Tier 1 regional knowledge modules + equipment_specs +
the async ground_truth_loop wrapper + the ZA/IL registry adapters.

All offline — no external HTTP calls. redis_store in-memory fallback."""
from __future__ import annotations

import asyncio

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
# knowledge_gulf
# ═══════════════════════════════════════════════════════════════════════════

class TestKnowledgeGulf:
    def test_context_on_gulf_keywords(self):
        from aria_service.intel import knowledge_gulf
        ctx = knowledge_gulf.get_gulf_context("what's the UAE EDGE Group structure?")
        assert "EDGE" in ctx or "UAE" in ctx or "GULF" in ctx

    def test_context_empty_on_unrelated(self):
        from aria_service.intel import knowledge_gulf
        assert knowledge_gulf.get_gulf_context("unrelated text about potatoes") == ""

    def test_list_blocks(self):
        from aria_service.intel import knowledge_gulf
        blocks = knowledge_gulf.list_blocks()
        assert "gulf_landscape" in blocks
        assert "gulf_arkmurus_angle" in blocks


class TestKnowledgeTurkeyStandalone:
    def test_context_matches_turkey_keywords(self):
        from aria_service.intel import knowledge_turkey_standalone as k
        ctx = k.get_turkey_context("what's Baykar's export footprint via SSB?")
        assert "SSB" in ctx or "Baykar" in ctx

    def test_context_does_not_collapse_into_nato(self):
        from aria_service.intel import knowledge_turkey_standalone as k
        # The whole point is Turkey must NOT be framed as just-NATO.
        ctx = k.get_turkey_context("Turkey defence industry overview")
        assert "NATO-aligned in practice" not in ctx or "independently of NATO" in ctx

    def test_list_blocks(self):
        from aria_service.intel import knowledge_turkey_standalone as k
        assert k.list_blocks() == ["turkey_standalone_landscape"]


class TestKnowledgeWestAfrica:
    def test_context_on_nigeria(self):
        from aria_service.intel import knowledge_west_africa as k
        ctx = k.get_west_africa_context("Nigeria defence procurement")
        assert "Nigeria" in ctx

    def test_context_flags_aes_alliance(self):
        from aria_service.intel import knowledge_west_africa as k
        ctx = k.get_west_africa_context("what's the Sahel AES Alliance posture?")
        assert "AES" in ctx or "Mali" in ctx

    def test_context_empty_on_unrelated(self):
        from aria_service.intel import knowledge_west_africa as k
        assert k.get_west_africa_context("nothing related") == ""


class TestKnowledgeLatamNonLusophone:
    def test_context_on_peru(self):
        from aria_service.intel import knowledge_latam_non_lusophone as k
        ctx = k.get_latam_context("Peru SEACE procurement")
        assert "Peru" in ctx or "SEACE" in ctx

    def test_context_on_colombia(self):
        from aria_service.intel import knowledge_latam_non_lusophone as k
        ctx = k.get_latam_context("COTECMAR ship programme")
        assert "COTECMAR" in ctx or "Colombia" in ctx


# ═══════════════════════════════════════════════════════════════════════════
# equipment_specs
# ═══════════════════════════════════════════════════════════════════════════

class TestEquipmentSpecs:
    def test_catalogue_size(self):
        from aria_service.intel import equipment_specs as eq
        names = eq.list_platforms()
        assert len(names) >= 25
        # No duplicates
        assert len(set(names)) == len(names)

    def test_get_platform_case_insensitive(self):
        from aria_service.intel import equipment_specs as eq
        p = eq.get_platform("bayraktar tb2")
        assert p is not None
        assert p["manufacturer"] == "Baykar"
        assert p["country_hq"] == "TR"

    def test_get_platform_unknown(self):
        from aria_service.intel import equipment_specs as eq
        assert eq.get_platform("Atlantis Super Tank 5000") is None

    def test_find_platforms_by_manufacturer(self):
        from aria_service.intel import equipment_specs as eq
        hits = eq.find_platforms("lockheed martin")
        names = [p["name"] for p in hits]
        assert any("F-16" in n or "F-35" in n for n in names)

    def test_platforms_for_operator(self):
        from aria_service.intel import equipment_specs as eq
        # US is in many platform operator lists
        hits = eq.platforms_for_operator("US")
        assert len(hits) >= 3

    def test_equipment_context_emits_marker(self):
        from aria_service.intel import equipment_specs as eq
        ctx = eq.get_equipment_context("tell me about the Gripen fighter")
        assert "[EQUIPMENT:" in ctx
        assert "Gripen" in ctx

    def test_equipment_context_empty_on_unrelated(self):
        from aria_service.intel import equipment_specs as eq
        assert eq.get_equipment_context("completely unrelated query") == ""

    def test_stats_shape(self):
        from aria_service.intel import equipment_specs as eq
        s = eq.stats()
        assert s["platforms_total"] >= 25
        assert "by_class" in s
        assert "by_country" in s


# ═══════════════════════════════════════════════════════════════════════════
# ground_truth_loop — async wrapper
# ═══════════════════════════════════════════════════════════════════════════

class TestGroundTruthAsync:
    def test_record_assessment_async_persists(self):
        from aria_service.intel import ground_truth_loop as gt
        aid = _run(gt.record_assessment_async(
            assessment_type="TENDER_OPPORTUNITY",
            subject="Angola test subject",
            aria_prediction="prediction text",
            aria_confidence=0.66,
            domain="AO",
        ))
        assert aid
        # Persisted under the canonical key prefix
        keys = [k for k in rs._mem_store if aid in k]
        assert keys

    def test_confidence_clamped_to_unit_interval(self):
        from aria_service.intel import ground_truth_loop as gt
        aid = _run(gt.record_assessment_async(
            assessment_type="INTELLIGENCE_CLAIM",
            subject="x", aria_prediction="y",
            aria_confidence=1.5,  # out of range
        ))
        import json
        stored = json.loads(rs._mem_store[f"{gt._ASYNC_KEY_PREFIX}:{aid}"])
        assert stored["aria_confidence"] == 1.0

    def test_unknown_assessment_type_defaults(self):
        from aria_service.intel import ground_truth_loop as gt
        aid = _run(gt.record_assessment_async(
            assessment_type="GARBAGE_TYPE",
            subject="x", aria_prediction="y", aria_confidence=0.5,
        ))
        import json
        stored = json.loads(rs._mem_store[f"{gt._ASYNC_KEY_PREFIX}:{aid}"])
        assert stored["assessment_type"] == "INTELLIGENCE_CLAIM"


# ═══════════════════════════════════════════════════════════════════════════
# Registry adapters — ZA + IL
# ═══════════════════════════════════════════════════════════════════════════

class TestRegistryZAIL:
    def test_za_and_il_are_supported(self):
        from aria_service.intel import registry_adapters as ra
        assert "ZA" in ra._SUPPORTED_JURISDICTIONS
        assert "IL" in ra._SUPPORTED_JURISDICTIONS

    def test_za_lookup_returns_stub_with_gaps(self, monkeypatch):
        from aria_service.intel import registry_adapters as ra

        class _FailClient:
            def __init__(self, *a, **kw): pass
            async def __aenter__(self): return self
            async def __aexit__(self, *a): return False
            async def get(self, *a, **kw):
                raise RuntimeError("offline")

        monkeypatch.setattr("httpx.AsyncClient", _FailClient)
        result = _run(ra.lookup_entity("Acme ZA Ltd", "ZA"))
        assert result is not None
        assert result["adapter"] == "south_africa_cipc_stub"
        assert result["data_gaps"]

    def test_il_lookup_returns_stub_with_gaps(self, monkeypatch):
        from aria_service.intel import registry_adapters as ra

        class _FailClient:
            def __init__(self, *a, **kw): pass
            async def __aenter__(self): return self
            async def __aexit__(self, *a): return False
            async def get(self, *a, **kw):
                raise RuntimeError("offline")

        monkeypatch.setattr("httpx.AsyncClient", _FailClient)
        result = _run(ra.lookup_entity("Rafael Advanced Defense", "IL"))
        assert result is not None
        assert result["adapter"] == "israel_registrar_stub"
        assert result["data_gaps"]
