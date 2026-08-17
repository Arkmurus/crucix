"""R-F4097 (C-152) — a store read failure WIPED the whole freshness tracker.

`record_refresh` did a read-modify-write of the entire domain dict:

    existing = await rs.get_json(_REDIS_KEY)      # non-strict
    if not isinstance(existing, dict):
        existing = {}
    ...
    await rs.set_json(_REDIS_KEY, existing, ex=_TTL_SECONDS)

`get_json` honours the R-F1 None-on-error contract (`redis_store.py:299-303`):
it returns `None` for a genuinely absent key **and** for a store failure, and
those are indistinguishable. So one failed read collapsed `existing` to `{}` and
the very next line CLOBBERED the durable key with a single domain.

This is the R-F2664 shape exactly, in a second module. CLAUDE.md §1 records it
for `_load_regional_mastery`: *"a slow-boot StoreReadError poisoned
`_regional_cache` to `{}` → the next `update_regional_mastery` CLOBBERED the
durable key"*. aria-intel takes ~10 minutes to boot (§11c), so the window where
the store is not ready is large and is hit on every deploy.

**Observed live, which is what prompted the check.** The freshness panel read
`protected_total 91 / ambient_total 909` (1,000 tracked, at the cap) before a
deploy, and `8 / 128` after it. Protected domains are never pruned — the prune
predicate returns False for them by construction — so expiry cannot explain it.
A clobber can.

§7 is explicit: ARIA has infinite memory, no eviction. Losing 864 tracked
domains to a boot-time race is exactly what that rule forbids, and it was
silent: no error, no gap, just a smaller number on a panel nobody was diffing.

The fix reads STRICTLY and **skips the write** when the current contents cannot
be established. A skipped refresh loses one timestamp; a clobber loses the
tracker. `get_strict` is used rather than `get_json_strict` deliberately: the
json helper swallows a parse failure into `None`, which would put us straight
back into "unreadable looks like absent" for a corrupt value.
"""
from __future__ import annotations

import pytest

from aria_service.intel import learning_progress as lp
from aria_service.intel.redis_store import StoreReadError


class _Store:
    """Minimal stand-in: records what was written, and can fail its reads."""

    def __init__(self, initial=None, mode="ok"):
        self.value = initial
        self.mode = mode
        self.writes: list = []

    async def get_strict(self, key):
        if self.mode == "wedged":
            raise StoreReadError("store not ready")
        if self.mode == "corrupt":
            return "{not json"
        return self.value

    async def get_json(self, key):        # legacy path, must stay unused here
        if self.mode == "wedged":
            return None
        return self.value

    async def set_json(self, key, obj, ex=None):
        self.writes.append(obj)
        self.value = obj


@pytest.fixture
def store(monkeypatch):
    s = _Store()

    async def _fake_redis():
        return s

    monkeypatch.setattr(lp, "_redis", _fake_redis)
    return s


@pytest.mark.asyncio
async def test_a_wedged_store_does_not_clobber_the_tracker(store):
    import json

    store.value = json.dumps({f"topic{i}": {"domain": f"topic{i}",
                                            "refresh_count": 3,
                                            "last_refreshed_at": "2026-08-17T00:00:00+00:00"}
                              for i in range(1000)})
    store.mode = "wedged"

    await lp.record_refresh("newly_seen")

    assert store.writes == [], (
        "a failed read must SKIP the write — writing here replaces 1000 tracked "
        "domains with one, which is the R-F2664 clobber")


@pytest.mark.asyncio
async def test_an_unparseable_value_does_not_clobber_either(store):
    store.mode = "corrupt"
    await lp.record_refresh("newly_seen")
    assert store.writes == [], (
        "an unreadable value is not an empty one; `get_json_strict` would have "
        "swallowed this into None and clobbered")


@pytest.mark.asyncio
async def test_a_genuinely_absent_key_still_writes(store):
    """The guard must not break first use — absent really is empty."""
    store.value = None
    store.mode = "ok"
    await lp.record_refresh("first_topic")
    assert len(store.writes) == 1
    assert "first_topic" in store.writes[0]


@pytest.mark.asyncio
async def test_a_healthy_read_preserves_every_existing_domain(store):
    import json

    store.value = json.dumps({
        "kept": {"domain": "kept", "refresh_count": 5,
                 "last_refreshed_at": "2026-08-17T00:00:00+00:00"},
    })
    store.mode = "ok"
    await lp.record_refresh("added")
    written = store.writes[-1]
    assert "kept" in written, "an existing protected domain must survive a write"
    assert "added" in written


@pytest.mark.asyncio
async def test_the_reader_reports_an_unreadable_store_instead_of_zero(store):
    """`get_all_domains` returned `[]` on failure, so the freshness panel showed
    a confident zero for a store it could not read — the absence-as-health shape
    this batch keeps finding. It must be distinguishable."""
    store.mode = "wedged"
    st = await lp.stats()
    assert st.get("store_readable") is False, st
    assert st.get("tracked") is None, (
        "an unreadable store has no count; 0 would read as 'nothing tracked'")


@pytest.mark.asyncio
async def test_a_readable_empty_store_is_a_measured_zero(store):
    store.value = None
    store.mode = "ok"
    st = await lp.stats()
    assert st.get("store_readable") is True
    assert st.get("tracked") == 0
