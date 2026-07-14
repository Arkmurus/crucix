"""R-F2555 — Golden Intel promotion bridge capability tests.

Proves the user-visible outcome: a decision-grade public tender is promoted into
the SAME signal store news_monitor uses, in a shape that passes the live Telegram
gate (selectTelegramGoldenIntel) / dashboard split (isDistributionReadyGolden) —
raising Golden Intel volume WITHOUT lowering the gate. Also proves never-false-clean
(adapter failure → gap) and §21 wiring (success → wire_success).
"""
from __future__ import annotations

import asyncio

from aria_service.intel import golden_intel_bridge as bridge


def _decision_grade_tender_finding() -> dict:
    return {
        "source_key": "tender_monitor",
        "source": "Procurement: TED",
        "signal_type": "active_tender",
        "priority": "HIGH",
        "confidence": "HIGH",
        "score": 82,
        "source_tier": "tier_1a",
        "title": "Supply of tactical radios for national defence force",
        "why_it_matters": "MoD (Portugal) — value EUR 5M, deadline 2026-09-01. Matched: radios.",
        "recommended_action": "Assess bid/no-bid.",
        "target": "Ministry of Defence",
        "entities": {"countries": ["Portugal"], "products": ["radios"], "oems": []},
        "evidence_url": "https://ted.europa.eu/udl?uri=TED:NOTICE:123-2026",
        "url": "https://ted.europa.eu/udl?uri=TED:NOTICE:123-2026",
        "ref": "tender_abc123",
        "detected_at": "2026-07-11T10:00:00+00:00",
        "evidence_count": 1,
        "category": "procurement",
    }

# The exact keys every news_monitor signal carries (news_monitor._build_intel_signal).
_SCHEMA_KEYS = {
    "id", "signal_type", "priority", "confidence", "score", "quality_label",
    "confidence_rationale", "evidence_count", "corroboration", "action_horizon",
    "urgency", "title", "decision_summary", "why_it_matters", "recommended_action",
    "target", "source", "source_tier", "category", "language", "url", "published",
    "detected_at", "entities", "evidence",
}


def test_decision_grade_tender_is_distribution_ready():
    sig = bridge._normalize_finding_to_signal(_decision_grade_tender_finding())
    assert sig is not None
    assert sig["signal_type"] == "active_tender"
    assert sig["priority"] == "HIGH"
    assert sig["quality_label"].startswith("decision-grade")
    assert sig["source_tier"] == "tier_1a"
    # user-visible: passes the same gate the dashboard/Telegram apply
    assert bridge._is_distribution_ready(sig) is True
    assert sig["customer_value"]["score"] >= 80
    assert sig["customer_value"]["telegram_ready"] is True
    # schema fidelity — every news_monitor signal key is present
    missing = _SCHEMA_KEYS - set(sig)
    assert not missing, f"signal missing schema keys: {missing}"


def test_weak_finding_lands_in_mining_queue_not_distribution():
    f = _decision_grade_tender_finding()
    f.update({"priority": "LOW", "confidence": "LOW", "score": 30, "source_tier": "tier_2"})
    sig = bridge._normalize_finding_to_signal(f)
    assert sig is not None                       # still a valid signal (Mining Queue)
    assert bridge._is_distribution_ready(sig) is False   # honestly NOT promoted to public


def test_generic_customer_action_is_rejected_from_distribution():
    f = _decision_grade_tender_finding()
    f.update({
        "signal_type": "conflict_escalation",
        "title": "Regional security situation changes",
        "why_it_matters": "Security reporting indicates elevated operating risk but does not identify a named customer exposure or decision path.",
        "recommended_action": "Assess country risk",
        "target": "Regional market",
    })
    sig = bridge._normalize_finding_to_signal(f)
    assert sig is not None
    assert "generic_action" in sig["customer_value"]["rejection_reasons"]
    assert sig["customer_value"]["distribution_ready"] is False
    assert bridge._is_distribution_ready(sig) is False


def test_customer_value_dashboard_threshold_is_not_telegram_threshold():
    f = _decision_grade_tender_finding()
    f["customer_value"] = {
        "score": 75,
        "segments": ["procurement_team"],
        "problems": ["bid_opportunity"],
        "aria_added": ["procurement_implication"],
    }
    sig = bridge._normalize_finding_to_signal(f)
    assert sig is not None
    assert sig["customer_value"]["distribution_ready"] is True
    assert sig["customer_value"]["telegram_ready"] is False
    assert bridge._is_distribution_ready(sig) is False


