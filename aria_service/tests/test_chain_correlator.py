"""Tests for country_taxonomy + chain_correlator (Priority 1, 2026-04-17).

These tests exercise the long-horizon causal chain: geopolitics → procurement
→ relationships. They rely on redis_store.py's in-memory fallback (activated
when _client is None) so no real Redis is required.

Uses asyncio.run() directly so the suite works without pytest-asyncio.

Run:
    python -m pytest aria_service/tests/test_chain_correlator.py -v
"""
from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta, timezone

import pytest

from aria_service.intel import country_taxonomy as ct
from aria_service.intel import chain_correlator as cc
from aria_service.intel import redis_store as rs


# ── In-memory fallback setup ──────────────────────────────────────────────

@pytest.fixture(autouse=True)
def _force_memory_backend():
    """Force redis_store into its in-memory fallback and wipe it between tests.

    The module keeps _client == None when no connect() has run, so we only
    need to clear _mem_store. We also clear intel_ledger's module-level
    cache so state doesn't leak between tests.
    """
    from aria_service.intel import intel_ledger
    rs._client = None
    rs._mem_store.clear()
    intel_ledger._cache = None
    yield
    rs._mem_store.clear()
    intel_ledger._cache = None


def _run(coro):
    """Execute a coroutine synchronously (pytest-asyncio not available)."""
    return asyncio.run(coro)


async def _seed_ledger(signals: list[dict]) -> None:
    """Write a synthetic ledger snapshot into the in-memory store."""
    from aria_service.intel import intel_ledger
    intel_ledger._cache = {"signals": signals, "version": 1}
    await intel_ledger._save()


async def _seed_shift(iso2: str, shift_type: str, days_ago: int, severity: float = 0.7) -> dict:
    """Write a stored GeoShift directly, bypassing scan.

    Tests that exercise projection / close-chains need shifts older than
    the 90-day ledger lookback, which wouldn't survive a real scan. Stored
    shifts have their own 365-day retention so this is the documented path.
    """
    ts = _iso(days_ago)
    sid = cc._hash_id(shift_type, iso2, ts[:10], "seeded")
    shift = {
        "id": sid,
        "iso2": iso2,
        "region": ct.iso2_to_region(iso2),
        "shift_type": shift_type,
        "severity": severity,
        "summary": f"seeded {shift_type} for tests",
        "source": "test",
        "source_tier_score": 0.6,
        "ts": ts,
        "detected_at": _iso(0),
    }
    stored = await cc._load(cc.SHIFTS_KEY)
    stored["items"].append(shift)
    await cc._save(cc.SHIFTS_KEY, stored)
    return shift


def _iso(days_ago: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days_ago)).isoformat()


def _sig(text: str, countries: list[str], days_ago: int, tier: float = 0.6) -> dict:
    return {
        "text": text,
        "source": "test",
        "type": "osint",
        "url": "",
        "countries": countries,
        "products": [],
        "oems": [],
        "severity": "medium",
        "ts": _iso(days_ago),
        "tags": [],
        "source_tier": "TIER_1A",
        "source_tier_score": tier,
    }


# ═══════════════════════════════════════════════════════════════════════════
# country_taxonomy
# ═══════════════════════════════════════════════════════════════════════════

