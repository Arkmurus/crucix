"""R-F962 — knowledge.consolidate_facts must NOT delete facts by age (§7).

CLAUDE.md §7 is binding: ARIA has infinite memory — no TTL, no oldest-first
prune, no eviction; overflow → cold storage, never delete. Pre-R-F962 the
consolidate_facts() routine DELETED facts >90 days old with accessCount<2 (a
direct §7 violation, same class as R-F173 which R-F238 reversed). It was
reachable only via the manual POST /api/aria/neural/consolidate (no cron), so
it was a latent landmine rather than an active drain.

R-F962 replaces the age-DELETION with a non-destructive staleness FLAG: every
fact is kept forever; old rarely-used facts get `stale=True` so age is visible
(the legitimate kernel of the 2026-05-28 self-gap-analysis "recency layer"
request) without losing knowledge. Duplicate-merge (§7-safe) is preserved.
"""
from __future__ import annotations

import asyncio
import inspect
from datetime import datetime, timedelta, timezone

from aria_service.intel import knowledge as kn

# R-F3773/§16 — NOT inspect.getsource: it slices at line numbers captured AT
# IMPORT, so a mid-run edit silently returns a DIFFERENT function's body. A CLASS
# target scopes the lookup to that class's own body (R-F3771).
from ._source_probe import function_source


def _iso(days_ago: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days_ago)).isoformat()


def _patch_store(monkeypatch, db: dict):
    async def _fake_load():
        return db

    # **kwargs so the double follows `_save`'s real signature instead of
    # pinning a copy of it (R-F4022 added record=/kind=/structural=).
    async def _fake_save(**_kw):
        return None

    monkeypatch.setattr(kn, "_load", _fake_load)
    monkeypatch.setattr(kn, "_save", _fake_save)


def test_rf962_old_fact_is_flagged_not_deleted(monkeypatch):
    db = {"facts": [
        {"topic": "old-rarely-used", "content": "x", "createdAt": _iso(120), "accessCount": 0},
        {"topic": "recent", "content": "y", "createdAt": _iso(5), "accessCount": 0},
        {"topic": "old-but-used", "content": "z", "createdAt": _iso(200), "accessCount": 9},
    ]}
    _patch_store(monkeypatch, db)

    res = asyncio.run(kn.consolidate_facts())

    # §7: nothing deleted by age — all three facts survive
    assert res["pruned"] == 0, "R-F962: age-prune must report 0 deletions"
    assert res["total_after"] == res["total_before"] == 3
    assert len(db["facts"]) == 3

    by_topic = {f["topic"]: f for f in db["facts"]}
    # the old, rarely-accessed fact is FLAGGED (not removed)
    assert by_topic["old-rarely-used"].get("stale") is True
    # recent + frequently-accessed facts are NOT stale
    assert not by_topic["recent"].get("stale")
    assert not by_topic["old-but-used"].get("stale")
    assert res["flagged_stale"] == 1


def test_rf962_stale_flag_self_corrects_when_reaccessed(monkeypatch):
    # A fact previously flagged stale that has since been re-accessed must be
    # un-flagged on the next pass (flag is idempotent + self-correcting).
    db = {"facts": [
        {"topic": "revived", "content": "x", "createdAt": _iso(300),
         "accessCount": 7, "stale": True},
    ]}
    _patch_store(monkeypatch, db)

    asyncio.run(kn.consolidate_facts())
    assert db["facts"][0].get("stale") is False, "re-accessed fact must lose stale flag"


def test_rf962_still_merges_duplicates(monkeypatch):
    # §7-safe deduplication must be preserved — the higher-accessCount copy wins.
    db = {"facts": [
        {"topic": "Dup Topic", "content": "a", "createdAt": _iso(1), "accessCount": 1},
        {"topic": "dup topic", "content": "b", "createdAt": _iso(1), "accessCount": 5},
    ]}
    _patch_store(monkeypatch, db)

    res = asyncio.run(kn.consolidate_facts())
    assert res["merged"] == 1
    assert len(db["facts"]) == 1
    assert db["facts"][0]["accessCount"] == 5  # kept the better copy


def test_rf962_no_age_deletion_logic_in_source():
    """Regression guard (mirrors R-F242's §7 scan, for knowledge.py): the
    consolidate_facts source must not re-introduce an age-based deletion."""
    src = function_source(kn, "consolidate_facts")
    assert "pruned += 1" not in src, "R-F962 regression: age-prune counter crept back"
    assert 'f["stale"] = True' in src, "R-F962: non-destructive staleness flag must remain"
