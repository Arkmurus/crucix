"""Tests for competitor_tracker (Priority 4, 2026-04-17)."""
from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta, timezone

import pytest

from aria_service.intel import competitor_tracker as ct_mod
from aria_service.intel import redis_store as rs


@pytest.fixture(autouse=True)
def _force_memory_backend():
    from aria_service.intel import intel_ledger
    rs._client = None
    rs._mem_store.clear()
    intel_ledger._cache = None
    yield
    rs._mem_store.clear()
    intel_ledger._cache = None


def _run(coro):
    return asyncio.run(coro)


def _iso(days_ago: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days_ago)).isoformat()


class TestRegistry:
    def test_seed_loads(self):
        reg = _run(ct_mod._load_registry())
        assert len(reg["firms"]) >= 25
        assert "Baykar" in reg["firms"]
        assert "Leonardo" in reg["firms"]

    def test_normalise_firm_dedupes_aliases(self):
        normed = ct_mod._normalise_firm({
            "firm": "Baykar",
            "aliases": ["baykar", "TB2", "tb2"],
        })
        assert "baykar" in normed["aliases"]
        assert len([a for a in normed["aliases"] if a == "tb2"]) == 1


class TestRecordActivity:
    def test_record_dedupes_same_signal(self):
        aid1 = _run(ct_mod.record_activity({
            "firm": "Baykar",
            "country_iso2": "AO",
            "activity_type": "tender_win",
            "title": "Angola UAV award",
            "ts": _iso(5),
        }))
        aid2 = _run(ct_mod.record_activity({
            "firm": "Baykar",
            "country_iso2": "AO",
            "activity_type": "tender_win",
            "title": "Angola UAV award",
            "ts": _iso(5),
        }))
        assert aid1 == aid2
        data = _run(ct_mod._load_activities())
        assert len(data["items"]) == 1

    def test_record_resolves_country_name(self):
        _run(ct_mod.record_activity({
            "firm": "Rheinmetall",
            "country": "Poland",
            "activity_type": "tender_bid",
            "title": "Leopard upgrade bid",
        }))
        data = _run(ct_mod._load_activities())
        assert data["items"][0]["country_iso2"] == "PL"
        assert data["items"][0]["region"] == "NATO"


class TestScans:
    def test_scan_tender_alerts_detects_competitor(self):
        _run(rs.lpush(
            "crucix:aria:tender_monitor:alerts",
            json.dumps({
                "country_iso2": "AO",
                "title": "Angola Air Force UAV contract awarded to Baykar",
                "description": "Baykar TB2 drones delivered to Angolan Air Force",
                "buyer": "Angolan MoD",
                "portal": "UNGM",
                "publication_date": _iso(5),
                "detected_at": _iso(5),
            }),
        ))
        result = _run(ct_mod.scan_sources())
        assert result["tender_hits"] >= 1
        hits = _run(ct_mod.who_else_in("Angola"))
        firms = [h["firm"] for h in hits]
        assert "Baykar" in firms

    def test_scan_ignores_unrelated_tenders(self):
        _run(rs.lpush(
            "crucix:aria:tender_monitor:alerts",
            json.dumps({
                "country_iso2": "AO",
                "title": "Generic stationery supply tender",
                "description": "Office supplies only",
                "portal": "TED",
                "publication_date": _iso(5),
                "detected_at": _iso(5),
            }),
        ))
        result = _run(ct_mod.scan_sources())
        assert result["tender_hits"] == 0


class TestWhoElseIn:
    def test_aggregates_by_firm(self):
        for i in range(3):
            _run(ct_mod.record_activity({
                "firm": "Baykar",
                "country_iso2": "AO",
                "activity_type": "tender_win" if i == 0 else "press_announcement",
                "title": f"Angola signal {i}",
                "ts": _iso(5 + i),
            }))
        _run(ct_mod.record_activity({
            "firm": "Norinco",
            "country_iso2": "AO",
            "activity_type": "mou_signed",
            "title": "Norinco MoU with Angola",
            "ts": _iso(10),
        }))
        hits = _run(ct_mod.who_else_in("AO"))
        firms = {h["firm"]: h["activity_count"] for h in hits}
        assert firms["Baykar"] == 3
        assert firms["Norinco"] == 1

    def test_unknown_country_returns_empty(self):
        assert _run(ct_mod.who_else_in("Atlantis")) == []


class TestChatContext:
    def test_context_returns_empty_without_country(self):
        assert _run(ct_mod.get_competitor_context("generic question")) == ""

    def test_context_emits_marker(self):
        _run(ct_mod.record_activity({
            "firm": "Baykar",
            "country_iso2": "AO",
            "activity_type": "tender_win",
            "title": "Angola UAV award",
            "ts": _iso(5),
        }))
        ctx = _run(ct_mod.get_competitor_context("who else is active in angola?"))
        assert "[COMPETITORS:" in ctx
        assert "Baykar" in ctx


class TestBriefingAndStats:
    def test_briefing_surfaces_activity(self):
        _run(ct_mod.record_activity({
            "firm": "Baykar",
            "country_iso2": "AO",
            "activity_type": "tender_win",
            "title": "Angola UAV",
            "ts": _iso(5),
        }))
        text = _run(ct_mod.generate_competitor_briefing())
        assert "COMPETITOR ACTIVITY" in text
        assert "AO" in text

    def test_stats_shape(self):
        _run(ct_mod._load_registry())
        snap = _run(ct_mod.stats())
        assert snap["firms_tracked"] >= 25
        assert snap["activities_total"] == 0
