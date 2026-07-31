"""R-F2557 — Golden Intel bridge Node→brain ingest inbox + source honesty policy.

Proves: (1) Node-pushed findings queue + drain + promote; (2) the SERVER-SIDE
honesty policy caps OpenSanctions to Mining-Queue (MEDIUM/tier_2) so a heuristic
sanctions feed can NEVER reach public "Distribution Ready", even if Node pushes
HIGH/tier_1a; (3) BD Intelligence findings promote at full value.
"""
from __future__ import annotations

import asyncio

from aria_service.intel import golden_intel_bridge as bridge


def _os_finding(priority="HIGH", tier="tier_1a"):
    return {
        "source_key": "opensanctions", "source": "OpenSanctions", "signal_type": "sanctions_change",
        "priority": priority, "confidence": "HIGH", "score": 80, "source_tier": tier,
        "title": "ACME Corp added to 3 lists", "why_it_matters": "multi-list within 48h",
        "recommended_action": "Monitor for official designation; screen counterparties.",
        "target": "ACME Corp", "evidence_url": "https://ofac.treasury.gov/x",
        "entities": {"countries": ["RU"], "products": [], "oems": []},
        "ref": "acme-2026", "detected_at": "2026-07-12T00:00:00+00:00", "evidence_count": 1,
    }


def _bd_finding():
    return {
        "source_key": "bd_intelligence", "source": "BD Opportunity: Angola",
        "signal_type": "programme_signal", "priority": "HIGH", "confidence": "HIGH",
        "score": 80, "source_tier": "tier_2", "title": "Angola: procurement window open",
        "why_it_matters": "active conflict events; OEM match on radios",
        "recommended_action": "Assess market entry — review procurement needs.",
        "target": "Angola", "evidence_url": "https://contractsfinder.example/1",
        "entities": {"countries": ["Angola"], "products": ["radios"], "oems": ["radios"]},
        "ref": "AO-1", "detected_at": "2026-07-12T00:00:00+00:00", "evidence_count": 1,
    }


def test_policy_caps_opensanctions_to_mining_queue():
    f = bridge._apply_source_policy(_os_finding("HIGH", "tier_1a"))
    assert f["priority"] == "MEDIUM"       # capped from HIGH
    assert f["source_tier"] == "tier_2"    # capped from tier_1a
    sig = bridge._normalize_finding_to_signal(f)
    assert bridge._is_distribution_ready(sig) is False   # can NEVER be distribution-ready


def test_policy_leaves_bd_untouched():
    f = bridge._apply_source_policy(_bd_finding())
    assert f["priority"] == "HIGH"
    assert f["source_tier"] == "tier_2"


def _inbox_stubs(monkeypatch):
    inbox: list = []
    ledger: dict = {}

    async def lpush(k, v, **kw):
        inbox.insert(0, v)

    async def lpop_multi(k, n=10):
        out = inbox[:n]
        del inbox[:n]
        return out

    async def ltrim(k, a, b):
        if a == 0:
            inbox[:] = inbox[:b + 1]

    async def get_json(k):
        return ledger.get(k)

    async def set_json(k, o, *a, **kw):
        ledger[k] = o

    monkeypatch.setattr(bridge.rs, "lpush", lpush)
    monkeypatch.setattr(bridge.rs, "lpop_multi", lpop_multi)
    monkeypatch.setattr(bridge.rs, "ltrim", ltrim)
    monkeypatch.setattr(bridge.rs, "get_json", get_json)
    monkeypatch.setattr(bridge.rs, "set_json", set_json)
    return inbox, ledger


def test_push_to_inbox_validates(monkeypatch):
    inbox, _ = _inbox_stubs(monkeypatch)
    good = _bd_finding()
    bad_no_title = {"source_key": "x", "signal_type": "active_tender"}
    bad_no_type = {"source_key": "x", "title": "y"}
    n = asyncio.run(bridge.push_to_inbox("bd_intelligence", [good, bad_no_title, bad_no_type]))
    assert n == 1
    assert len(inbox) == 1


def test_inbox_adapter_drains_and_promotes(monkeypatch):
    inbox, ledger = _inbox_stubs(monkeypatch)
    stored: list = []

    async def fake_store(s):
        stored.append(s)

    monkeypatch.setattr(bridge._nm, "_store_intel_signal", fake_store)
    asyncio.run(bridge.push_to_inbox("bd_intelligence", [_bd_finding()]))
    asyncio.run(bridge.push_to_inbox("opensanctions", [_os_finding("HIGH", "tier_1a")]))
    assert len(inbox) == 2

    findings = asyncio.run(bridge._inbox_adapter())
    assert len(findings) == 2
    assert len(inbox) == 0                       # atomically drained
    osf = [f for f in findings if f["source_key"] == "opensanctions"][0]
    assert osf["priority"] == "MEDIUM" and osf["source_tier"] == "tier_2"   # policy applied

    res = asyncio.run(bridge.promote_findings(findings, source_name="ingested"))
    assert res["promoted"] == 2
    bd_sig = [s for s in stored if s.get("promotion_source") == "bd_intelligence"][0]
    os_sig = [s for s in stored if s.get("promotion_source") == "opensanctions"][0]
    assert bridge._is_distribution_ready(bd_sig) is True     # BD = full value
    assert bridge._is_distribution_ready(os_sig) is False    # OpenSanctions = Mining Queue


