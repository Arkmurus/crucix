"""R-F875..F879 — DD lifecycle coherence batch (honesty + §7 + monitoring).

Grounded against the Web/UI + DD-lifecycle 360 (2026-05-25):

  R-F875 — as_dict() dropped report.deception / counter_intelligence /
           sanctions_divergence (instance attrs, not dataclass fields) → a
           HIGH deception signal rendered into prose but was invisible to
           every JSON consumer. HONESTY defect. Fix: declare the 3 fields.
  R-F876 — rescreen_watchlist matched the prior report by raw entity_name,
           so "Embraer SA" vs "Embraer S.A." baselined from CLEAN/0.0 and
           fired a spurious CLEAN→CLEAN 0.00→0.833 score_change. Fix: match
           by canonical_entity_id, fall back to normalized name.
  R-F877 — DD report bodies expired after 7 days (REPORT_TTL_SECONDS) →
           re-DD past 7d couldn't diff + violated CLAUDE.md §7. Fix: no TTL.
  R-F878 — orchestrate_dd never enrolled the entity into the watchlist, so
           manually-scrutinised entities were never re-screened. Fix: auto-
           enroll on persist + add_to_watchlist enriches an existing entry.
  R-F879 — WEEKLY-DD-WATCHLIST claimed a 7-layer full re-DD but ran a
           sanctions-only rescreen. Fix: honest description.
"""
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from aria_service.intel import dd_orchestrator as o
from aria_service.intel.dd_schema import ARKDDReport


# ─────────────────────────── R-F875 ───────────────────────────
def test_rf875_as_dict_serialises_the_three_signals():
    r = ARKDDReport()
    r.deception = {"tier": "HIGH", "max_score": 0.82}
    r.counter_intelligence = {"composite_score": 0.6}
    r.sanctions_divergence = {"matches": 2, "divergence_count": 1}
    d = r.as_dict()
    assert d["deception"]["tier"] == "HIGH"
    assert d["counter_intelligence"]["composite_score"] == 0.6
    assert d["sanctions_divergence"]["matches"] == 2


def test_rf875_default_none_when_unset():
    d = ARKDDReport().as_dict()
    assert d["deception"] is None
    assert d["counter_intelligence"] is None
    assert d["sanctions_divergence"] is None


# ─────────────────────────── R-F877 ───────────────────────────
def test_rf877_report_bodies_do_not_expire():
    assert o.REPORT_TTL_SECONDS is None


# ─────────────────────────── R-F879 ───────────────────────────
def test_rf879_weekly_task_description_is_honest():
    import yaml
    y = yaml.safe_load(
        (Path(o.__file__).resolve().parents[1] / "autonomous" / "tasks.yaml").read_text(encoding="utf-8")
    )
    t = [t for t in y["tasks"] if t["id"] == "WEEKLY-DD-WATCHLIST"][0]
    desc = t["description"]
    # Must NOT claim a full 7-layer re-DD as the thing it does…
    assert "SANCTIONS RE-SCREEN" in desc
    assert "does NOT re-run the 7-layer" in desc


# ─────────────────────────── in-memory store ───────────────────────────
class _Store:
    """Minimal async redis_store stand-in (dict-backed)."""
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


@pytest.fixture
def store(monkeypatch):
    s = _Store()
    import aria_service.intel.redis_store as rs
    for fn in ("get_json", "set_json", "lpush", "ltrim", "expire"):
        monkeypatch.setattr(rs, fn, getattr(s, fn))
    return s


# ─────────────────────────── R-F878 ───────────────────────────
def test_rf878_add_to_watchlist_enriches_existing_entry(store):
    async def run():
        store.d[o.WATCHLIST_KEY] = [{"name": "Embraer SA"}]   # legacy entry, no canonical id
        res = await o.add_to_watchlist({
            "name": "Embraer SA",
            "canonical_entity_id": "company:BR:embraer",
            "entity_type": "company",
            "last_risk": "AMBER",
        })
        assert res["note"] == "already on watchlist"
        assert res["enriched"] is True
        wl = store.d[o.WATCHLIST_KEY]
        assert len(wl) == 1                                   # not duplicated
        assert wl[0]["canonical_entity_id"] == "company:BR:embraer"
        assert wl[0]["last_risk"] == "AMBER"
    asyncio.run(run())


# ─────────────────────────── R-F876 ───────────────────────────
def test_rf876_no_spurious_score_change_on_name_variant(store, monkeypatch):
    """The whole bug: a watchlist entry "Embraer SA" must resolve to the prior
    DD stored under "Embraer S.A." (same entity, cosmetic punctuation) so the
    0.833 best-match score is carried as the baseline — NOT re-baselined from
    0.0, which fired the spurious CLEAN→CLEAN 0.00→0.833 alert."""
    async def run():
        store.d[o.WATCHLIST_KEY] = [{"name": "Embraer SA"}]   # no canonical id → normalize fallback
        store.d[o.REPORT_INDEX_KEY] = [
            {"run_id": "dd_prev", "entity_name": "Embraer S.A.",
             "canonical_entity_id": "company:BR:embraer"},
        ]
        store.d[o.REPORT_REDIS_KEY.format(run_id="dd_prev")] = {
            "identity": {"findings": [],
                         "sanctions_screen": {"matches": [{"score": 0.833}]}},
        }
        # sanctions screen returns the SAME 0.833 weak match, classified CLEAN
        import aria_service.intel.sanctions as _sanc
        import aria_service.intel._sanctions_classify as _cls
        async def _fake_screen(name):
            return {"matches": [{"score": 0.833, "name": name}]}
        monkeypatch.setattr(_sanc, "screen_with_aliases", _fake_screen, raising=False)
        monkeypatch.setattr(_sanc, "fuzzy_screen", _fake_screen, raising=False)
        monkeypatch.setattr(_sanc, "_looks_like_entity_name", lambda n: True, raising=False)
        monkeypatch.setattr(_cls, "classify_matches",
                            lambda m, query_name="": {"worst_severity": "clean", "summary": ""})
        res = await o.rescreen_watchlist()
        assert res["entities_screened"] == 1
        # The fix: prior 0.833 baseline matched → no change. Pre-fix this list
        # held a spurious {"change_type": "score_change", 0.0→0.833}.
        assert res["changes_detected"] == [], res["changes_detected"]
    asyncio.run(run())
