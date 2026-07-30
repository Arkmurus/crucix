"""R-F2744 — watchlist re-screen integrity (Codex watchlist-integrity batch).

Drives the REAL dd_orchestrator.rescreen_watchlist against an in-memory store
and proves the four defects the Assan-Group repeated-alert investigation found:

  finding 1/4 — the re-screen baseline never advanced: every cycle re-diffed the
                frozen DD-report score and re-fired the SAME transition. Now a
                durable per-entity observation is persisted and used as the
                baseline, so a repeat produces exactly ONE alert.
  finding 6  — a degraded/partial sanctions source returns matches=[] which read
                as CLEAN/0.0 → a false "removed"/score-drop. NEVER-FALSE-CLEAN:
                an unperformed screen must not emit an improvement and must not
                advance the baseline.
  finding 7  — the >0.1 threshold ran on raw scores while the alert rendered 2dp,
                so a raw 0.899-vs-1.0 (renders 0.90) fired a phantom 1.00→0.90.
  finding 2  — no idempotency: the transition now carries a deterministic
                fingerprint bucketed at the displayed precision.

Every test calls rescreen_watchlist (the path the operator hit), not a helper.
"""
from __future__ import annotations

import asyncio
import pytest

from aria_service.intel import dd_orchestrator as o
import aria_service.intel.sanctions as _sanc
import aria_service.intel._sanctions_classify as _cls


class _Store:
    """Minimal async redis_store stand-in (dict-backed)."""
    def __init__(self):
        self.d = {}
    async def get_json_strict(self, k):
        # R-F3506 — same answer, strict contract
        return await self.get_json(k)

    async def get_json(self, k):
        return self.d.get(k)
    async def set_json(self, k, v, ex=None, keepttl=False):
        self.d[k] = v
    async def lpush(self, k, v):
        self.d.setdefault(k, []).insert(0, v)
    async def ltrim(self, k, a, b):
        pass
    async def expire(self, k, s):
        pass


@pytest.fixture
def store(monkeypatch):
    s = _Store()
    import aria_service.intel.redis_store as rs
    # R-F3506 — strict watchlist reads must be faked too
    for fn in ("get_json", "get_json_strict", "set_json", "lpush", "ltrim", "expire"):
        monkeypatch.setattr(rs, fn, getattr(s, fn))
    # keep the loop hermetic: no real deal fan-out, no entity-shape rejects.
    monkeypatch.setattr(_sanc, "_looks_like_entity_name", lambda n: True, raising=False)

    async def _no_fanout(alert):
        return []
    monkeypatch.setattr(o, "_fan_out_alert_to_deals", _no_fanout, raising=False)
    return s


def _mock_screen(monkeypatch, *, matches, severity, screened=True, extra=None):
    """Point both sanctions entrypoints + the classifier at a fixed result."""
    async def _fake_screen(name, *a, **k):
        res = {"matches": list(matches), "screened": screened, "name": name}
        if extra:
            res.update(extra)
        return res
    monkeypatch.setattr(_sanc, "screen_with_aliases", _fake_screen, raising=False)
    monkeypatch.setattr(_sanc, "fuzzy_screen", _fake_screen, raising=False)
    monkeypatch.setattr(
        _cls, "classify_matches",
        lambda m, query_name="": {"worst_severity": severity, "summary": ""},
    )


def _alerts(store):
    import json
    return [json.loads(a) for a in store.d.get(o.WATCHLIST_ALERTS_KEY, [])]


# ── finding 1/4 — advancing baseline: a repeated transition fires ONCE ──────────
def test_repeated_transition_alerts_once(store, monkeypatch):
    async def run():
        store.d[o.WATCHLIST_KEY] = [{"name": "Assan Group", "user_id": "u1"}]
        store.d[o.REPORT_INDEX_KEY] = [
            {"run_id": "dd1", "entity_name": "Assan Group"},
        ]
        store.d[o.REPORT_REDIS_KEY.format(run_id="dd1")] = {
            "identity": {"findings": [],
                         "sanctions_screen": {"matches": [{"score": 1.0}]}},
        }
        # every re-screen sees a stable 0.80 (a genuine drop from the 1.0 report)
        _mock_screen(monkeypatch, matches=[{"score": 0.80, "name": "Assan"}], severity="clean")
        r1 = await o.rescreen_watchlist(user_id=None)
        r2 = await o.rescreen_watchlist(user_id=None)   # SAME screen again
        r3 = await o.rescreen_watchlist(user_id=None)   # …and again
        return r1, r2, r3
    r1, r2, r3 = asyncio.run(run())
    # Cycle 1 fires the 1.00→0.80 change; cycles 2 & 3 compare against the
    # advanced 0.80 baseline → nothing new. Pre-fix this produced 3 alerts.
    assert len(r1["changes_detected"]) == 1, r1["changes_detected"]
    assert r2["changes_detected"] == [], r2["changes_detected"]
    assert r3["changes_detected"] == [], r3["changes_detected"]
    assert len(_alerts(store)) == 1


