"""R-F1813 — C1: cross-tenant chat disclosure fixed (conversations export/search + admin/brain).

Authorization review finding C1 (CRITICAL): /api/aria/conversations/export read
crucix:aria:session:{id} with NO ownership check; /conversations/search scanned ALL
users' sessions; /admin/brain/{id} exposed any session's brain state. Any token holder
could read another user's chats by id.

Fix: each endpoint now requires user_id and delegates ownership to
conversation_store.get_conversation (meta.userId == user_id); Node pins user_id from
the JWT and strips client-supplied values.

Capability test drives the REAL endpoint handlers and asserts: missing user_id → 400,
cross-user (ownership mismatch) → 404, owner → data. fail_wire re-raises HTTPException
(verified engine_wiring.py:282), so the rejections reach the client.
"""
import pytest
from fastapi import HTTPException

from aria_service.routes import aria as A

OWNER = "alice"


@pytest.fixture
def store(monkeypatch):
    import aria_service.intel.conversation_store as cs

    async def _get(session_id, user_id=None):
        # ownership: only the owner (or trusted None) gets the convo
        if session_id == "s1" and (user_id is None or user_id == OWNER):
            return {"session_id": "s1", "createdAt": 1.0,
                    "messages": [{"role": "user", "content": "secret hello"}]}
        return None

    async def _list(user_id, offset=0, limit=30):
        return [{"session_id": "s1"}] if user_id == OWNER else []

    monkeypatch.setattr(cs, "get_conversation", _get)
    monkeypatch.setattr(cs, "list_conversations", _list)
    return cs


@pytest.mark.asyncio
async def test_export_requires_user_id(store):
    with pytest.raises(HTTPException) as e:
        await A.export_conversation(session_id="s1", user_id="")
    assert e.value.status_code == 400


@pytest.mark.asyncio
async def test_export_blocks_cross_user(store):
    with pytest.raises(HTTPException) as e:
        await A.export_conversation(session_id="s1", user_id="bob")  # not the owner
    assert e.value.status_code == 404  # ownership mismatch → not found (no existence leak)


@pytest.mark.asyncio
async def test_export_owner_gets_data(store):
    out = await A.export_conversation(session_id="s1", user_id=OWNER)
    assert any("secret hello" in m["content"] for m in out["messages"])


@pytest.mark.asyncio
async def test_search_requires_user_id(store):
    with pytest.raises(HTTPException) as e:
        await A.search_conversations(q="secret", user_id="")
    assert e.value.status_code == 400


@pytest.mark.asyncio
async def test_search_scoped_to_caller(store):
    # bob has no conversations of his own → no cross-tenant results
    rb = await A.search_conversations(q="secret", limit=50, user_id="bob")
    assert rb["results"] == [] and rb["total"] == 0
    # alice finds her own
    ra = await A.search_conversations(q="secret", limit=50, user_id=OWNER)
    assert ra["total"] >= 1


@pytest.mark.asyncio
async def test_admin_brain_blocks_cross_user_and_missing(store):
    with pytest.raises(HTTPException) as e:
        await A.admin_brain_ep(session_id="s1", user_id="bob")
    assert e.value.status_code == 404
    with pytest.raises(HTTPException) as e2:
        await A.admin_brain_ep(session_id="s1", user_id="")
    assert e2.value.status_code == 400
