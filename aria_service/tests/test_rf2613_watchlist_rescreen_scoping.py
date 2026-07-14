"""R-F2613 — manual watchlist re-screen must be OWNER-SCOPED (was global cross-tenant).
Drives the real rescreen_watchlist against the in-memory store."""
import asyncio
from aria_service.intel import dd_orchestrator as dd
from aria_service.intel import redis_store as rs


def test_rf2613_scoped_rescreen_uses_owner_list_not_global(monkeypatch):
    async def run():
        # Global watchlist has TWO other-tenant entries.
        await rs.set_json(dd.WATCHLIST_KEY, [
            {"name": "Acme Defence GmbH", "user_id": "userY"},
            {"name": "Beta Corp Ltd", "user_id": "userZ"},
        ])
        # userX owns NOTHING → get_watchlist returns [] for them (R-F2355 scoping).
        async def fake_gw(uid=None, dom=None):
            return [] if uid == "userX" else [{"name": "Acme Defence GmbH"}]
        monkeypatch.setattr(dd, "get_watchlist", fake_gw)
        r = await dd.rescreen_watchlist(user_id="userX", user_email_domain="x.com")
        wl_after = await rs.get_json(dd.WATCHLIST_KEY) or []
        return r, wl_after
    r, wl = asyncio.run(run())
    # scoped to userX's (empty) list, NOT the global 2 → nothing screened
    assert r["entities_screened"] == 0, r
    # and the GLOBAL watchlist is UNTOUCHED (no scoped-subset overwrite / data loss)
    assert len(wl) == 2, wl


def test_rf2613_global_path_still_reads_global(monkeypatch):
    # user_id=None (daily loop) must read the global key, not get_watchlist.
    async def run():
        await rs.set_json(dd.WATCHLIST_KEY, [])   # empty global → fast early return
        called = {"gw": False}
        async def fake_gw(uid=None, dom=None):
            called["gw"] = True; return []
        monkeypatch.setattr(dd, "get_watchlist", fake_gw)
        r = await dd.rescreen_watchlist(user_id=None)
        return r, called
    r, called = asyncio.run(run())
    assert r["entities_screened"] == 0
    assert called["gw"] is False   # global path did NOT call get_watchlist
