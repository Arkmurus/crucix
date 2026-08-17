"""R-F4067 (C-110) — the freshness tracker must be able to report a stale
domain, and a flood of one-off topics must not evict the domains it exists for.

Measured on aria-intel 2026-08-16, `crucix:aria:learning_progress:domains`:

    tracked: 1000        oldest first_seen: 47.1h ago     newest: 0.14h ago
    minted <24h: 999     minted <168h: 1000
    source prefix: knowledge 993 · intel_ledger 7

    sanctions_screening    ABSENT      fcpa_enforcement     ABSENT
    fatf_ml_typologies     ABSENT      virtual_assets       ABSENT
    weapon_systems         ABSENT      eccn_classification  ABSENT

The brain page rendered `0 stale / 1000 · Fresh 1000 · Stale 0 (0%)`.

`stale` means `hours_since_refresh > max_staleness_hours`, and the default
window is 168h. `record_refresh` caps the store at 1000 and evicts the
least-recently-touched. `knowledge.add_fact` (R-F96) registers **every fact's
topic** as a domain — live entries include `'rage_bait_pays'_headline` and
`13-year-old_shoplifting_suspect` — at a rate that turns the whole table over in
under 48 hours. **Eviction always beats the staleness clock, so `stale_count`
was pinned at zero by construction**: a guard whose universe empties faster than
its own window can never fire.

It is not only a display defect. `stale_domains()` feeds
`continuous_update.recompute_priorities()` — the R-F90 orchestrator's Layer 1
urgency input. With an empty stale list, the freshness-driven half of the
refresh scheduler contributes nothing, so the sanctions / FATF / ECCN surfaces
that `_MAX_STALENESS_OVERRIDES` gives 24h-to-14d SLAs were never re-targeted.

The fix does NOT raise the cap (that delays the same failure behind an unbounded
blob) and does NOT stop `knowledge.add_fact` registering topics (R-F96's intent
— any ingest updates freshness — is sound, and plain recurring topics like
`compliance` are legitimate domains that are not in the override map). It
distinguishes the two populations already present in the data:

  * **protected** — in `_MAX_STALENESS_OVERRIDES`, or matching a curated prefix,
    or having recurred (`refresh_count >= 2`). Live, 999 of 1000 entries had
    `refresh_count: 1`; a topic that comes back is a real ingest surface.
  * **ambient** — seen once, never again. Evicted first, and pruned once past
    its own window, because a single-shot free-text topic has no SLA to miss.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest


# ── in-memory stand-in for the state store ─────────────────────────────────

class _FakeStore:
    """Only the two calls record_refresh/get_all_domains make."""

    def __init__(self, initial: dict | None = None):
        self.data: dict = dict(initial or {})

    async def get_json(self, key):
        return json.loads(json.dumps(self.data))

    async def get_strict(self, key):
        # R-F4097 (C-152): the real store returns a RAW STRING here and raises
        # StoreReadError on failure. record_refresh reads strictly now, so a
        # fake without this method would make every write skip.
        return json.dumps(self.data)

    async def set_json(self, key, obj, ex=None):
        self.data = json.loads(json.dumps(obj))


def _rec(domain: str, *, hours_ago: float, refresh_count: int = 1,
         source: str = "knowledge:research:web_search:x") -> dict:
    ts = (datetime.now(timezone.utc) - timedelta(hours=hours_ago)).isoformat()
    return {
        "domain": domain,
        "first_seen_at": ts,
        "last_refreshed_at": ts,
        "last_refresh_source": source,
        "facts_count": 1,
        "signals_count": 0,
        "refresh_count": refresh_count,
    }


@pytest.fixture
def store(monkeypatch):
    from aria_service.intel import learning_progress as lp
    s = _FakeStore()

    async def _fake_redis():
        return s

    monkeypatch.setattr(lp, "_redis", _fake_redis)
    return s


# ── 1. the curated domains must survive a flood ────────────────────────────

@pytest.mark.asyncio
async def test_curated_domain_survives_a_flood_of_one_off_topics(store):
    """Reproduces the live shape: sanctions_screening present, then 1200
    single-shot research topics arrive. It must still be there."""
    from aria_service.intel import learning_progress as lp

    store.data["sanctions_screening"] = _rec(
        "sanctions_screening", hours_ago=72, refresh_count=40,
        source="intel_ledger:ofac")

    for i in range(1200):
        await lp.record_refresh(
            f"'headline_{i}'_fragment",
            source="knowledge:research:web_search:noise",
        )

    assert "sanctions_screening" in store.data, (
        "the domain the tracker exists for was evicted by single-shot research "
        f"topics. tracked={len(store.data)}")
    # And it must still be able to say it is stale: 72h against a 24h window.
    all_d = await lp.get_all_domains()
    rec = next(d for d in all_d if d["domain"] == "sanctions_screening")
    assert rec["max_staleness_hours"] == 24, rec
    assert rec["is_stale"] is True, rec


@pytest.mark.asyncio
async def test_stale_domains_is_not_empty_under_flood(store):
    """`stale_domains()` drives the R-F90 refresh orchestrator. An empty list
    is what starved it."""
    from aria_service.intel import learning_progress as lp

    for name, hours in (("sanctions_screening", 72),
                        ("fatf_ml_typologies", 400),
                        ("weapon_systems", 800)):
        store.data[name] = _rec(name, hours_ago=hours, refresh_count=9,
                                source="intel_ledger:seed")
    for i in range(1100):
        await lp.record_refresh(f"topic_{i}_fragment",
                                source="knowledge:research:x")

    stale = await lp.stale_domains()
    names = {s["domain"] for s in stale}
    assert {"sanctions_screening", "fatf_ml_typologies",
            "weapon_systems"} <= names, (
        f"the orchestrator's staleness input is starved: {sorted(names)[:10]}")


# ── 2. recurrence, not an allowlist, is what protects a plain topic ────────

@pytest.mark.asyncio
async def test_a_recurring_topic_is_protected_even_though_it_is_not_curated(store):
    """`compliance` is a real ingest surface but is NOT in
    _MAX_STALENESS_OVERRIDES. An allowlist-only fix would have dropped it."""
    from aria_service.intel import learning_progress as lp

    await lp.record_refresh("compliance", source="knowledge:doc")
    await lp.record_refresh("compliance", source="knowledge:doc")
    assert store.data["compliance"]["refresh_count"] == 2

    for i in range(1100):
        await lp.record_refresh(f"noise_{i}", source="knowledge:research:x")

    assert "compliance" in store.data, (
        "a topic that recurred is a real domain and must not be evicted")


# ── 3. the ambient population must not read as a green light ──────────────

@pytest.mark.asyncio
async def test_stats_separates_protected_from_ambient(store):
    """`0 stale / 1000` was green because 999 of the 1000 could not go stale.
    The split has to be visible or the panel keeps certifying an unmeasurable
    population."""
    from aria_service.intel import learning_progress as lp

    store.data["sanctions_screening"] = _rec(
        "sanctions_screening", hours_ago=72, refresh_count=40,
        source="intel_ledger:ofac")
    for i in range(50):
        await lp.record_refresh(f"noise_{i}", source="knowledge:research:x")

    st = await lp.stats()
    assert "protected_total" in st and "ambient_total" in st, (
        f"stats must distinguish the two populations: {sorted(st)}")
    assert st["protected_total"] >= 1
    assert st["ambient_total"] >= 50
    assert st["protected_stale"] == 1, st
    # The legacy fields keep their meaning — other readers depend on them.
    assert st["tracked_total"] == st["protected_total"] + st["ambient_total"]


# ── 4. single-shot topics drain instead of accumulating forever ───────────

@pytest.mark.asyncio
async def test_aged_out_ambient_entries_are_pruned(store):
    """An unprotected topic seen once, already past its own window, has no SLA
    left to miss and must not hold a slot against a real domain."""
    from aria_service.intel import learning_progress as lp

    store.data["old_noise"] = _rec("old_noise", hours_ago=200)   # window 168h
    store.data["fresh_noise"] = _rec("fresh_noise", hours_ago=2)

    await lp.record_refresh("sanctions_screening", source="intel_ledger:ofac")

    assert "old_noise" not in store.data, "aged-out single-shot topic kept"
    assert "fresh_noise" in store.data, "a still-fresh topic must be kept"
    assert "sanctions_screening" in store.data


@pytest.mark.asyncio
async def test_pruning_never_touches_a_protected_domain(store):
    """A curated domain being stale is the SIGNAL. Pruning it would delete the
    very thing the tracker is for."""
    from aria_service.intel import learning_progress as lp

    store.data["fatf_ml_typologies"] = _rec(
        "fatf_ml_typologies", hours_ago=5000, refresh_count=3,
        source="intel_ledger:fatf")

    await lp.record_refresh("virtual_assets", source="intel_ledger:crypto")

    assert "fatf_ml_typologies" in store.data, (
        "a long-stale curated domain must be REPORTED, never pruned")
    stale = await lp.stale_domains()
    assert any(s["domain"] == "fatf_ml_typologies" for s in stale)


# ── 5. the cap still holds ─────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_store_stays_bounded(store):
    from aria_service.intel import learning_progress as lp

    for i in range(1500):
        await lp.record_refresh(f"t{i}", source="knowledge:research:x")
    assert len(store.data) <= lp._MAX_TRACKED_DOMAINS, len(store.data)
