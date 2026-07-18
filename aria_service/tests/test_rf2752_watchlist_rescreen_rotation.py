"""R-F2752 — watchlist re-screen ROTATION CURSOR (capability test).

The 360° review (2026-07-18) found a starvation defect: the watchlist holds up
to 200 entries but a re-screen cycle enforces a 50-entity cost cap. The old code
took ``watchlist[:50]`` every cycle — and because add_to_watchlist front-inserts
(newest first) with no rotation, that was ALWAYS the 50 most-recently-ADDED
entries. Positions 51..N were NEVER re-screened, so an oldest-enrolled
counterparty newly added to a sanctions list would never alert.

R-F2752 orders each cycle by ``last_rescreened_at`` ASC (never-rescreened first,
then oldest), using the per-entity observation ts R-F2744 persists after EVERY
source-complete screen. Successive cycles therefore rotate through the whole
list within the 50/cycle cap.

Every test drives the REAL dd_orchestrator.rescreen_watchlist on the daily-loop
path (user_id=None) — the path the operator's watchlist actually runs on — not a
helper. It records WHICH entities each cycle screened to prove the tail is
reached.
"""
from __future__ import annotations

import asyncio
import pytest

from aria_service.intel import dd_orchestrator as o
import aria_service.intel.sanctions as _sanc
import aria_service.intel._sanctions_classify as _cls


class _Store:
    """Minimal async redis_store stand-in (dict-backed), incl. the R-F2746
    INCR/DELETE lock primitives so the cross-trigger guard is hermetic."""
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
    async def incr(self, k):
        self.d[k] = int(self.d.get(k, 0)) + 1
        return self.d[k]
    async def delete(self, k):
        self.d.pop(k, None)


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


def _record_screen(monkeypatch, seen):
    """Point both sanctions entrypoints at a clean, SOURCE-COMPLETE screen and
    record each name screened, so a test can see which entities a cycle touched.
    A clean+screened result advances the observation baseline (persists ts),
    which is exactly what drives the rotation."""
    async def _fake_screen(name, *a, **k):
        seen.append(name)
        return {"matches": [], "screened": True, "name": name}
    monkeypatch.setattr(_sanc, "screen_with_aliases", _fake_screen, raising=False)
    monkeypatch.setattr(_sanc, "fuzzy_screen", _fake_screen, raising=False)
    monkeypatch.setattr(
        _cls, "classify_matches",
        lambda m, query_name="": {"worst_severity": "clean", "summary": ""},
    )


def test_rotation_covers_all_entries_across_cycles(store, monkeypatch):
    """120 entries, 50/cycle → all 120 re-screened within 3 cycles; cycles 1 and
    2 are disjoint (the tail the old code never reached is covered)."""
    N = 120
    async def run():
        store.d[o.WATCHLIST_KEY] = [
            {"name": f"Entity {i:03d}", "user_id": "u1"} for i in range(N)
        ]
        store.d[o.REPORT_INDEX_KEY] = []
        cycles = []
        for _ in range(3):
            seen: list[str] = []
            _record_screen(monkeypatch, seen)
            await o.rescreen_watchlist(user_id=None)
            cycles.append(list(seen))
        return cycles
    cycles = asyncio.run(run())

    # the 50/cycle cost cap still holds
    assert o._RESCREEN_MAX_ENTITIES == 50
    assert len(cycles[0]) == 50, cycles[0]
    assert len(cycles[1]) == 50, cycles[1]
    # rotation: cycle 2 must NOT re-screen cycle-1 entities while others starve
    assert set(cycles[0]).isdisjoint(cycles[1]), \
        "cycle 2 re-screened cycle-1 entities instead of rotating to the tail"
    # every one of the 120 entities is covered within ceil(120/50)=3 cycles —
    # pre-fix, entries 51..119 were covered in ZERO cycles.
    covered = set(cycles[0]) | set(cycles[1]) | set(cycles[2])
    assert len(covered) == N, f"only {len(covered)}/{N} entities covered — starvation remains"


def test_least_recently_rescreened_goes_first(store, monkeypatch):
    """An entry with a FRESH observation sorts to the back — a just-rescreened
    entity is deferred, so starved (never-rescreened) entries are served first."""
    async def run():
        store.d[o.WATCHLIST_KEY] = [
            {"name": f"Entity {i:03d}", "user_id": "u1"} for i in range(60)
        ]
        store.d[o.REPORT_INDEX_KEY] = []
        # pre-seed Entity 000 with a far-future observation ts → sorts LAST
        fresh = {"name": "Entity 000", "user_id": "u1"}
        obs_key = o.WATCHLIST_OBS_KEY.format(
            obs_id=o._watchlist_obs_id(fresh, "Entity 000"))
        store.d[obs_key] = {"status": "CLEAN", "score": 0.0,
                            "ts": "2999-01-01T00:00:00+00:00"}
        seen: list[str] = []
        _record_screen(monkeypatch, seen)
        await o.rescreen_watchlist(user_id=None)
        return seen
    seen = asyncio.run(run())
    assert len(seen) == 50
    assert "Entity 000" not in seen, \
        "a freshly-rescreened entry was screened again ahead of starved ones"
