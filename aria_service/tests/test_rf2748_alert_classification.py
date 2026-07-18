"""R-F2748 — classify watchlist alerts adverse vs informational (Codex finding 8).

A score DROP / removal is informational (lower fuzzy-match quality is not itself
an adverse event); a new hit / new PEP / rising score is adverse. rescreen_watchlist
tags every alert with `category`, and the daily loop reserves URGENT WhatsApp
delivery for adverse categories only. Drives the real rescreen_watchlist.
"""
from __future__ import annotations

import asyncio
import re
from pathlib import Path

import pytest

from aria_service.intel import dd_orchestrator as o
import aria_service.intel.sanctions as _sanc
import aria_service.intel._sanctions_classify as _cls


class _Store:
    def __init__(self):
        self.d = {}
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
    async def incr(self, k, amount=1, *, critical=False):
        self.d[k] = int(self.d.get(k, 0)) + amount
        return self.d[k]
    async def delete(self, k):
        self.d.pop(k, None)
        return True


@pytest.fixture
def store(monkeypatch):
    s = _Store()
    import aria_service.intel.redis_store as rs
    for fn in ("get_json", "set_json", "lpush", "ltrim", "expire", "incr", "delete"):
        monkeypatch.setattr(rs, fn, getattr(s, fn))
    monkeypatch.setattr(_sanc, "_looks_like_entity_name", lambda n: True, raising=False)

    async def _no_fanout(alert):
        return []
    monkeypatch.setattr(o, "_fan_out_alert_to_deals", _no_fanout, raising=False)
    return s


def _mock_screen(monkeypatch, *, matches, severity):
    async def _fake_screen(name, *a, **k):
        return {"matches": list(matches), "screened": True, "name": name}
    monkeypatch.setattr(_sanc, "screen_with_aliases", _fake_screen, raising=False)
    monkeypatch.setattr(_sanc, "fuzzy_screen", _fake_screen, raising=False)
    monkeypatch.setattr(
        _cls, "classify_matches",
        lambda m, query_name="": {"worst_severity": severity, "summary": ""})


def _seed_report(store, *, findings, score):
    store.d[o.WATCHLIST_KEY] = [{"name": "Assan Group", "user_id": "u1"}]
    store.d[o.REPORT_INDEX_KEY] = [{"run_id": "dd1", "entity_name": "Assan Group"}]
    store.d[o.REPORT_REDIS_KEY.format(run_id="dd1")] = {
        "identity": {"findings": findings,
                     "sanctions_screen": {"matches": [{"score": score}]}},
    }


def _only_change(r):
    assert len(r["changes_detected"]) == 1, r["changes_detected"]
    return r["changes_detected"][0]


# ── a score DROP is informational ──────────────────────────────────────────────
def test_score_drop_is_informational(store, monkeypatch):
    async def run():
        _seed_report(store, findings=[], score=1.0)
        _mock_screen(monkeypatch, matches=[{"score": 0.80}], severity="clean")
        return await o.rescreen_watchlist(user_id=None)
    ch = _only_change(asyncio.run(run()))
    assert ch["change_type"] == "score_change"
    assert ch["category"] == "informational", ch


# ── a rising score is adverse ─────────────────────────────────────────────────
def test_score_rise_is_adverse(store, monkeypatch):
    async def run():
        _seed_report(store, findings=[], score=0.50)
        _mock_screen(monkeypatch, matches=[{"score": 0.95}], severity="clean")
        return await o.rescreen_watchlist(user_id=None)
    ch = _only_change(asyncio.run(run()))
    assert ch["change_type"] == "score_change"
    assert ch["category"] == "adverse", ch


# ── a new hit is adverse ──────────────────────────────────────────────────────
def test_new_hit_is_adverse(store, monkeypatch):
    async def run():
        store.d[o.WATCHLIST_KEY] = [{"name": "Assan Group", "user_id": "u1"}]
        store.d[o.REPORT_INDEX_KEY] = []
        _mock_screen(monkeypatch, matches=[{"score": 0.97}], severity="hard_stop")
        return await o.rescreen_watchlist(user_id=None)
    ch = _only_change(asyncio.run(run()))
    assert ch["change_type"] == "new_hit"
    assert ch["category"] == "adverse", ch


# ── a removal (was HIT, now source-confirmed clean) is informational ───────────
def test_removal_is_informational(store, monkeypatch):
    async def run():
        _seed_report(
            store, findings=[{"severity": "hard_stop", "source": "sanctions"}], score=0.95)
        # source-COMPLETE clean screen (screened=True, no matches) → genuine removal
        _mock_screen(monkeypatch, matches=[], severity="clean")
        return await o.rescreen_watchlist(user_id=None)
    ch = _only_change(asyncio.run(run()))
    assert ch["change_type"] == "removed"
    assert ch["category"] == "informational", ch


# ── the daily loop gates URGENT WhatsApp to adverse only (source contract) ─────
def test_daily_loop_gates_wa_to_adverse():
    src = (Path(o.__file__).resolve().parents[1] / "main.py").read_text(encoding="utf-8")
    # the watchlist WA block must build an `adverse` list and send only when it
    # is non-empty — NOT blast every changes_detected entry.
    assert re.search(r"adverse\s*=\s*\[", src), "no adverse filter in the WA path"
    assert 'ch.get("category") == "adverse"' in src
    assert re.search(r"if adverse:", src), "WA send is not gated on adverse"
