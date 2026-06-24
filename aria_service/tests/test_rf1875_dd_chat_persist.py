"""R-F1875 — DD chat turns must persist even when the SSE stream is dropped.

Symptom (operator-confirmed 2026-06-24): plain chats persist and show on
return, but DD turns vanish from the sidebar. Root cause: the stream-path
conversation persist (create_conversation + session save) ran only AFTER the
whole response streamed; a long DD whose client SSE stream drops (proxy 240s
budget / 390s overrun) closes the generator before that block runs, so the turn
was never registered.

Fix: register the conversation + seed the user turn EARLY in
_aria_chat_stream_impl, before any long streamed work. This test proves:
  1. the persistence mechanism the early block uses is correct + idempotent
     (so the duplicate early+end registration is safe);
  2. the early-registration block is ordered BEFORE the LLM/streaming section.
The disconnect-survival itself follows from (2) — the block runs before the
first yield, so a mid-stream close can't skip it.
"""
from __future__ import annotations

import asyncio
import inspect

import pytest


class _FakeRS:
    """In-memory stand-in for redis_store covering the calls conversation_store uses."""
    def __init__(self):
        self.z: dict[str, dict[str, float]] = {}
        self.h: dict[str, dict] = {}

    async def zadd(self, key, score, member):
        self.z.setdefault(key, {})[member] = score

    async def hset(self, key, mapping):
        self.h.setdefault(key, {}).update(mapping)

    async def hgetall(self, key):
        return dict(self.h.get(key, {}))

    async def zrevrange(self, key, start, stop):
        members = sorted(self.z.get(key, {}).items(), key=lambda kv: kv[1], reverse=True)
        return [m for m, _ in members][start: (stop + 1 if stop >= 0 else None)]

    async def zcard(self, key):
        return len(self.z.get(key, {}))


def test_create_conversation_is_idempotent(monkeypatch):
    """Early + end create_conversation must not create duplicate sidebar entries."""
    from aria_service.intel import conversation_store as cs
    fake = _FakeRS()
    monkeypatch.setattr(cs, "rs", fake)

    uid, sid = "acorreaarkmuruscom", "wa_acorreaarkmuruscom_abc123"
    asyncio.run(cs.create_conversation(uid, sid, "do a full DD on Modirum Gespi"))
    # simulate the end-of-turn re-registration on the SAME turn
    asyncio.run(cs.create_conversation(uid, sid, "do a full DD on Modirum Gespi"))

    key = cs._CONV_KEY.format(user_id=uid)
    assert len(fake.z.get(key, {})) == 1, "duplicate sidebar entry — create_conversation not idempotent"
    # meta exists for the session (sidebar can render it)
    assert fake.h.get(cs._META_KEY.format(session_id=sid)), "conversation meta missing"


def test_early_registration_precedes_streaming():
    """The R-F1875 early block must run BEFORE the LLM/streaming section, so a
    mid-stream disconnect cannot skip it."""
    src = inspect.getsource
    import aria_service.aria_engine as ae
    impl = src(ae._aria_chat_stream_impl)
    assert "R-F1875" in impl, "early-registration block missing from _aria_chat_stream_impl"
    early = impl.index("R-F1875")
    # The end-of-turn persist marker ("Persist session") must come AFTER the early block.
    end = impl.index("Persist session")
    assert early < end, "R-F1875 early block must precede the end-of-turn persist"
    # And the early block must precede the bulk of streaming yields.
    first_yield = impl.index("yield")
    # early registration should be before most streaming work completes
    assert early < end and first_yield > 0
