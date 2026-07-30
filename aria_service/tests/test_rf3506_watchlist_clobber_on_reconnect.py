"""R-F3506 — a store reconnect could wipe every tenant's watchlist.

Observed live 2026-07-30 while building the orphan reconcile. A strict read
against aria-intel returned:

    StoreReadError: state_store: no connection (reconnect in progress)

while the NON-strict read of the same key returned ``[]`` and ``list_reports()``
returned 25. The store was mid-reconnect, not empty.

Every watchlist mutation in dd_orchestrator is a read-modify-write built on the
non-strict read:

    current = await rs.get_json(WATCHLIST_KEY) or []     # <- swallows the error
    ...                                                   #    and yields []
    await rs.set_json(WATCHLIST_KEY, <derived from current>)

There are SEVEN such write-backs (:16079, :16115, :16158, :16387, :16464,
:17909, :18269), all fed by that pattern. So a mutation that lands inside a
reconnect window reads an empty list, derives an empty list, and PERSISTS it —
destroying every tenant's watchlist entries, permanently, with no error and no
signal.

This is the class memory/nonstrict_read_clobber_defect_class records: "get_json()
swallows StoreReadError -> None -> writeback wipes durable state. Fix =
get_json_strict." It is the same mechanism R-F2664 fixed for regional mastery,
where a slow-boot read poisoned the cache and the next update clobbered the
durable key.

The window is real and recurring, not theoretical: the R-F2277 liveness watchdog
is armed with reconnect_after=45s and a 180s ceiling, and a reconnect was
observed in production today.

R-F3500 made this MORE reachable by adding a second caller — delete_report now
cascades into remove_from_watchlist — so a DD deletion during a reconnect could
wipe the whole watchlist.

The fix is the R-F2664 shape: read STRICTLY, and on an unreadable store SKIP the
mutation entirely. A lost mutation is recoverable (the user retries); a clobbered
watchlist is not.
"""
from __future__ import annotations

import pytest

from aria_service.intel import dd_orchestrator


class _ReconnectingStore:
    """Reads raise as they do mid-reconnect; writes are recorded."""

    def __init__(self, entries):
        self.entries = list(entries)
        self.writes: list = []
        self.readable = True

    async def get_json(self, _key):
        # The non-strict contract: swallow and return None.
        if not self.readable:
            return None
        return list(self.entries)

    async def get_json_strict(self, _key):
        if not self.readable:
            from aria_service.intel.redis_store import StoreReadError
            raise StoreReadError(
                "state_store: no connection (reconnect in progress) reading "
                "crucix:dd:watchlist")
        return list(self.entries)

    async def set_json(self, _key, value, **_kw):
        self.writes.append(list(value))
        self.entries = list(value)
        return True


@pytest.fixture
def store(monkeypatch):
    def _install(entries, readable=True):
        fake = _ReconnectingStore(entries)
        fake.readable = readable
        import aria_service.intel.redis_store as rs
        monkeypatch.setattr(rs, "get_json", fake.get_json)
        monkeypatch.setattr(rs, "get_json_strict", fake.get_json_strict)
        monkeypatch.setattr(rs, "set_json", fake.set_json)
        return fake
    return _install


_TENANTS = [
    {"name": "Acme Ltd", "user_id": "u1"},
    {"name": "Beta GmbH", "user_id": "u2"},
    {"name": "Gamma SA", "user_id": "u3"},
]


class TestAReconnectMustNotWipeTheWatchlist:

    @pytest.mark.asyncio
    async def test_removal_during_a_reconnect_does_not_persist_an_empty_list(
            self, store):
        """The catastrophic case: one user's removal erases everyone."""
        fake = store(_TENANTS, readable=False)
        await dd_orchestrator.remove_from_watchlist("Acme Ltd", user_id="u1")
        assert fake.writes == [], (
            f"a mutation during a store reconnect PERSISTED {fake.writes!r} — "
            f"this wipes every tenant's watchlist"
        )

    @pytest.mark.asyncio
    async def test_removal_during_a_reconnect_reports_failure(self, store):
        """It must not look like a successful removal either (R-F3503)."""
        store(_TENANTS, readable=False)
        out = await dd_orchestrator.remove_from_watchlist("Acme Ltd", user_id="u1")
        assert out.get("ok") is False
        assert out.get("removed") == 0
        assert "unavailable" in str(out.get("reason", "")).lower() or \
               "could not" in str(out.get("reason", "")).lower(), out

    @pytest.mark.asyncio
    async def test_add_during_a_reconnect_does_not_persist(self, store):
        fake = store(_TENANTS, readable=False)
        try:
            await dd_orchestrator.add_to_watchlist({"name": "Delta Inc"})
        except Exception:
            pass
        assert fake.writes == [], (
            f"an add during a reconnect wrote {fake.writes!r}, discarding the "
            f"existing entries it could not read"
        )

    @pytest.mark.asyncio
    async def test_the_cascade_from_delete_report_is_also_safe(self, store):
        """R-F3500 added this caller, so it must not become a wipe vector."""
        fake = store(_TENANTS, readable=False)
        await dd_orchestrator._unwatch_deleted_subject(
            {"subject": "Acme Ltd", "user_id": "u1"})
        assert fake.writes == [], (
            f"the delete-report cascade wiped the watchlist during a reconnect: "
            f"{fake.writes!r}"
        )


class TestNormalOperationIsUnchanged:

    @pytest.mark.asyncio
    async def test_removal_still_works_on_a_healthy_store(self, store):
        fake = store(_TENANTS, readable=True)
        out = await dd_orchestrator.remove_from_watchlist("Acme Ltd", user_id="u1")
        assert out["ok"] is True and out["removed"] == 1
        assert fake.writes, "a healthy removal wrote nothing"
        names = {e["name"] for e in fake.writes[-1]}
        assert "Acme Ltd" not in names
        assert {"Beta GmbH", "Gamma SA"} <= names, (
            "the other tenants' entries were not preserved"
        )

    @pytest.mark.asyncio
    async def test_a_refused_removal_still_writes_nothing_new(self, store):
        fake = store(_TENANTS, readable=True)
        out = await dd_orchestrator.remove_from_watchlist(
            "Beta GmbH", user_id="u1")   # not the owner
        assert out["ok"] is False
        if fake.writes:
            assert {e["name"] for e in fake.writes[-1]} == {
                "Acme Ltd", "Beta GmbH", "Gamma SA"}, (
                "a refused removal altered the stored list"
            )
