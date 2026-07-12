"""R-F2560 — OFAC/UN/FCDO designation-diff feed.

Proves: baseline emits nothing; a genuinely-new designation emits an alert; a
dead/tiny fetch is SKIPPED (snapshot kept, gap recorded — never-false-clean); the
per-run cap holds; and the bridge adapter promotes a new designation as decision-grade
tier_1a sanctions_change.
"""
from __future__ import annotations

import asyncio
import json

from aria_service.intel import sanctions_designation_diff as sdd
from aria_service.intel import golden_intel_bridge as bridge


def _rs_stub(monkeypatch):
    store: dict = {}
    lists: dict = {}
    from aria_service.intel import redis_store as rs
    async def get_json(k):
        return store.get(k)
    async def set_json(k, v, *a, **kw):
        store[k] = v
    async def lpush(k, v, **kw):
        lists.setdefault(k, []).insert(0, v)
    async def ltrim(k, a, b):
        if a == 0:
            lists[k] = lists.get(k, [])[:b + 1]
    async def lrange(k, a, b):
        return lists.get(k, [])
    for n, f in (("get_json", get_json), ("set_json", set_json), ("lpush", lpush),
                 ("ltrim", ltrim), ("lrange", lrange)):
        monkeypatch.setattr(rs, n, f)
    return store, lists


def _set_loaders(monkeypatch, pairs):
    async def fake():
        return pairs
    monkeypatch.setattr(sdd, "_loaders", fake)


def _ofac(uids):
    async def loader():
        return [{"uid": u, "name": f"Entity {u}", "list_type": "SDN",
                 "programs": ["RUSSIA-EO14024"],
                 "citation_url": f"https://sanctionssearch.ofac.treas.gov/Details.aspx?id={u}"}
                for u in uids]
    return loader


def _snap(source):
    return sdd._SNAPSHOT_KEY.format(source=source)


def test_first_run_is_baseline_no_alerts(monkeypatch):
    store, lists = _rs_stub(monkeypatch)
    _set_loaders(monkeypatch, [("ofac", _ofac([str(i) for i in range(1, 12)]))])
    r = asyncio.run(sdd.run_designation_diff())
    assert r["sources"]["ofac"]["baseline"] == 11
    assert lists.get(sdd._ALERTS_KEY, []) == []          # baseline emits nothing
    assert len(store[_snap("ofac")]) == 11               # snapshot recorded


def test_new_designation_emits_alert(monkeypatch):
    store, lists = _rs_stub(monkeypatch)
    store[_snap("ofac")] = [str(i) for i in range(1, 12)]     # prior 1..11
    _set_loaders(monkeypatch, [("ofac", _ofac([str(i) for i in range(1, 13)]))])  # +uid 12
    r = asyncio.run(sdd.run_designation_diff())
    assert r["sources"]["ofac"]["new"] == 1
    alerts = [json.loads(x) for x in lists.get(sdd._ALERTS_KEY, [])]
    assert len(alerts) == 1
    assert alerts[0]["id"] == "12" and alerts[0]["list_type"] == "SDN" and alerts[0]["entity"] == "Entity 12"


def test_no_change_no_alert(monkeypatch):
    store, lists = _rs_stub(monkeypatch)
    ids = [str(i) for i in range(1, 12)]
    store[_snap("ofac")] = ids
    _set_loaders(monkeypatch, [("ofac", _ofac(ids))])
    r = asyncio.run(sdd.run_designation_diff())
    assert r["sources"]["ofac"]["new"] == 0
    assert lists.get(sdd._ALERTS_KEY, []) == []


def test_dead_fetch_skips_diff_keeps_snapshot(monkeypatch):
    store, lists = _rs_stub(monkeypatch)
    fails: list = []
    monkeypatch.setattr(sdd, "wire_failure", lambda *a, **k: fails.append(1))
    prior = [str(i) for i in range(1, 101)]                   # prior 100
    store[_snap("ofac")] = list(prior)
    _set_loaders(monkeypatch, [("ofac", _ofac(["1", "2", "3"]))])  # only 3 -> unhealthy
    r = asyncio.run(sdd.run_designation_diff())
    assert r["sources"]["ofac"]["skipped"] == "unhealthy_fetch"
    assert lists.get(sdd._ALERTS_KEY, []) == []              # no alerts
    assert store[_snap("ofac")] == prior                     # snapshot UNCHANGED
    assert fails                                             # gap recorded


def test_new_designations_capped(monkeypatch):
    store, lists = _rs_stub(monkeypatch)
    store[_snap("ofac")] = [str(i) for i in range(1, 101)]    # prior 100
    _set_loaders(monkeypatch, [("ofac", _ofac([str(i) for i in range(1, 201)]))])  # +100 new
    r = asyncio.run(sdd.run_designation_diff())
    assert r["sources"]["ofac"]["new"] == sdd._MAX_NEW_PER_SOURCE
    assert r["sources"]["ofac"]["new_uncapped"] == 100
    assert len(lists.get(sdd._ALERTS_KEY, [])) == sdd._MAX_NEW_PER_SOURCE


