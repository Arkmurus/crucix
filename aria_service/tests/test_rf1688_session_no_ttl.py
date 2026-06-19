"""R-F1688 — chat history is permanent (no SESSION_TTL).

Pre-fix `_save_session` wrote each session with ex=30 days, so a returning
user's conversations older than 30 days opened blank. This drives the actual
broken function (_save_session) and asserts the user-visible contract: the
session is persisted with NO expiry.
"""
import asyncio

from aria_service import aria_engine


def test_save_session_passes_no_ttl(monkeypatch):
    captured = {}

    async def _fake_set_json(key, obj, ex=None, keepttl=False):
        captured["key"] = key
        captured["ex"] = ex

    monkeypatch.setattr(aria_engine.rs, "set_json", _fake_set_json)
    asyncio.run(aria_engine._save_session("rf1688_sid", {"messages": [{"role": "user", "content": "hi"}]}))

    assert captured["key"] == "crucix:aria:session:rf1688_sid"
    # The contract: sessions are saved with NO TTL → expires_at NULL → never swept.
    assert captured["ex"] is None, "chat sessions must persist forever (no TTL)"


def test_session_ttl_constant_is_none():
    assert aria_engine.SESSION_TTL is None
