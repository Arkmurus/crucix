"""R-F3070 — a turn answered by a short-circuit path must still reach the sidebar.

BROKEN PATH (reproduced live 2026-07-25 against aria-intel):

  1. POST /api/aria/chat/stream {"message":"hello there", auto_tools:true}
     → answered by the fast lane → `GET /api/aria/conversations` did NOT list
       the session at all. The user's chat was simply GONE on refresh.
  2. POST .../chat/stream {"message":"hi"} → answered by the trivial
     short-circuit → nothing persisted anywhere: not the session history (so
     ARIA had no memory of the exchange) and not the conversation index.
  3. Open with a short-circuit turn, then ask a real question → the full
     pipeline saw history >= 2, so it took touch_conversation's
     create-on-missing branch, which passed first_message="" → the conversation
     was titled "New conversation" FOREVER.

These tests drive the same entry points the endpoints call, and assert the
user-visible outcome (what the chat sidebar lists), not a helper's return value.
"""
import asyncio

import pytest

from aria_service.aria_engine import fast_lane_chat, persist_trivial_turn
from aria_service.intel import conversation_store


class _StubResult:
    def __init__(self, text):
        self.text = text


class _StubLLM:
    def __init__(self, text="Hello there! How can I help you today?"):
        self._text = text

    async def complete(self, system, user, *, max_tokens=600, timeout=30.0):
        return _StubResult(self._text)


async def _titles_for(user_id):
    convos = await conversation_store.list_conversations(user_id, limit=50)
    return {c["session_id"]: c["title"] for c in convos}


def test_fast_lane_turn_appears_in_the_sidebar_with_a_real_title():
    """Pre-R-F3070 this session never reached conversation_store at all."""
    uid = "rf3070user"
    sid = "rf3070_fastlane_sess"

    async def run():
        out = await fast_lane_chat("hello there", sid, _StubLLM(), user_id=uid)
        assert out, "fast lane must still answer"

        titles = await _titles_for(uid)
        assert sid in titles, (
            "the conversation must be listed — pre-fix a fast-lane-only session "
            "was invisible in the sidebar and the user lost the chat on refresh"
        )
        assert titles[sid] == "hello there", (
            f"title must come from the first message, got {titles[sid]!r}"
        )

    asyncio.run(run())


def test_trivial_turn_is_persisted_and_indexed():
    """Pre-R-F3070 trivial_reply returned without touching session OR index."""
    uid = "rf3070user2"
    sid = "rf3070_trivial_sess"
    reply = "Hi — ARIA here. Ask me anything about compliance."

    async def run():
        await persist_trivial_turn("hi", sid, reply, user_id=uid)

        # (a) the exchange is in the session history — ARIA remembers it next turn
        from aria_service.aria_engine import _get_session
        msgs = (await _get_session(sid)).get("messages") or []
        assert any(m.get("role") == "user" and m.get("content") == "hi" for m in msgs), \
            "the user's message must be in history"
        assert any(m.get("role") == "aria" and m.get("content") == reply for m in msgs), \
            "ARIA's reply must be in history"

        # (b) and it is on the sidebar
        titles = await _titles_for(uid)
        assert titles.get(sid) == "hi"

    asyncio.run(run())


def test_shortcircuit_open_then_full_pipeline_keeps_the_real_title():
    """The 'New conversation' bug: a session OPENED by a short-circuit turn.

    The full pipeline then calls touch_conversation with history already >= 2.
    Pre-R-F3070 that branch created the conversation with first_message="" and
    the title was stuck at "New conversation" for the life of the session.
    """
    uid = "rf3070user3"
    sid = "rf3070_mixed_sess"

    async def run():
        # Turn 1 — short-circuit opens the session.
        await fast_lane_chat("hey", sid, _StubLLM(), user_id=uid)
        # Turn 2 — the full pipeline's end-of-turn registration.
        await conversation_store.touch_conversation(sid, uid, first_message="hey")

        titles = await _titles_for(uid)
        assert titles.get(sid) == "hey", (
            f"expected the first message as the title, got {titles.get(sid)!r} — "
            "'New conversation' here means the create-on-missing branch is still "
            "being handed an empty first message"
        )

    asyncio.run(run())


def test_touch_conversation_titles_from_first_message_when_it_creates():
    """The root defect, isolated: touch_conversation's create branch."""
    uid = "rf3070user4"
    sid = "rf3070_touch_creates"

    async def run():
        await conversation_store.touch_conversation(
            sid, uid, first_message="what are the UK export control rules")
        titles = await _titles_for(uid)
        assert titles.get(sid) == "what are the UK export control rules"

    asyncio.run(run())


def test_anonymous_turn_is_not_indexed():
    """No owner → nothing to index. Must not raise, must not invent a bucket."""
    sid = "rf3070_anon_sess"

    async def run():
        out = await fast_lane_chat("hello there", sid, _StubLLM(), user_id="anon")
        assert out, "an anonymous caller still gets an answer"
        assert await _titles_for("anon") == {}, "must not create an 'anon' sidebar"

    asyncio.run(run())


def test_registration_failure_never_breaks_the_reply(monkeypatch):
    """A conversation-index outage must not fail a reply that already succeeded."""
    async def _boom(*a, **k):
        raise RuntimeError("store down")

    monkeypatch.setattr(conversation_store, "touch_conversation", _boom)

    async def run():
        out = await fast_lane_chat("hello there", "rf3070_failsafe", _StubLLM(),
                                   user_id="rf3070user5")
        assert out and "Hello there" in out, \
            "the answer must survive a conversation-store failure"

    asyncio.run(run())