class TestTaxonomy:
    @pytest.mark.parametrize("value,expected", [
        ("AO", "AO"),
        ("ao", "AO"),
        ("AGO", "AO"),
        ("ago", "AO"),
        ("Angola", "AO"),
        ("angola", "AO"),
        ("PL", "PL"),
        ("POL", "PL"),
        ("Poland", "PL"),
        ("TR", "TR"),
        ("TUR", "TR"),
        ("Turkey", "TR"),
        ("türkiye", "TR"),
        ("turkiye", "TR"),
        ("GB", "GB"),
        ("GBR", "GB"),
        ("United Kingdom", "GB"),
        ("UK", "GB"),
        ("usa", "US"),
        ("United States", "US"),
        ("DRC", "CD"),
        ("BR", "BR"),
        ("Brazil", "BR"),
        ("Thailand", "TH"),
        ("THA", "TH"),
        ("Saudi Arabia", "SA"),
        ("UAE", "AE"),
        ("myanmar", "MM"),
        ("burma", "MM"),
        ("eswatini", "SZ"),
        ("north macedonia", "MK"),
    ])
    def test_to_iso2_known(self, value, expected):
        assert ct.to_iso2(value) == expected

    @pytest.mark.parametrize("value", ["", None, "   ", "Atlantis", "ZZZ9"])
    def test_to_iso2_unknown_returns_none(self, value):
        assert ct.to_iso2(value) is None

    def test_region_members_ecowas_core(self):
        members = ct.region_members("ECOWAS")
        # Spot-check the core members — don't over-spec exact set so the
        # test survives future accession/suspension edits.
        for code in ("NG", "GH", "SN", "CI", "ML"):
            assert code in members, f"{code} missing from ECOWAS"

    def test_region_members_nato_has_new_accession(self):
        members = ct.region_members("NATO")
        assert "FI" in members
        assert "SE" in members  # Sweden accession 2024

    def test_region_members_case_insensitive(self):
        assert ct.region_members("nato") == ct.region_members("NATO")

    def test_region_members_unknown_returns_empty(self):
        assert ct.region_members("ATLANTIS") == set()

    def test_iso2_to_region_priority(self):
        # Priority order is defence-relevant first: NATO > EU, SADC > AU
        assert ct.iso2_to_region("PL") == "NATO"
        assert ct.iso2_to_region("AO") == "SADC"
        assert ct.iso2_to_region("SA") == "GCC"
        assert ct.iso2_to_region("NG") == "ECOWAS"
        assert ct.iso2_to_region("BR") == "MERCOSUR"
        assert ct.iso2_to_region("ID") == "ASEAN"

    def test_iso2_to_region_unknown(self):
        assert ct.iso2_to_region("XX") is None

    def test_all_known_iso2_covers_target_markets(self):
        """Every country in tender_monitor.TARGET_MARKETS should be reachable."""
        from aria_service.intel import tender_monitor
        known = ct.all_known_iso2()
        missing = [iso2 for iso2 in tender_monitor.TARGET_MARKETS if iso2 not in known]
        assert not missing, f"tender_monitor targets missing from taxonomy: {missing}"


# ═══════════════════════════════════════════════════════════════════════════
# chain_correlator — classifier
# ═══════════════════════════════════════════════════════════════════════════

class TestShiftClassifier:
    @pytest.mark.parametrize("text,expected", [
        ("Military coup overthrows government in country X", "regime_change"),
        ("Full-scale invasion launched at dawn", "conflict_escalation"),
        ("OFAC sanctions imposed on state arms trader", "sanctions_change"),
        ("Parliament approves defence budget increase of 15%", "budget_announcement"),
        ("Country signs NATO accession protocol", "alliance_shift"),
        ("New minister of defence inaugurated yesterday", "major_election"),
    ])
    def test_classify_each_shift_type(self, text, expected):
        assert cc._classify(text) == expected

    def test_classify_noise_returns_none(self):
        assert cc._classify("Random unrelated news item about football") is None

    def test_severity_bumps_high_impact(self):
        low = cc._severity("budget approved last week", 0.3)
        high = cc._severity("full-scale war declared, invasion under way", 0.9)
        assert high > low
        assert 0.0 <= low <= 1.0
        assert 0.0 <= high <= 1.0


# ═══════════════════════════════════════════════════════════════════════════
# chain_correlator — scan / project / close end-to-end
# ═══════════════════════════════════════════════════════════════════════════

