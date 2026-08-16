"""R-F4080 (C-128) — a counter named `stats_24h` keeps a 14-DAY window.

Found during the C-109 TTL audit and left standing, which is how a fix for a
CLASS of defect becomes a fix for one instance of it. Measured live
2026-08-16 across every `*stats_24h*` key in the store:

    281.5h  crucix:learning:harvest:stats_24h
      0.7h  crucix:learning:spider:stats_24h
      3.8h  crucix:learning:research:stats_24h
      7.5h  crucix:learning:memory_backup:stats_24h

Every sibling rolls inside a day. `output_harvester` writes its counter with
`ex=14 * 86400` — deliberately, and its docstring explains why: an earlier
version reset the TTL on every event so the counter never rolled at all. The
window is intentional. **The NAME is the defect.**

That matters because the name is what a reader trusts. During the C-109 audit
this key was flagged as suspicious purely on its name, and confirming it took a
live TTL probe. A future reader comparing `harvest.total_scored` against a
genuinely-24h sibling would be comparing a fortnight to a day and would have no
way to see it.

Not a display bug: nothing renders this key, and it is not in
`memory_replication`'s replicated set. It is a correctness-of-naming bug in a
counter that feeds `learning_stats_ep`, and the cheapest moment to fix it is
while the reason is still on the record.

The rename carries the existing counters across once, so 14 days of accumulated
harvest scoring is not thrown away to fix a label.
"""
from __future__ import annotations

import json
from unittest.mock import AsyncMock, patch

import pytest


def test_the_key_names_the_window_it_keeps():
    from aria_service.learning import output_harvester as oh

    src_ttl = oh._STATS_TTL_S
    assert src_ttl == 14 * 86400, src_ttl
    assert "24h" not in oh._REDIS_STATS_KEY, (
        f"{oh._REDIS_STATS_KEY} keeps a {src_ttl // 86400}-day window; every "
        "sibling *stats_24h* key rolls inside a day, so this name misleads "
        "anyone comparing them")
    assert "14d" in oh._REDIS_STATS_KEY, oh._REDIS_STATS_KEY


def test_ttl_and_key_cannot_drift_apart():
    """The window is derived from ONE constant that also names the key, so a
    future change to the duration cannot leave the name behind — which is the
    entire defect."""
    from aria_service.learning import output_harvester as oh
    days = oh._STATS_TTL_S // 86400
    assert oh._REDIS_STATS_KEY.endswith(f"stats_{days}d"), (
        f"key {oh._REDIS_STATS_KEY} does not match TTL of {days}d")


@pytest.mark.asyncio
async def test_existing_counters_are_carried_over_once():
    """14 days of accumulated scoring must not be discarded to fix a label."""
    from aria_service.learning import output_harvester as oh

    legacy = {"total_scored": 21, "total_passed": 3, "total_written": 2,
              "total_dry_skipped": 1}
    store: dict[str, str] = {oh._LEGACY_STATS_KEY: json.dumps(legacy)}
    deleted: list[str] = []

    async def _get_json(key):
        raw = store.get(key)
        return json.loads(raw) if raw else None

    async def _set_json(key, obj, ex=None, keepttl=False):
        store[key] = json.dumps(obj)

    async def _delete(key):
        deleted.append(key)
        return store.pop(key, None) is not None

    oh._legacy_stats_migrated = False
    with patch("aria_service.intel.redis_store.get_json", _get_json), \
         patch("aria_service.intel.redis_store.set_json", _set_json), \
         patch("aria_service.intel.redis_store.delete", _delete):
        await oh._incr_stats(passed=True, dry_run=False)

    carried = json.loads(store[oh._REDIS_STATS_KEY])
    assert carried["total_scored"] == 22, carried
    assert carried["total_passed"] == 4, carried
    assert carried["total_written"] == 3, carried
    assert oh._LEGACY_STATS_KEY in deleted, (
        "the legacy key must be retired, or the next reader finds two "
        "counters for one thing")


@pytest.mark.asyncio
async def test_a_fresh_install_needs_no_legacy_key():
    from aria_service.learning import output_harvester as oh

    store: dict[str, str] = {}

    async def _get_json(key):
        raw = store.get(key)
        return json.loads(raw) if raw else None

    async def _set_json(key, obj, ex=None, keepttl=False):
        store[key] = json.dumps(obj)

    oh._legacy_stats_migrated = False
    with patch("aria_service.intel.redis_store.get_json", _get_json), \
         patch("aria_service.intel.redis_store.set_json", _set_json), \
         patch("aria_service.intel.redis_store.delete", AsyncMock()):
        await oh._incr_stats(passed=False, dry_run=True)

    assert json.loads(store[oh._REDIS_STATS_KEY])["total_scored"] == 1
