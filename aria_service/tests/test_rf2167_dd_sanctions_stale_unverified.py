"""R-F2167 — DD primary-source sanctions adapters must NOT read a STALE
snapshot as a clearance, and a stale screen must downgrade the headline.

The direct primary-source adapter loop in dd_orchestrator was the unprotected
twin of the OpenSanctions-aggregate path already guarded by R-F1696. When an
adapter's upstream feed is down but an OLD cache is still warm, `_load_records`
served the stale records and `lookup` returned ok=True/hits=[] with NO
staleness signal — so a newly-designated entity absent from the old snapshot
read as CLEAN, and the headline verdict was never downgraded.

Fix (verified by these capability tests):
  1. each adapter stamps `stale`/`source_unavailable` when it served a snapshot
     past its cache TTL (refresh failed) — `_common.mark_stale_if_expired`.
  2. the orchestrator's identity primary-source loop surfaces that as a
     SANCTIONS_SOURCE_UNVERIFIED data_gap + an amber finding.
  3. the synthesis freshness-gate forces GREEN → AMBER-LIGHT on that marker,
     independent of the person flag and the >=3-gap threshold.
"""
from __future__ import annotations

import asyncio
import time

import pytest


# ─────────────────────── 1. adapter emits the stale signal ──────────────────

def test_rf2167_mark_stale_if_expired_helper():
    from aria_service.intel.sources import _common
    # fetched 1 hour ago, TTL 30 min → expired → flagged.
    r = {"ok": True, "hits": []}
    _common.mark_stale_if_expired(r, {"fetched_at": time.time() - 3600}, 1800)
    assert r["stale"] is True and r["source_unavailable"] is True
    # fresh within TTL → NOT flagged.
    r2 = {"ok": True, "hits": []}
    _common.mark_stale_if_expired(r2, {"fetched_at": time.time()}, 1800)
    assert "stale" not in r2 and "source_unavailable" not in r2


def test_rf2167_ofac_lookup_flags_stale_when_feed_down(monkeypatch):
    """Drive the REAL ofac_sdn.lookup: warm-but-EXPIRED cache + feed down →
    lookup must return ok=True with stale/source_unavailable set, so empty hits
    are not read as clean."""
    from aria_service.intel.sources import ofac_sdn, _common

    # Seed a warm cache that is PAST its TTL (refresh will be attempted).
    monkeypatch.setitem(ofac_sdn._CACHE, "records",
                        [{"name": "SOME OLD SANCTIONED CO", "aliases": []}])
    monkeypatch.setitem(ofac_sdn._CACHE, "fetched_at",
                        time.time() - (ofac_sdn._CACHE_TTL_S + 60))
    # Feed is DOWN → http_get_text returns None → _load_records serves stale.
    async def _down(*a, **k):
        return None
    monkeypatch.setattr(_common, "http_get_text", _down)

    r = asyncio.run(ofac_sdn.lookup("Globex International Trading Ltd"))
    assert r["ok"] is True, "stale serve is still a 'successful' call shape"
    assert r["hits"] == [], "the old snapshot has no match for this name"
    assert r.get("stale") is True, "a stale serve must be flagged stale"
    assert r.get("source_unavailable") is True, "stale serve must flag source_unavailable"


def test_rf2167_ofac_lookup_not_flagged_when_fresh(monkeypatch):
    """A freshly-refreshed cache must NOT be flagged stale (no false UNVERIFIED)."""
    from aria_service.intel.sources import ofac_sdn, _common

    monkeypatch.setitem(ofac_sdn._CACHE, "records",
                        [{"name": "SOME SANCTIONED CO", "aliases": []}])
    monkeypatch.setitem(ofac_sdn._CACHE, "fetched_at", time.time())  # fresh
    # Even if a refresh is attempted, fetched_at is recent → not stale.
    r = asyncio.run(ofac_sdn.lookup("Globex International Trading Ltd"))
    assert r["ok"] is True
    assert not r.get("stale"), "fresh cache must not be flagged stale"
    assert not r.get("source_unavailable")


# ──────────────── 2+3. orchestrator surfaces + downgrades headline ──────────

def _green_company_report(with_marker_gap: bool):
    """Build a clean GREEN company ARKDDReport. When with_marker_gap, plant the
    SANCTIONS_SOURCE_UNVERIFIED marker the freshness-gate keys off."""
    from aria_service.intel.dd_schema import ARKDDReport, RiskClassification

    report = ARKDDReport()
    report.identity.entity_name = "Globex International Trading Ltd"
    report.identity.entity_type = "company"
    # Give it verification substance so the OTHER confidence gate doesn't fire
    # — isolates the freshness gate as the sole cause of any AMBER override.
    report.identity.registration_status = "active"
    report.identity.incorporation_date = "2009-04-01"
    report.identity.directors = [{"name": "A. Director"}]
    report.risk_classification = RiskClassification.GREEN.value
    report.synthesis.risk_classification = RiskClassification.GREEN.value
    if with_marker_gap:
        report.identity.data_gaps.append(
            "ofac_sdn: SANCTIONS_SOURCE_UNVERIFIED — served stale/unavailable "
            "data, NOT freshly screened (re-screen required, not a clearance)"
        )
    return report


def test_rf2167_synthesis_gate_downgrades_green_on_unverified_sanctions():
    """The user-visible outcome: a GREEN headline with a stale sanctions source
    must be overridden to AMBER-LIGHT by the synthesis freshness-gate."""
    from aria_service.intel import dd_orchestrator
    from aria_service.intel.dd_schema import RiskClassification

    report = _green_company_report(with_marker_gap=True)
    target = {"name": "Globex International Trading Ltd", "entity_type": "company"}
    asyncio.run(dd_orchestrator._run_synthesis(target, report))

    assert report.risk_classification == RiskClassification.AMBER_LIGHT.value, (
        f"a stale/unverified sanctions source must force non-GREEN, got "
        f"{report.risk_classification}"
    )
    gate_findings = [
        f for f in report.identity.findings
        if "UNVERIFIED" in f.title and "GREEN overridden" in f.title
    ]
    assert gate_findings, "freshness-gate must record an explanatory finding"


def test_rf2167_synthesis_gate_keeps_green_without_marker():
    """Control: a clean GREEN company with NO stale-sanctions marker must stay
    GREEN (the gate must not over-trigger)."""
    from aria_service.intel import dd_orchestrator
    from aria_service.intel.dd_schema import RiskClassification

    report = _green_company_report(with_marker_gap=False)
    target = {"name": "Globex International Trading Ltd", "entity_type": "company"}
    asyncio.run(dd_orchestrator._run_synthesis(target, report))

    assert report.risk_classification == RiskClassification.GREEN.value, (
        f"a clean GREEN company must stay GREEN, got {report.risk_classification}"
    )
