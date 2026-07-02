"""R-F2316 — deep-analysis (/think) turns must PERSIST so they survive a refresh.

Before this, chat (/chat/stream) persisted via _save_session + conversation_store
but /think did not — a "Deep analysis" turn vanished on reload (and the sidebar
showed a false client-only row). think_ep now persists when the proxy supplies a
session_id + a server-pinned user_id. Fully additive: no-op without them.
"""
import pytest

from aria_service.routes import aria as aria_routes
from aria_service.intel import conversation_store


def test_think_result_to_history_text_structured():
    txt = aria_routes._think_result_to_history_text({
        "orientation": "O",
        "conclusion": {"statement": "C", "confidence": 90,
                       "epistemic_status": "CONFIRMED", "action": {"what": "Do X"}},
        "reasoning": "R",
        "metacognition": {"biggest_gap": "G"},
    })
    assert "## Conclusion\nC" in txt
    assert "## Reasoning\nR" in txt
    assert "Do X" in txt and "90%" in txt and "CONFIRMED" in txt


def test_think_result_to_history_text_fallback():
    assert aria_routes._think_result_to_history_text({"full_text": "FT"}) == "FT"
    assert aria_routes._think_result_to_history_text({"response": "RE"}) == "RE"
    assert aria_routes._think_result_to_history_text("plain") == "plain"


@pytest.mark.asyncio
async def test_think_ep_persists_turn(monkeypatch):
    async def fake_think(q, ctx, llm, intel):
        return {"conclusion": {"statement": "Test conclusion", "confidence": 80}, "reasoning": "Because."}
    monkeypatch.setattr(aria_routes, "aria_think", fake_think)
    monkeypatch.setattr(aria_routes, "get_llm", lambda request: None)
    monkeypatch.setattr(aria_routes, "get_intel_data", lambda request: None)

    sid, uid = "test_rf2316_think_sess", "test_rf2316_user_slug"
    await conversation_store.delete_conversation(uid, sid)  # clean slate
    req = aria_routes.ThinkRequest(question="Analyse Modirum", session_id=sid, user_id=uid)
    result = await aria_routes.think_ep(req, None)  # request unused (get_* patched)
    assert result["conclusion"]["statement"] == "Test conclusion"

    convo = await conversation_store.get_conversation(sid, user_id=uid)
    assert convo is not None, "conversation not persisted"
    msgs = convo.get("messages") or []
    assert any(m.get("role") == "user" and "Analyse Modirum" in (m.get("content") or "") for m in msgs)
    assert any(m.get("role") == "aria" and "Test conclusion" in (m.get("content") or "") for m in msgs)
    # and the conversation is indexed for the user (survives refresh → sidebar reload)
    convos = await conversation_store.list_conversations(uid, offset=0, limit=50)
    assert any((c.get("session_id") or c.get("sessionId")) == sid for c in (convos or [])), convos
    await conversation_store.delete_conversation(uid, sid)


@pytest.mark.asyncio
async def test_think_ep_no_session_is_noop(monkeypatch):
    async def fake_think(q, ctx, llm, intel):
        return {"full_text": "answer"}
    monkeypatch.setattr(aria_routes, "aria_think", fake_think)
    monkeypatch.setattr(aria_routes, "get_llm", lambda request: None)
    monkeypatch.setattr(aria_routes, "get_intel_data", lambda request: None)
    req = aria_routes.ThinkRequest(question="Q")  # no session_id/user_id
    result = await aria_routes.think_ep(req, None)
    assert result["full_text"] == "answer"  # returns fine, no crash, no persistence
