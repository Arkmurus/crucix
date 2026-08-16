"""R-F4068 (C-109) — the chat-audit "24h" figure must be a 24h measurement.

Measured on aria-intel 2026-08-16:

    state row: crucix:chat_audit:entries_24h = '758'   expires_at = NULL
    list_entries dated 2026-08-16 → 10
    list_entries dated 2026-08-15 →  6

The brain page rendered `✅ Auto-allowed (24h) → Chat turns served 758`. The
real figure was ~10. **Roughly a 50x overstatement of the single most visible
"what did ARIA do today" number on the command centre.**

Mechanism: `record_chat()` incremented `crucix:chat_audit:entries_24h` and set a
25-hour TTL **only when the increment returned 1**. The live row carried
`expires_at = NULL`, so it never expired, so the increment never returned 1
again, so the TTL could never be re-applied. **The defect repairs its own
trigger** — once the TTL is lost the counter is a lifetime tally forever. Of the
seven `*24h*` keys in the store it was the only one without a TTL
(`crucix:autonomous:fires_24h` = 432, TTL 12.9h, was sound).

The comment above that code describes fixing this bug in the OTHER direction:
an earlier version called `expire()` on every increment, which under continuous
traffic refreshed the TTL forever so the key never rolled. The fix swapped one
TTL-dependent failure for another.

**So the fix here is not a better TTL rule.** The window is moved into the KEY:
an hourly-bucketed hash (`hincrby` per turn, one atomic op, same cost as the old
incr), summed over the last 24 buckets on read. A TTL can no longer corrupt the
figure because no TTL is consulted — a bucket outside the window is simply never
read again. Bounded by construction: stale fields are pruned, so the hash holds
at most a day or so of buckets.

Also pinned here: `autonomy_surface.audit_entries` is the LIFETIME total
(`chat_audit_log.get_stats()["total_entries"]`) and was rendered under the same
"(24h)" heading — which is why the page showed 1208 in both the 24h column and
the Chat Audit panel's "Total Entries". The value is genuine and useful; the
placement was the lie. The UI label now says so (asserted in
test/aria-brain-24h-labels-rf4068.test.mjs).
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest


class _FakeStore:
    """Just the primitives chat_audit_log touches."""

    def __init__(self):
        self.kv: dict[str, str] = {}
        self.hashes: dict[str, dict[str, str]] = {}
        self.lists: dict[str, list[str]] = {}
        self.deleted: list[str] = []

    async def get(self, key):
        return self.kv.get(key)

    async def set(self, key, value, ex=None, keepttl=False):
        self.kv[key] = value

    async def delete(self, key):
        self.deleted.append(key)
        return self.kv.pop(key, None) is not None

    async def expire(self, key, seconds):
        return key in self.kv

    async def lpush(self, key, value, *, critical=False):
        self.lists.setdefault(key, []).insert(0, value)

    async def ltrim(self, key, start, stop):
        pass

    async def llen(self, key):
        return len(self.lists.get(key, []))

    async def incr(self, key, amount=1, *, critical=False):
        cur = int(self.kv.get(key, "0") or "0") + amount
        self.kv[key] = str(cur)
        return cur

    async def hincrby(self, key, field, amount=1, *, critical=False):
        h = self.hashes.setdefault(key, {})
        cur = int(h.get(field, "0") or "0") + amount
        h[field] = str(cur)
        return cur

    async def hgetall(self, key):
        return dict(self.hashes.get(key, {}))

    async def hdel(self, key, field):
        return self.hashes.get(key, {}).pop(field, None) is not None


@pytest.fixture
def store(monkeypatch):
    from aria_service.intel import redis_store as rs
    s = _FakeStore()
    for name in ("get", "set", "delete", "expire", "lpush", "ltrim", "llen",
                 "incr", "hincrby", "hgetall", "hdel"):
        monkeypatch.setattr(rs, name, getattr(s, name))
    return s


def _hour_field(hours_ago: float) -> str:
    from aria_service.intel import chat_audit_log as cal
    dt = datetime.now(timezone.utc) - timedelta(hours=hours_ago)
    return cal._hour_field(dt)


# ── 1. the window is a property of the key, not of a TTL ───────────────────

@pytest.mark.asyncio
async def test_entries_24h_counts_only_the_last_24_hours(store):
    from aria_service.intel import chat_audit_log as cal

    store.hashes[cal._K_ENTRIES_HOURLY] = {
        _hour_field(0.1): "4",
        _hour_field(5): "6",
        _hour_field(23): "2",
        _hour_field(30): "700",    # outside the window
        _hour_field(240): "58",    # ten days ago
    }
    stats = await cal.get_stats()
    assert stats["entries_24h"] == 12, (
        f"buckets outside the window must not be summed: {stats}")


@pytest.mark.asyncio
async def test_a_ttl_less_legacy_counter_cannot_inflate_the_window(store):
    """The exact live poisoning: 758 with expires_at NULL."""
    from aria_service.intel import chat_audit_log as cal

    store.kv["crucix:chat_audit:entries_24h"] = "758"
    store.hashes[cal._K_ENTRIES_HOURLY] = {_hour_field(1): "10"}

    stats = await cal.get_stats()
    assert stats["entries_24h"] == 10, (
        "the orphaned lifetime counter must not be read any more; "
        f"got {stats['entries_24h']}")


@pytest.mark.asyncio
async def test_no_buckets_reads_as_zero_not_as_the_legacy_value(store):
    from aria_service.intel import chat_audit_log as cal

    store.kv["crucix:chat_audit:entries_24h"] = "758"
    stats = await cal.get_stats()
    assert stats["entries_24h"] == 0


# ── 2. the real write path drives it ───────────────────────────────────────

@pytest.mark.asyncio
async def test_record_increments_the_current_hour_bucket(store):
    """Capability test: drive `record_chat()`, the function the chat path calls."""
    from aria_service.intel import chat_audit_log as cal

    for _ in range(3):
        await cal.record_chat(
            session_id="s1",
            user_message="what is the OFSI position",
            response_text="An assessment.",
            mastery_overall=0.8,
        )

    buckets = store.hashes.get(cal._K_ENTRIES_HOURLY, {})
    assert buckets, "record_chat() did not write an hourly bucket"
    assert sum(int(v) for v in buckets.values()) == 3, buckets
    stats = await cal.get_stats()
    assert stats["entries_24h"] == 3
    assert stats["total_entries"] == 3


@pytest.mark.asyncio
async def test_record_retires_the_poisoned_legacy_key(store):
    """Leaving a 758-valued orphan in the store invites the next reader to
    'restore' it."""
    from aria_service.intel import chat_audit_log as cal

    cal._legacy_counter_retired = False
    store.kv["crucix:chat_audit:entries_24h"] = "758"
    await cal.record_chat(session_id="s1", user_message="q", response_text="a")
    assert "crucix:chat_audit:entries_24h" in store.deleted, store.deleted


# ── 3. bounded by construction ─────────────────────────────────────────────

@pytest.mark.asyncio
async def test_hourly_buckets_are_pruned(store):
    from aria_service.intel import chat_audit_log as cal

    store.hashes[cal._K_ENTRIES_HOURLY] = {
        _hour_field(h): "1" for h in range(0, 400, 3)
    }
    await cal.record_chat(session_id="s1", user_message="q", response_text="a")
    kept = store.hashes[cal._K_ENTRIES_HOURLY]
    assert len(kept) <= cal._HOURLY_BUCKETS_KEPT + 1, (
        f"bucket hash is unbounded: {len(kept)} fields")
    # The in-window buckets must survive the prune.
    assert _hour_field(0) in kept


# ── 4. the lifetime figure keeps its meaning ───────────────────────────────

@pytest.mark.asyncio
async def test_total_entries_remains_the_lifetime_count(store):
    """autonomy_surface.audit_entries reads this. It is lifetime BY DESIGN —
    the defect was rendering it under a 24h heading, not the value."""
    from aria_service.intel import chat_audit_log as cal

    store.lists[cal._K_LOG] = ["{}"] * 1208
    store.hashes[cal._K_ENTRIES_HOURLY] = {_hour_field(2): "10"}
    stats = await cal.get_stats()
    assert stats["total_entries"] == 1208
    assert stats["entries_24h"] == 10
    assert stats["total_entries"] != stats["entries_24h"], (
        "these two must never be the same number by construction again")


# ── 5. the wrapper this fix added was silently breaking a live capability ──

@pytest.mark.asyncio
async def test_resolve_operator_pending_actually_clears(store):
    """R-F4068 side effect, pinned deliberately.

    `redis_store.hdel` did not exist, and `dd_trigger_pipeline`'s
    `resolve_operator_pending()` calls it twice inside a bare
    `except Exception: return False`. So the AttributeError was swallowed, the
    function ALWAYS returned False, and an entity stuck in `operator_pending`
    could never be cleared. It was registered in `KNOWN_DEAD_CALLS` rather than
    fixed. Third instance of this family in that module after R-F2486 (hget)
    and R-F2625 (hincrby), both of which also failed open through a broad
    except.
    """
    from aria_service.intel import dd_trigger_pipeline as ddt

    store.hashes[ddt._OPERATOR_PENDING_KEY] = {"acme ltd": "{}"}
    store.hashes[ddt._TRIGGER_HISTORY_KEY] = {"acme ltd": "{}"}

    ok = await ddt.resolve_operator_pending("Acme Ltd")

    assert ok is True, "resolve_operator_pending still reports failure"
    assert "acme ltd" not in store.hashes[ddt._OPERATOR_PENDING_KEY]
    assert "acme ltd" not in store.hashes[ddt._TRIGGER_HISTORY_KEY]
