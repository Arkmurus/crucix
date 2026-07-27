"""R-F2750 — expose watchlist re-screen observation provenance (Codex finding 5).

watchlist.html showed a single "Last Checked" that fell back to the add-date
because nothing wrote a re-screen timestamp, so an entity re-screened daily still
displayed its add-date. R-F2744 persists a per-entity observation; this surfaces
it via the read path and splits the UI into "Last DD" vs "Last re-screen".
"""
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from aria_service.intel import dd_orchestrator as o


class _Store:
    def __init__(self):
        self.d = {}
    async def get_json(self, k):
        return self.d.get(k)
    async def set_json(self, k, v, ex=None, keepttl=False):
        self.d[k] = v


@pytest.fixture
def store(monkeypatch):
    s = _Store()
    import aria_service.intel.redis_store as rs
    for fn in ("get_json", "set_json"):
        monkeypatch.setattr(rs, fn, getattr(s, fn))
    return s


# ── enrichment attaches the observation provenance ─────────────────────────────
def test_enrich_attaches_rescreen_provenance(store):
    async def run():
        entry = {"name": "Assan Group", "user_id": "u1", "added_at": "2026-01-01T00:00:00Z"}
        obs_key = o.WATCHLIST_OBS_KEY.format(obs_id=o._watchlist_obs_id(entry, "Assan Group"))
        store.d[obs_key] = {
            "status": "HIT", "score": 0.91, "source_complete": True,
            "ts": "2026-07-18T09:00:00Z",
        }
        return await o.enrich_watchlist_with_observations([dict(entry)])
    out = asyncio.run(run())
    e = out[0]
    assert e["last_rescreened_at"] == "2026-07-18T09:00:00Z"
    assert e["rescreen_status"] == "HIT"
    assert e["rescreen_score"] == 0.91
    assert e["source_complete"] is True


# ── an entry with no observation is left untouched (no crash, no fake data) ─────
def test_enrich_leaves_unobserved_entry_untouched(store):
    async def run():
        return await o.enrich_watchlist_with_observations(
            [{"name": "Never Screened Ltd", "user_id": "u1", "added_at": "2026-01-01T00:00:00Z"}])
    e = asyncio.run(run())[0]
    assert "last_rescreened_at" not in e   # honest: no observation → no re-screen field
    assert e["added_at"] == "2026-01-01T00:00:00Z"


# ── the read route enriches before returning ──────────────────────────────────
def test_route_calls_enrich():
    src = (Path(o.__file__).resolve().parents[1] / "routes" / "aria.py").read_text(encoding="utf-8")
    assert "enrich_watchlist_with_observations" in src, "route does not enrich the watchlist"


# ── the UI never conflates "entered monitoring" with "last re-screened" ───────
def test_ui_splits_columns_and_reads_field():
    """R-F3290 — this pinned the column CAPTIONS ("Last DD" / "Last re-screen"),
    and R-F3225 deliberately replaced them when it reworked the table around
    review cycles ("Review cycle" / "Next review" / "Last review"). The guard
    has been red ever since, asserting a caption rather than the thing R-F2750
    was actually about.

    What R-F2750 protects is the DISTINCTION: the add-date and the last
    re-screen are different facts, and showing the add-date where a re-screen
    belongs reads as "never re-screened" for an entity screened this morning.
    That property survived the rework intact, so this now asserts it.
    """
    html = (Path(o.__file__).resolve().parents[2] / "public" / "watchlist.html").read_text(encoding="utf-8")
    # The re-screen timestamp is read from its own field, not from added_at.
    assert "e.last_rescreened_at" in html, "the re-screen field is not read"
    # An entity that has never been re-screened says so, rather than borrowing
    # the add-date and looking current.
    assert "not re-screened yet" in html
    # The old conflated single column stays gone.
    assert "<th>Last Checked</th>" not in html
    # And the two facts are still rendered in separate cells.
    assert "<th>Next review</th>" in html and "<th>Last review</th>" in html
