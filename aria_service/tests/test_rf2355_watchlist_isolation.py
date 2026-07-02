"""R-F2355 — the watchlist must be PER-USER (R-F2097). Before this it was one global list,
so another user's DD'd company (e.g. Arthur's) leaked onto every other user's watchlist
while the DD *reports* were correctly per-user. get_watchlist now filters like list_reports.
"""
import pytest

from aria_service.intel import dd_orchestrator as ddo


@pytest.fixture
def fake_rs(monkeypatch):
    store: dict = {}
    import aria_service.intel.redis_store as rs

    async def set_json(k, v, ex=None, keepttl=False):
        store[k] = v
        return True

    async def get_json(k):
        return store.get(k)

    monkeypatch.setattr(rs, "set_json", set_json)
    monkeypatch.setattr(rs, "get_json", get_json)
    return store


@pytest.mark.asyncio
async def test_watchlist_is_per_user(fake_rs):
    fake_rs[ddo.WATCHLIST_KEY] = [
        {"name": "Arthur Co", "user_id": "arthur", "user_email_domain": "arthur.com", "share_to_company": True},
        {"name": "My Co", "user_id": "op", "user_email_domain": "op.com", "share_to_company": True},
        {"name": "Colleague Co", "user_id": "op2", "user_email_domain": "op.com", "share_to_company": True},
        {"name": "Private Colleague Co", "user_id": "op3", "user_email_domain": "op.com", "share_to_company": False},
        {"name": "Legacy No Owner"},   # pre-R-F2355 entry, no owner
    ]
    names = {w["name"] for w in await ddo.get_watchlist(user_id="op", user_email_domain="op.com")}
    assert "My Co" in names                      # own entry
    assert "Colleague Co" in names               # same-company shared
    assert "Arthur Co" not in names              # THE FIX: another tenant no longer leaks
    assert "Private Colleague Co" not in names   # share_to_company=False respected
    assert "Legacy No Owner" not in names        # no-owner hidden from user view (fail closed)


@pytest.mark.asyncio
async def test_watchlist_admin_internal_sees_all(fake_rs):
    # The daily re-screen loop calls get_watchlist() with no user → must see EVERY entry so
    # monitoring still covers all watched entities across tenants.
    fake_rs[ddo.WATCHLIST_KEY] = [
        {"name": "A", "user_id": "arthur"}, {"name": "B", "user_id": "op"}, {"name": "C"},
    ]
    names = {w["name"] for w in await ddo.get_watchlist()}
    assert names == {"A", "B", "C"}