def test_record_id_per_source():
    assert sdd._record_id("ofac", {"uid": "42"}) == "42"
    assert sdd._record_id("un", {"group_id": "g9"}) == "g9"
    assert sdd._record_id("un", {"reference": "QDe.001"}) == "QDe.001"
    assert sdd._record_id("fcdo", {"group_id": "14212"}) == "14212"
    assert sdd._record_id("ofac", {"name": "Foo Ltd"}) == "name:foo ltd"   # fallback


def test_sanctions_diff_adapter_is_decision_grade(monkeypatch):
    async def fake_alerts(since_hours=168):
        return [{"source": "ofac", "id": "999", "entity": "Baykar Teknoloji", "list_type": "SDN",
                 "programs": "RUSSIA-EO14024", "designation_date": "",
                 "citation_url": "https://sanctionssearch.ofac.treas.gov/Details.aspx?id=999",
                 "timestamp": "2026-07-12T10:00:00+00:00"}]
    monkeypatch.setattr(sdd, "get_designation_alerts", fake_alerts)
    findings = asyncio.run(bridge._sanctions_diff_adapter())
    assert len(findings) == 1
    f = findings[0]
    assert f["signal_type"] == "sanctions_change" and f["priority"] == "HIGH" and f["source_tier"] == "tier_1a"
    assert bridge._is_distribution_ready(bridge._normalize_finding_to_signal(f)) is True


def test_partial_fetch_does_not_shrink_or_flood(monkeypatch):
    """Review #1: a partial fetch (>=50%, passes the guard) must NOT shrink the snapshot,
    so recovery cannot re-emit long-standing designations as false 'newly designated'."""
    store, lists = _rs_stub(monkeypatch)
    prior = [str(i) for i in range(1, 101)]                       # prior 100
    store[_snap("ofac")] = list(prior)
    _set_loaders(monkeypatch, [("ofac", _ofac([str(i) for i in range(1, 61)]))])  # 60% subset
    r1 = asyncio.run(sdd.run_designation_diff())
    assert r1["sources"]["ofac"]["new"] == 0
    assert lists.get(sdd._ALERTS_KEY, []) == []
    assert set(store[_snap("ofac")]) == set(prior)               # snapshot NOT shrunk
    _set_loaders(monkeypatch, [("ofac", _ofac(list(prior)))])    # recovery to full 100
    r2 = asyncio.run(sdd.run_designation_diff())
    assert r2["sources"]["ofac"]["new"] == 0
    assert lists.get(sdd._ALERTS_KEY, []) == []                  # NO false flood on recovery


def test_over_cap_remainder_drains_next_run(monkeypatch):
    """Review #2: >cap new designations promote _MAX/run; the remainder is NOT lost."""
    store, lists = _rs_stub(monkeypatch)
    store[_snap("ofac")] = [str(i) for i in range(1, 101)]        # prior 100
    full = [str(i) for i in range(1, 201)]                        # +100 new
    _set_loaders(monkeypatch, [("ofac", _ofac(full))])
    r1 = asyncio.run(sdd.run_designation_diff())
    assert r1["sources"]["ofac"]["new"] == sdd._MAX_NEW_PER_SOURCE
    assert r1["sources"]["ofac"]["new_uncapped"] == 100
    _set_loaders(monkeypatch, [("ofac", _ofac(full))])
    r2 = asyncio.run(sdd.run_designation_diff())
    assert r2["sources"]["ofac"]["new"] == 50                     # remainder drains
    _set_loaders(monkeypatch, [("ofac", _ofac(full))])
    r3 = asyncio.run(sdd.run_designation_diff())
    assert r3["sources"]["ofac"]["new"] == 0                      # fully drained
    assert len(lists.get(sdd._ALERTS_KEY, [])) == 100            # all 100 eventually promoted


def test_source_error_does_not_starve_others(monkeypatch):
    """Review #3: an error on one source must not stop the other sources running."""
    store, lists = _rs_stub(monkeypatch)
    monkeypatch.setattr(sdd, "wire_failure", lambda *a, **k: None)
    monkeypatch.setattr(sdd, "wire_success", lambda *a, **k: None)
    async def boom():
        raise RuntimeError("state_store timeout")
    async def un_loader():
        return [{"group_id": f"g{i}", "name": f"UN {i}", "list_type": "UN_SC",
                 "citation_url": "https://www.un.org/x"} for i in range(1, 12)]
    _set_loaders(monkeypatch, [("ofac", boom), ("un", un_loader)])
    r = asyncio.run(sdd.run_designation_diff())
    assert "error" in r["sources"]["ofac"]
    assert r["sources"]["un"]["baseline"] == 11                  # un still ran


if __name__ == "__main__":
    import sys
    import pytest
    sys.exit(pytest.main([__file__, "-v"]))