class TestChainEndToEnd:
    def test_scan_emits_shift_for_regime_change(self):
        async def scenario():
            await _seed_ledger([
                _sig("Military coup in Angola — transition government announced",
                     ["Angola"], days_ago=20),
            ])
            return await cc.scan_geopolitical_shifts()

        shifts = _run(scenario())
        assert len(shifts) == 1
        s = shifts[0]
        assert s["iso2"] == "AO"
        assert s["shift_type"] == "regime_change"
        assert s["region"] == "SADC"
        assert s["severity"] >= cc.MIN_SEVERITY

    def test_scan_is_idempotent(self):
        async def scenario():
            await _seed_ledger([
                _sig("New defence minister inaugurated in Poland",
                     ["Poland"], days_ago=10),
            ])
            first = await cc.scan_geopolitical_shifts()
            second = await cc.scan_geopolitical_shifts()
            return first, second

        first, second = _run(scenario())
        assert len(first) == 1
        assert len(second) == 0, "dedup should suppress re-emission"

    def test_scan_multi_region_parity(self):
        # Priority 1 must be applied across regions with parity per
        # aria_global_positioning.md — no single-region bias.
        async def scenario():
            await _seed_ledger([
                _sig("Military coup in Angola", ["Angola"], days_ago=30),
                _sig("Poland signs NATO accession defence pact",
                     ["Poland"], days_ago=25),
                _sig("Thailand parliament approves defence budget increase",
                     ["Thailand"], days_ago=20),
                _sig("OFAC sanctions imposed on entities in Brazil",
                     ["Brazil"], days_ago=15),
            ])
            return await cc.scan_geopolitical_shifts()

        shifts = _run(scenario())
        iso2s = {s["iso2"] for s in shifts}
        regions = {s["region"] for s in shifts}
        assert iso2s == {"AO", "PL", "TH", "BR"}
        assert len(regions) >= 4

    def test_project_windows_emits_active_after_12mo(self):
        async def scenario():
            await _seed_shift("AO", "regime_change", days_ago=14 * 30)
            return await cc.project_windows()

        windows = _run(scenario())
        assert len(windows) == 1
        w = windows[0]
        assert w["iso2"] == "AO"
        assert w["status"] == "ACTIVE"
        assert w["confidence"] > 0

    def test_project_windows_projected_before_open(self):
        async def scenario():
            await _seed_shift("PL", "major_election", days_ago=3 * 30)
            return await cc.project_windows()

        windows = _run(scenario())
        assert len(windows) == 1
        assert windows[0]["status"] == "PROJECTED"

    def test_project_windows_dedupes_predictor_and_ground_truth(self, monkeypatch):
        """R-F343: re-projecting the same windows must NOT re-spam
        predictor.forecast or ground_truth_loop.record_assessment_async.

        Before R-F343, every 5-min tick wrote ~80 brain_hook absorbs +
        ~80 ground-truth records for the same windows = ~23k redundant
        writes/day. This test pins the dedupe behaviour at the
        chain_correlator boundary.
        """
        from aria_service.intel import predictor as _predictor
        from aria_service.intel import ground_truth_loop as _gt

        predictor_calls: list[dict] = []
        gt_calls: list[dict] = []

        async def fake_forecast(**kwargs):
            predictor_calls.append(kwargs)
            return {"overall_confidence": 0.7, "recommendations": []}

        async def fake_record(**kwargs):
            gt_calls.append(kwargs)
            return "fake_assessment_id"

        monkeypatch.setattr(_predictor, "forecast", fake_forecast)
        monkeypatch.setattr(_gt, "record_assessment_async", fake_record)

        async def scenario():
            # Two distinct shifts → two distinct windows
            await _seed_shift("AO", "regime_change", days_ago=14 * 30)
            await _seed_shift("PL", "major_election", days_ago=3 * 30)

            # First projection: both windows are new — should register
            windows1 = await cc.project_windows()
            after_first_predictor = len(predictor_calls)
            after_first_gt = len(gt_calls)

            # Second projection (simulates the next 5-min tick): same
            # windows, must be skipped
            windows2 = await cc.project_windows()
            after_second_predictor = len(predictor_calls)
            after_second_gt = len(gt_calls)

            # Third projection (another tick) — still no new writes
            await cc.project_windows()
            after_third_predictor = len(predictor_calls)
            after_third_gt = len(gt_calls)

            registered = await cc._load(cc.FORECAST_REGISTERED_KEY)
            return (
                windows1, windows2,
                after_first_predictor, after_first_gt,
                after_second_predictor, after_second_gt,
                after_third_predictor, after_third_gt,
                registered,
            )

        (
            w1, w2,
            p1, g1, p2, g2, p3, g3,
            registered,
        ) = _run(scenario())

        # First tick registered both windows
        assert len(w1) == 2
        assert p1 == 2, f"first tick must call predictor.forecast per window; got {p1}"
        assert g1 == 2, f"first tick must call ground_truth.record per window; got {g1}"

        # Second and third ticks must not re-register
        assert p2 == p1, f"second tick must skip predictor (still {p1}, got {p2})"
        assert g2 == g1, f"second tick must skip ground_truth (still {g1}, got {g2})"
        assert p3 == p1, f"third tick must still be skipping predictor (got {p3})"
        assert g3 == g1, f"third tick must still be skipping ground_truth (got {g3})"

        # The registered set is persisted with both window IDs
        assert len(registered["items"]) == 2
        assert all(isinstance(x, str) for x in registered["items"])

    def test_project_windows_registers_new_shifts_after_dedupe(self, monkeypatch):
        """R-F343: dedupe must NOT block new shifts that appear later."""
        from aria_service.intel import predictor as _predictor
        from aria_service.intel import ground_truth_loop as _gt

        predictor_calls: list[str] = []
        gt_calls: list[str] = []

        async def fake_forecast(**kwargs):
            predictor_calls.append(kwargs["context"]["window_id"])
            return {"overall_confidence": 0.7, "recommendations": []}

        async def fake_record(**kwargs):
            gt_calls.append(kwargs["subject"])
            return "id"

        monkeypatch.setattr(_predictor, "forecast", fake_forecast)
        monkeypatch.setattr(_gt, "record_assessment_async", fake_record)

        async def scenario():
            # First shift registered on tick 1
            await _seed_shift("AO", "regime_change", days_ago=14 * 30)
            await cc.project_windows()
            after_first = len(predictor_calls)

            # Tick 2 — same single shift, should NOT re-register
            await cc.project_windows()
            after_second = len(predictor_calls)

            # Tick 3 — new shift seeded → should ONLY register the new one
            await _seed_shift("PL", "major_election", days_ago=3 * 30)
            await cc.project_windows()
            after_third = len(predictor_calls)

            return after_first, after_second, after_third

        a, b, c = _run(scenario())
        assert a == 1, f"tick 1 should register 1 window, got {a}"
        assert b == 1, f"tick 2 should skip (still {a}), got {b}"
        assert c == 2, f"tick 3 should register the new shift only, got {c}"

    def test_close_chains_confirms_within_window(self):
        async def scenario():
            await _seed_shift("AO", "regime_change", days_ago=15 * 30)
            await cc.project_windows()
            await rs.lpush(
                "crucix:aria:tender_monitor:alerts",
                json.dumps({
                    "country_iso2": "AO",
                    "title": "Angolan MoD radar procurement",
                    "portal": "UNGM",
                    "publication_date": _iso(1),
                    "detected_at": _iso(1),
                }),
            )
            return await cc.close_chains()

        chains = _run(scenario())
        assert len(chains) == 1
        c = chains[0]
        assert c["iso2"] == "AO"
        assert c["within_window"] is True
        assert c["lag_days"] >= 12 * 30

    def test_close_chains_no_match_without_preceding_shift(self):
        async def scenario():
            await rs.lpush(
                "crucix:aria:tender_monitor:alerts",
                json.dumps({
                    "country_iso2": "TH",
                    "title": "Thai navy frigate tender",
                    "portal": "TED",
                    "publication_date": _iso(1),
                    "detected_at": _iso(1),
                }),
            )
            return await cc.close_chains()

        chains = _run(scenario())
        assert chains == []

    def test_activation_nudges_within_lead_window(self):
        async def scenario():
            await _seed_shift("PL", "alliance_shift", days_ago=9 * 30)
            await cc.project_windows()
            return await cc.relationship_activation_nudges()

        nudges = _run(scenario())
        iso2s = [n["iso2"] for n in nudges]
        assert "PL" in iso2s

    def test_chain_briefing_renders_active_windows(self):
        async def scenario():
            await _seed_shift("AO", "regime_change", days_ago=14 * 30)
            await cc.project_windows()
            return await cc.generate_chain_briefing()

        text = _run(scenario())
        assert "CAUSAL CHAIN" in text
        assert "AO" in text

    def test_get_chain_context_country_match(self):
        async def scenario():
            await _seed_shift("AO", "regime_change", days_ago=14 * 30)
            await cc.project_windows()
            by_name = await cc.get_chain_context("what's happening in angola procurement?")
            by_iso2 = await cc.get_chain_context("situation in AO")
            return by_name, by_iso2

        by_name, by_iso2 = _run(scenario())
        assert "[CHAIN:" in by_name
        assert "AO" in by_name
        assert "[CHAIN:" in by_iso2

    def test_stats_snapshot_shape(self):
        async def scenario():
            await _seed_shift("AO", "regime_change", days_ago=14 * 30)
            await _seed_shift("PL", "major_election", days_ago=2 * 30)
            await cc.project_windows()
            return await cc.stats()

        snap = _run(scenario())
        assert snap["shifts_total"] == 2
        assert snap["windows_total"] == 2
        assert snap["windows_active"] >= 1
        assert "regime_change" in snap["shifts_by_type"]