def test_trade_gov_csl_finding_is_distribution_ready():
    finding = {
        "source_key": "trade_gov_csl",
        "source": "trade.gov CSL: BIS Entity List",
        "signal_type": "sanctions_change",
        "priority": "HIGH",
        "confidence": "HIGH",
        "score": 90,
        "source_tier": "tier_1a",
        "title": "ACME Defence LLC: official CSL match",
        "why_it_matters": (
            "ACME Defence LLC matched public watchlist term \"ACME\" on BIS Entity List. "
            "This is an official US export/sanctions screening source."
        ),
        "recommended_action": "Screen counterparties; pause export or bid activity until compliance review is complete.",
        "target": "ACME Defence LLC",
        "entities": {"countries": ["AE"], "products": [], "oems": []},
        "evidence_url": "https://www.bis.gov/entity-list",
        "url": "https://www.bis.gov/entity-list",
        "ref": "csl-1",
        "detected_at": "2026-07-14T10:00:00+00:00",
        "evidence_count": 1,
        "category": "export_control",
        "customer_value": {
            "score": 90,
            "segments": ["compliance_officer", "defence_exporter"],
            "problems": ["export_control_risk", "sanctions_risk"],
            "aria_added": ["compliance_implication", "watchlist_match"],
        },
    }
    sig = bridge._normalize_finding_to_signal(finding)
    assert sig is not None
    assert sig["customer_value"]["telegram_ready"] is True
    assert sig["customer_value"]["rejection_reasons"] == []
    assert bridge._is_distribution_ready(sig) is True


def test_finding_without_title_is_invalid():
    f = _decision_grade_tender_finding()
    f["title"] = ""
    f["decision_summary"] = ""
    assert bridge._normalize_finding_to_signal(f) is None


def test_promote_findings_stores_and_dedups(monkeypatch):
    stored: list[dict] = []
    ledger: dict = {}

    async def fake_store(sig):
        stored.append(sig)

    async def fake_get_json(key):
        return ledger.get(key)

    async def fake_set_json(key, obj, *a, **k):
        ledger[key] = obj

    monkeypatch.setattr(bridge._nm, "_store_intel_signal", fake_store)
    monkeypatch.setattr(bridge.rs, "get_json", fake_get_json)
    monkeypatch.setattr(bridge.rs, "set_json", fake_set_json)

    f = _decision_grade_tender_finding()
    r1 = asyncio.run(bridge.promote_findings([f], source_name="test"))
    assert r1["promoted"] == 1
    assert r1["distribution_ready"] == 1
    assert len(stored) == 1

    # same finding again → cooldown-deduped, not re-stored
    r2 = asyncio.run(bridge.promote_findings([f], source_name="test"))
    assert r2["promoted"] == 0
    assert r2["skipped"] == 1
    assert len(stored) == 1


def test_tender_adapter_maps_official_portal(monkeypatch):
    from aria_service.intel import tender_monitor

    class _FakeTender:
        def to_dict(self):
            return {
                "id": "tender_x", "portal": "TED", "title": "Radios tender",
                "buyer": "MoD", "country": "Portugal", "value_estimate": "EUR 5M",
                "deadline": "2026-09-01", "url": "https://ted.europa.eu/n/1",
                "relevance_score": 0.70, "matched_products": ["radios"],
                "detected_at": "2026-07-11T10:00:00+00:00", "publication_date": "2026-07-10",
            }

    async def fake_get_new(since_hours=24):
        return [_FakeTender()]

    monkeypatch.setattr(tender_monitor, "get_new_tenders", fake_get_new)
    findings = asyncio.run(bridge._tender_adapter())
    assert len(findings) == 1
    fnd = findings[0]
    assert fnd["signal_type"] == "active_tender"
    assert fnd["source_tier"] == "tier_1a"          # TED = official EU portal
    assert fnd["priority"] == "HIGH"                 # rel 0.70 + url + matched products
    # end-to-end: the adapter's finding normalizes to a distribution-ready signal
    sig = bridge._normalize_finding_to_signal(fnd)
    assert bridge._is_distribution_ready(sig) is True


def test_run_promotion_pass_wires_success(monkeypatch):
    successes: list = []
    monkeypatch.setattr(bridge, "wire_success", lambda *a, **k: successes.append((a, k)))
    monkeypatch.setattr(bridge, "wire_failure", lambda *a, **k: None)

    stored: list[dict] = []
    ledger: dict = {}
    async def fake_store(s):
        stored.append(s)
    monkeypatch.setattr(bridge._nm, "_store_intel_signal", fake_store)
    async def fake_get_json(k):
        return ledger.get(k)
    async def fake_set_json(k, o, *a, **kw):
        ledger[k] = o
    monkeypatch.setattr(bridge.rs, "get_json", fake_get_json)
    monkeypatch.setattr(bridge.rs, "set_json", fake_set_json)

    async def one_adapter():
        return [_decision_grade_tender_finding()]

    monkeypatch.setattr(bridge, "_ADAPTERS", {"t": one_adapter})
    totals = asyncio.run(bridge.run_promotion_pass())
    assert totals["promoted"] == 1
    assert totals["distribution_ready"] == 1
    assert successes, "wire_success was not called on a completed pass (§21a)"