# ── finding 7 — precision-consistent threshold: 1.00 vs 0.90 must NOT fire ──────
def test_precision_boundary_no_phantom_alert(store, monkeypatch):
    async def run():
        store.d[o.WATCHLIST_KEY] = [{"name": "Assan Group", "user_id": "u1"}]
        store.d[o.REPORT_INDEX_KEY] = [{"run_id": "dd1", "entity_name": "Assan Group"}]
        store.d[o.REPORT_REDIS_KEY.format(run_id="dd1")] = {
            "identity": {"findings": [],
                         "sanctions_screen": {"matches": [{"score": 1.0}]}},
        }
        # raw 0.899 renders as 0.90; |1.00 - 0.90| == 0.10, NOT > 0.10 → no alert
        _mock_screen(monkeypatch, matches=[{"score": 0.899}], severity="clean")
        return await o.rescreen_watchlist(user_id=None)
    r = asyncio.run(run())
    assert r["changes_detected"] == [], r["changes_detected"]


# ── finding 6 — NEVER-FALSE-CLEAN: a degraded source cannot emit an improvement ──
def test_degraded_source_never_false_clean(store, monkeypatch):
    async def run():
        store.d[o.WATCHLIST_KEY] = [{"name": "Assan Group", "user_id": "u1"}]
        store.d[o.REPORT_INDEX_KEY] = [{"run_id": "dd1", "entity_name": "Assan Group"}]
        # prior DD says HIT @0.95
        store.d[o.REPORT_REDIS_KEY.format(run_id="dd1")] = {
            "identity": {
                "findings": [{"severity": "hard_stop", "source": "sanctions"}],
                "sanctions_screen": {"matches": [{"score": 0.95}]},
            },
        }
        # OpenSanctions unreachable → R-F1696 source_unavailable, matches=[]
        _mock_screen(
            monkeypatch, matches=[], severity="clean", screened=False,
            extra={"source_unavailable": True, "error": "sanctions_source_unavailable"},
        )
        r = await o.rescreen_watchlist(user_id=None)
        obs_key = o.WATCHLIST_OBS_KEY.format(
            obs_id=o._watchlist_obs_id({"name": "Assan Group", "user_id": "u1"}, "Assan Group"))
        return r, (obs_key in store.d)
    r, obs_written = asyncio.run(run())
    # A degraded screen must NOT produce a "removed"/clean alert…
    assert r["changes_detected"] == [], r["changes_detected"]
    # …must be recorded as a degraded (unperformed) screen, not a clearance…
    assert any(e.get("degraded") for e in r["errors"]), r["errors"]
    # …and must NOT advance the baseline (no CLEAN observation persisted).
    assert obs_written is False


# ── first baseline bootstraps from the DD report (a real change still fires) ─────
def test_first_baseline_bootstraps_from_report(store, monkeypatch):
    async def run():
        store.d[o.WATCHLIST_KEY] = [{"name": "Assan Group", "user_id": "u1"}]
        store.d[o.REPORT_INDEX_KEY] = [{"run_id": "dd1", "entity_name": "Assan Group"}]
        store.d[o.REPORT_REDIS_KEY.format(run_id="dd1")] = {
            "identity": {"findings": [],
                         "sanctions_screen": {"matches": [{"score": 0.50}]}},
        }
        _mock_screen(monkeypatch, matches=[{"score": 0.90}], severity="clean")
        return await o.rescreen_watchlist(user_id=None)
    r = asyncio.run(run())
    assert len(r["changes_detected"]) == 1
    assert r["changes_detected"][0]["change_type"] == "score_change"


# ── a genuine worsening (CLEAN → HIT) still fires ──────────────────────────────
def test_real_new_hit_still_fires(store, monkeypatch):
    async def run():
        store.d[o.WATCHLIST_KEY] = [{"name": "Assan Group", "user_id": "u1"}]
        store.d[o.REPORT_INDEX_KEY] = []   # no prior report → baseline CLEAN/0.0
        _mock_screen(monkeypatch, matches=[{"score": 0.97}], severity="hard_stop")
        return await o.rescreen_watchlist(user_id=None)
    r = asyncio.run(run())
    assert len(r["changes_detected"]) == 1
    assert r["changes_detected"][0]["change_type"] == "new_hit"


# ── finding — observation id is tenant-scoped and stable ───────────────────────
def test_obs_id_tenant_scoped_and_stable():
    e1 = {"name": "Assan Group", "user_id": "userA"}
    e2 = {"name": "Assan Group", "user_id": "userB"}
    a = o._watchlist_obs_id(e1, "Assan Group")
    b = o._watchlist_obs_id(e2, "Assan Group")
    assert a != b                       # different owners → different baselines
    assert a == o._watchlist_obs_id(e1, "Assan Group")   # stable for same owner


# ── finding 2 — fingerprint is deterministic and buckets at 2dp ────────────────
def test_fingerprint_buckets_at_display_precision():
    fp = o._watchlist_obs_fingerprint
    # 0.899 and 0.901 both render as 0.90 → same transition fingerprint
    assert fp("k", "score_change", "CLEAN", "CLEAN", 1.0, 0.899) == \
           fp("k", "score_change", "CLEAN", "CLEAN", 1.0, 0.901)
    # a genuinely different bucket → different fingerprint
    assert fp("k", "score_change", "CLEAN", "CLEAN", 1.0, 0.80) != \
           fp("k", "score_change", "CLEAN", "CLEAN", 1.0, 0.90)
