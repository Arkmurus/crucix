"""R-F4101 (C-153) — the freshness tracker skipped writes and told nobody.

R-F4097 stopped `record_refresh` from clobbering the tracker when the store
cannot be read: it now SKIPS the write. That is the right call for the data, but
as shipped it was **dark** under §21a — the skip reached a `logger.warning` at
most, and a log line is explicitly not wiring:

    "Logged to console / `except: pass` / local ring buffer / Telegram-only is
     DARK, not wired."

The failure mode that creates is worse than the one it replaced. A clobber is
loud in the numbers — 1,000 tracked domains drop to 8 and someone eventually
notices. A permanent skip is silent by construction: the tracker simply stops
updating, `stale_domains()` keeps returning whatever it last held, R-F90's
refresh orchestrator keeps thinking everything is fresh, and **the panel still
says `store_readable: true`** because reads can recover while writes are still
being skipped. A limb that stopped moving and reports nothing is exactly what
§25 calls a limb ARIA cannot feel.

**Once per process, not per call.** `record_refresh` fires on every ingested
fact, so a per-skip gap is the self-sustaining flood that already filled the
500-slot capability ledger (§17, `sanctions_coverage_degraded`, C-98). The
degraded state is announced once and re-armed on recovery, so a second outage
after a good period announces again rather than staying quiet forever.
"""
from __future__ import annotations

import pytest

from aria_service.intel import learning_progress as lp
from aria_service.intel.redis_store import StoreReadError


class _Store:
    def __init__(self, mode="wedged"):
        self.mode = mode
        self.writes: list = []

    async def get_strict(self, key):
        if self.mode == "wedged":
            raise StoreReadError("store not ready")
        return None

    async def set_json(self, key, obj, ex=None):
        self.writes.append(obj)


@pytest.fixture
def wired(monkeypatch):
    """Capture brain signals instead of emitting them."""
    seen: list = []

    def _capture(**kw):
        seen.append(kw)

    monkeypatch.setattr(lp, "wire_failure", _capture)
    lp._reset_skip_announcement()          # per-process latch, reset per test
    return seen


@pytest.fixture
def store(monkeypatch):
    s = _Store()

    async def _fake_redis():
        return s

    monkeypatch.setattr(lp, "_redis", _fake_redis)
    return s


@pytest.mark.asyncio
async def test_a_skipped_write_reaches_the_brain(wired, store):
    await lp.record_refresh("topic")
    assert store.writes == [], "precondition: the write must have been skipped"
    assert len(wired) == 1, (
        "a skipped write is a silent degradation and must reach the brain "
        "(§21a): success AND failure, never a log line alone")
    assert "freshness" in str(wired[0]).lower()


@pytest.mark.asyncio
async def test_it_announces_once_per_process_not_once_per_call(wired, store):
    for _ in range(50):
        await lp.record_refresh("topic")
    assert len(wired) == 1, (
        f"emitted {len(wired)} signals for 50 skips — record_refresh runs on "
        "every ingested fact, so a per-skip gap is the flood that filled the "
        "500-slot capability ledger")


@pytest.mark.asyncio
async def test_recovery_re_arms_the_announcement(wired, store):
    """A second outage after a good period must announce again. A latch that
    only ever fires once turns into 'we told you in March'."""
    await lp.record_refresh("topic")
    assert len(wired) == 1

    store.mode = "ok"                      # store recovers, write succeeds
    await lp.record_refresh("topic")
    assert store.writes, "the healthy path must still write"

    store.mode = "wedged"                  # and it breaks again
    await lp.record_refresh("topic")
    assert len(wired) == 2, "the second outage must be announced, not swallowed"


@pytest.mark.asyncio
async def test_the_healthy_path_never_announces(wired, store):
    store.mode = "ok"
    await lp.record_refresh("topic")
    assert wired == [], "a working store must not emit a degradation signal"
