"""R-F2854 — a failed state read must never reset the monthly spend rollup.

Capability test (CLAUDE.md §3c): the broken path is the real cost-recording
update, and the user-visible symptom is the §17 $300/mo cap silently regaining
headroom because the accumulated total was wiped.

Pre-fix, every rollup update was::

    roll = await rs.get_json(key) or _new_rollup(...)

``get_json`` swallows a store-layer ``StoreReadError`` to ``None``
(redis_store:299-303), and ``None`` is indistinguishable from "no spend this
month" — so a state_store timeout produced a ZERO rollup, merged the current
record into it, and wrote it back.

Live evidence that the trigger occurs (2026-07-22, aria-intel)::

    state_store.get(crucix:aria:student:mastery) timed out after 5s

Consequences the tests below pin:
  * the durable month total must not be overwritten with a reset value
  * ``_month_cache`` must not be refreshed to the reset value (the cap and the
    operator-facing /cost/monthly/status read it)
"""

from __future__ import annotations

import time

import pytest

from aria_service.intel import cost_tracker as ct
from aria_service.intel.redis_store import StoreReadError


ACCUMULATED_TOTAL = 187.42


def _existing_rollup(month: str) -> dict:
    roll = ct._new_rollup(month, time.time())
    roll["total_cost_usd"] = ACCUMULATED_TOTAL
    roll["total_calls"] = 4210
    roll["total_tokens"] = 9_000_000
    return roll


def _record(month_ts: float) -> dict:
    return {
        "id": "call-1",
        "ts": month_ts,
        "provider": "deepseek",
        "feature": "chat",
        "model": "deepseek-chat",
        "cost_usd": 0.01,
        "total_tokens": 100,
    }


class _Store:
    def __init__(self, *, read_raises: bool, data=None):
        self.read_raises = read_raises
        self.data = data
        self.writes: list[tuple[str, dict]] = []

    async def get_json_strict(self, key):
        if self.read_raises:
            raise StoreReadError(f"state_store.get({key}) timed out after 5s")
        return self.data

    async def get_json(self, key):
        if self.read_raises:
            return None  # the pre-fix non-strict contract
        return self.data

    async def set_json(self, key, obj, ex=None, keepttl=False):
        self.writes.append((key, obj))


@pytest.fixture(autouse=True)
def _reset_month_cache():
    before = dict(ct._month_cache)
    yield
    ct._month_cache.clear()
    ct._month_cache.update(before)


# ── Capability test: the month total must survive a store read failure ──────

@pytest.mark.asyncio
async def test_failed_read_does_not_reset_month_total(monkeypatch):
    month = ct._current_month_key()
    store = _Store(read_raises=True)
    monkeypatch.setattr(ct, "rs", store)
    ct._month_cache["month"] = month
    ct._month_cache["total"] = ACCUMULATED_TOTAL

    await ct._update_month_rollup(_record(time.time()))

    assert store.writes == [], (
        f"MONTHLY SPEND RESET: wrote {store.writes!r} from a failed read"
    )
    assert ct._month_cache["total"] == ACCUMULATED_TOTAL, (
        "in-process cap cache was refreshed to a reset total"
    )


@pytest.mark.asyncio
async def test_prefix_pattern_would_have_reset_the_total(monkeypatch):
    """Negative control — proves the pre-fix expression really did wipe the month."""
    month = ct._current_month_key()
    store = _Store(read_raises=True)

    # The exact pre-fix expression.
    roll = await store.get_json(f"{ct.COST_MONTH_PREFIX}{month}") or ct._new_rollup(
        month, time.time()
    )
    ct._merge_record_into_rollup(roll, _record(time.time()))

    assert roll["total_cost_usd"] < 1.0, (
        "pre-fix reset no longer reproduces — re-check the chain"
    )
    assert roll["total_cost_usd"] != pytest.approx(ACCUMULATED_TOTAL)


# ── Healthy-store behaviour must be unchanged ──────────────────────────────

@pytest.mark.asyncio
async def test_healthy_read_accumulates_onto_existing_total(monkeypatch):
    month = ct._current_month_key()
    store = _Store(read_raises=False, data=_existing_rollup(month))
    monkeypatch.setattr(ct, "rs", store)

    await ct._update_month_rollup(_record(time.time()))

    assert len(store.writes) == 1, "healthy path must still persist the rollup"
    _key, written = store.writes[0]
    assert written["total_cost_usd"] > ACCUMULATED_TOTAL, (
        "new spend must accumulate onto the existing total, not replace it"
    )
    assert written["total_calls"] == 4211


@pytest.mark.asyncio
async def test_genuinely_absent_month_starts_a_fresh_rollup(monkeypatch):
    """Absent is not failed: a brand-new month must still start at zero."""
    store = _Store(read_raises=False, data=None)
    monkeypatch.setattr(ct, "rs", store)

    await ct._update_month_rollup(_record(time.time()))

    assert len(store.writes) == 1, "a new month must be created"
    _key, written = store.writes[0]
    assert written["total_calls"] == 1


@pytest.mark.asyncio
async def test_loader_returns_none_only_on_store_failure(monkeypatch):
    month = ct._current_month_key()
    key = f"{ct.COST_MONTH_PREFIX}{month}"

    failing = _Store(read_raises=True)
    monkeypatch.setattr(ct, "rs", failing)
    assert await ct._load_rollup_for_update(key, month, time.time()) is None

    absent = _Store(read_raises=False, data=None)
    monkeypatch.setattr(ct, "rs", absent)
    fresh = await ct._load_rollup_for_update(key, month, time.time())
    assert fresh is not None and fresh["total_cost_usd"] == 0.0

    healthy = _Store(read_raises=False, data=_existing_rollup(month))
    monkeypatch.setattr(ct, "rs", healthy)
    existing = await ct._load_rollup_for_update(key, month, time.time())
    assert existing["total_cost_usd"] == ACCUMULATED_TOTAL