def test_adapter_failure_records_gap_never_false_clean(monkeypatch):
    """A source adapter that raises must record a gap (never a silent 'all clear')."""
    failures: list = []
    monkeypatch.setattr(bridge, "wire_failure", lambda *a, **k: failures.append((a, k)))
    monkeypatch.setattr(bridge, "wire_success", lambda *a, **k: None)

    async def boom():
        raise RuntimeError("source down")

    monkeypatch.setattr(bridge, "_ADAPTERS", {"boom": boom})
    totals = asyncio.run(bridge.run_promotion_pass())
    assert totals["adapters_failed"] >= 1
    assert any("boom" in str(c) for c in failures), "wire_failure not called for failed adapter"


# ── Pass-2 review hardening (adversarial review findings #1–#5) ───────────────

class _FakeTender:
    def __init__(self, **kw):
        self._d = {
            "id": "t1", "portal": "TED", "title": "Radios tender", "buyer": "MoD",
            "country": "Portugal", "value_estimate": "EUR 5M", "deadline": "2026-09-01",
            "url": "https://ted.europa.eu/n/1", "relevance_score": 0.9,
            "matched_products": ["radios"], "detected_at": "2026-07-11T10:00:00+00:00",
            "publication_date": "2026-07-10",
        }
        self._d.update(kw)

    def to_dict(self):
        return dict(self._d)


def test_unknown_portal_defaults_to_tier3(monkeypatch):
    """#3 — an unmapped portal must NOT be treated as trusted (tier_2)."""
    from aria_service.intel import tender_monitor
    async def fake(since_hours=24):
        return [_FakeTender(portal="WEIRD_PORTAL")]
    monkeypatch.setattr(tender_monitor, "get_new_tenders", fake)
    findings = asyncio.run(bridge._tender_adapter())
    assert findings[0]["source_tier"] == "tier_3"
    assert bridge._is_distribution_ready(bridge._normalize_finding_to_signal(findings[0])) is False


def test_dead_tender_source_records_gap(monkeypatch):
    """#1 — empty result + UNREADABLE store → gap (never-false-clean), not silent."""
    from aria_service.intel import tender_monitor
    failures: list = []
    monkeypatch.setattr(bridge, "wire_failure", lambda *a, **k: failures.append((a, k)))
    async def empty(since_hours=24):
        return []
    async def broken_stats():
        raise RuntimeError("store corrupt")
    monkeypatch.setattr(tender_monitor, "get_new_tenders", empty)
    monkeypatch.setattr(tender_monitor, "get_stats", broken_stats)
    assert asyncio.run(bridge._tender_adapter()) == []
    assert any("unreadable" in str(c) for c in failures), "dead source not recorded as gap"


def test_readable_empty_source_records_no_false_gap(monkeypatch):
    """#1 — genuinely empty but READABLE source must not record a false gap."""
    from aria_service.intel import tender_monitor
    failures: list = []
    monkeypatch.setattr(bridge, "wire_failure", lambda *a, **k: failures.append((a, k)))
    async def empty(since_hours=24):
        return []
    async def ok_stats():
        return {"last_run": {}, "portal_health": {}, "total_alerts_stored": 0}
    monkeypatch.setattr(tender_monitor, "get_new_tenders", empty)
    monkeypatch.setattr(tender_monitor, "get_stats", ok_stats)
    assert asyncio.run(bridge._tender_adapter()) == []
    assert not failures, "false gap recorded for a readable empty source"


def test_tender_batch_is_capped_and_floored(monkeypatch):
    """#2 — a burst is bounded per pass; below-floor noise is dropped entirely."""
    from aria_service.intel import tender_monitor
    async def many(since_hours=24):
        high = [_FakeTender(id=f"t{i}", url=f"https://ted/{i}", relevance_score=0.9) for i in range(100)]
        low = [_FakeTender(id="tlow", url="https://ted/low", relevance_score=0.10, matched_products=[])]
        return high + low
    monkeypatch.setattr(tender_monitor, "get_new_tenders", many)
    findings = asyncio.run(bridge._tender_adapter())
    assert len(findings) <= bridge._MAX_TENDERS_PER_PASS
    assert all(f.get("ref") != "tlow" for f in findings), "below-floor tender not dropped"
    # explicit floor check: a single below-floor tender promotes nothing
    async def one_low(since_hours=24):
        return [_FakeTender(id="tlow", relevance_score=0.10, matched_products=[])]
    monkeypatch.setattr(tender_monitor, "get_new_tenders", one_low)
    assert asyncio.run(bridge._tender_adapter()) == []


def test_non_http_url_is_not_distribution_ready():
    """#5 — mirror the dashboard's strict http(s) evidence-URL requirement."""
    f = _decision_grade_tender_finding()
    f["evidence_url"] = "javascript:alert(1)"
    f["url"] = "javascript:alert(1)"
    sig = bridge._normalize_finding_to_signal(f)
    assert bridge._is_distribution_ready(sig) is False


if __name__ == "__main__":
    import sys
    import pytest
    sys.exit(pytest.main([__file__, "-v"]))
