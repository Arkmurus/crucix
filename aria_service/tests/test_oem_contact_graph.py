"""Tests for oem_contact_graph (Priority 2, 2026-04-17).

Anti-hallucination check: seeded records MUST have empty names — ARIA
never invents OEM personnel. Operator fills from DSEI/Eurosatory.
"""
from __future__ import annotations

import asyncio

import pytest

from aria_service.intel import oem_contact_graph as og
from aria_service.intel import redis_store as rs


@pytest.fixture(autouse=True)
def _force_memory_backend():
    rs._client = None
    rs._mem_store.clear()
    yield
    rs._mem_store.clear()


def _run(coro):
    return asyncio.run(coro)


class TestSeed:
    def test_seed_has_12_oems_x_5_slots(self):
        data = _run(og._load())
        # 12 OEMs × 5 anchor roles each = 60
        assert len(data["items"]) == 60

    def test_all_seed_records_are_placeholders(self):
        data = _run(og._load())
        # Anti-hallucination invariant — seed must not carry any names.
        for c in data["items"]:
            assert c["name"] == "", f"Hallucinated name: {c}"
            assert c["confidence"] == "LOW"
            assert c["source"] == "placeholder"

    def test_list_oems_returns_twelve(self):
        assert len(og.list_oems()) == 12


class TestMatching:
    def test_match_angola_covers_africa_and_global(self):
        # Angola → SADC → AFRICA bucket + GLOBAL bucket
        matches = _run(og.match_contact_to_opportunity("AO"))
        assert matches
        regions = {m["region_focus"] for m in matches}
        assert "AFRICA" in regions
        assert "GLOBAL" in regions
        # MENA-only records should NOT appear for Angola
        assert "MENA" not in regions

    def test_match_saudi_includes_mena(self):
        matches = _run(og.match_contact_to_opportunity("SA"))
        regions = {m["region_focus"] for m in matches}
        assert "MENA" in regions
        assert "GLOBAL" in regions
        assert "AFRICA" not in regions

    def test_match_filters_by_seniority(self):
        matches = _run(og.match_contact_to_opportunity("AO", seniority="C_SUITE"))
        assert matches
        assert all(m["seniority"] == "C_SUITE" for m in matches)

    def test_unknown_country_returns_empty(self):
        assert _run(og.match_contact_to_opportunity("Atlantis")) == []


class TestAddContact:
    def test_upsert_fills_slot(self):
        cid = _run(og.add_contact({
            "oem": "Leonardo",
            "role_slot": "Head of BD — Africa",
            "name": "Test Placeholder Name",
            "linkedin_url": "https://www.linkedin.com/in/test-placeholder",
            "seniority": "HEAD",
            "region_focus": "AFRICA",
            "source": "dsei_speaker_list",
            "confidence": "HIGH",
        }))
        assert cid
        filled = _run(og.get_oem_contacts(oem="Leonardo", filled_only=True))
        names = [c["name"] for c in filled]
        assert "Test Placeholder Name" in names

    def test_upsert_deduplicates(self):
        for _ in range(3):
            _run(og.add_contact({
                "oem": "Rheinmetall",
                "role_slot": "CEO",
                "name": "Jane Doe",
                "seniority": "C_SUITE",
                "region_focus": "GLOBAL",
            }))
        filled = _run(og.get_oem_contacts(oem="Rheinmetall", filled_only=True))
        matches = [c for c in filled if c["name"] == "Jane Doe" and c["role_slot"] == "CEO"]
        assert len(matches) == 1


class TestEnrichAndBriefing:
    def test_enrich_reports_placeholders(self):
        result = _run(og.enrich_from_linkedin())
        assert result["placeholders_total"] == 60
        assert len(result["placeholders_by_oem"]) == 12

    def test_briefing_surfaces_coverage_gaps(self):
        text = _run(og.generate_oem_briefing())
        assert "OEM CONTACT GRAPH" in text
        # Fresh seed: every OEM has 0/5 filled
        assert "0/5" in text or "0%" in text


class TestChatContext:
    def test_context_empty_without_country_mention(self):
        assert _run(og.get_oem_context("generic question")) == ""

    def test_context_emits_marker_for_country(self):
        ctx = _run(og.get_oem_context("who's the BD lead for angola at leonardo?"))
        assert "[OEM:" in ctx

    def test_context_prefers_filled_records(self):
        _run(og.add_contact({
            "oem": "Leonardo",
            "role_slot": "Head of BD — Africa",
            "name": "Jane Real",
            "seniority": "HEAD",
            "region_focus": "AFRICA",
            "source": "dsei_speaker_list",
            "confidence": "HIGH",
        }))
        ctx = _run(og.get_oem_context("opportunities in angola defence?"))
        # Should show the real name, not a placeholder label
        assert "Jane Real" in ctx


class TestStats:
    def test_stats_shape(self):
        snap = _run(og.stats())
        assert snap["contacts_total"] == 60
        assert snap["contacts_filled"] == 0
        assert snap["fill_rate"] == 0.0
        assert snap["oems_tracked"] == 12
        assert snap["by_confidence"]["LOW"] == 60