def test_opensanctions_never_distribution_ready_end_to_end(monkeypatch):
    _inbox_stubs(monkeypatch)
    stored: list = []

    async def fake_store(s):
        stored.append(s)

    monkeypatch.setattr(bridge._nm, "_store_intel_signal", fake_store)
    monkeypatch.setattr(bridge, "wire_success", lambda *a, **k: None)
    monkeypatch.setattr(bridge, "wire_failure", lambda *a, **k: None)
    # even a maliciously-elevated HIGH/tier_1a OpenSanctions push
    asyncio.run(bridge.push_to_inbox("opensanctions", [_os_finding("HIGH", "tier_1a")]))
    monkeypatch.setattr(bridge, "_ADAPTERS", {"ingested": bridge._inbox_adapter})
    totals = asyncio.run(bridge.run_promotion_pass())
    assert totals["promoted"] == 1
    assert totals["distribution_ready"] == 0     # honesty policy held through the full path


def _opportunity_finding():
    # shape the FIXED Node mapping produces — STABLE ref (market-anchored), no raw score
    return {
        "source_key": "bd_intelligence", "source": "Opportunity: Angola",
        "signal_type": "programme_signal", "priority": "HIGH", "confidence": "HIGH",
        "source_tier": "tier_2", "title": "Angola: active procurement/market window",
        "why_it_matters": "12 active conflict events; procurement needs: radios",
        "recommended_action": "Assess market entry.", "target": "Angola",
        "entities": {"countries": ["Angola"], "products": [], "oems": []},
        "evidence_url": "https://ted.europa.eu/n/1", "url": "https://ted.europa.eu/n/1",
        "ref": "AO-angola", "detected_at": "2026-07-12T00:00:00+00:00", "evidence_count": 1,
    }


def test_opportunity_dedups_across_sweeps_no_flood(monkeypatch):
    """R-F2557 review #1/#2 — the SAME market opportunity pushed on two sweeps must
    promote once, not flood the signal list. Guards the volatile-id regression."""
    _inbox_stubs(monkeypatch)
    stored: list = []

    async def fake_store(s):
        stored.append(s)

    monkeypatch.setattr(bridge._nm, "_store_intel_signal", fake_store)

    asyncio.run(bridge.push_to_inbox("bd_intelligence", [_opportunity_finding()]))
    r1 = asyncio.run(bridge.promote_findings(asyncio.run(bridge._inbox_adapter()), source_name="ingested"))
    assert r1["promoted"] == 1

    # second sweep — identical stable ref → must dedup
    asyncio.run(bridge.push_to_inbox("bd_intelligence", [_opportunity_finding()]))
    r2 = asyncio.run(bridge.promote_findings(asyncio.run(bridge._inbox_adapter()), source_name="ingested"))
    assert r2["promoted"] == 0
    assert r2["skipped"] == 1
    assert len(stored) == 1              # NOT flooded


def test_malformed_numeric_is_coerced_not_fatal(monkeypatch):
    """Regression review #1: a bad numeric (evidence_count='n/a', score='55%') from the
    ingest boundary is defensively coerced — promotes, never aborts the pass."""
    _inbox_stubs(monkeypatch)
    stored: list = []
    async def fake_store(s):
        stored.append(s)
    monkeypatch.setattr(bridge._nm, "_store_intel_signal", fake_store)
    f = _bd_finding()
    f.update({"evidence_count": "n/a", "score": "55%"})
    r = asyncio.run(bridge.promote_findings([f], source_name="test"))
    assert r["promoted"] == 1 and len(stored) == 1


def test_normalize_exception_contained_to_one_finding(monkeypatch):
    """Regression review #2: a raise in normalization is contained to that one finding
    (invalid + gap); the pass continues and other findings still promote."""
    _inbox_stubs(monkeypatch)
    stored: list = []
    fails: list = []
    async def fake_store(s):
        stored.append(s)
    monkeypatch.setattr(bridge._nm, "_store_intel_signal", fake_store)
    monkeypatch.setattr(bridge, "wire_failure", lambda *a, **k: fails.append(1))
    _orig = bridge._normalize_finding_to_signal
    _n = {"c": 0}
    # R-F3545 — the stub must model the REAL signature. R-F3540 threads the
    # registered adapter name into the normalizer so every signal is attributable
    # to its producer; a `flaky(f)` stub then raised TypeError for BOTH findings,
    # turning a containment test into a signature test and reporting invalid==2.
    # The property under test is unchanged: ONE bad finding must not take the
    # others down with it.
    def flaky(f, **kwargs):
        _n["c"] += 1
        if _n["c"] == 1:
            raise ValueError("boom")
        return _orig(f, **kwargs)
    monkeypatch.setattr(bridge, "_normalize_finding_to_signal", flaky)
    f1 = _bd_finding(); f1["ref"] = "A"
    f2 = _bd_finding(); f2["ref"] = "B"
    r = asyncio.run(bridge.promote_findings([f1, f2], source_name="test"))
    assert r["invalid"] == 1 and r["promoted"] == 1 and fails


if __name__ == "__main__":
    import sys
    import pytest
    sys.exit(pytest.main([__file__, "-v"]))
