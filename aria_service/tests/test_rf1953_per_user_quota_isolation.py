"""R-F1953 — per-user quota isolation (capability test).

Before R-F1953 the chat handler keyed the per-user quota on
`session_id.split("_", 1)[0]`, which collapsed EVERY WhatsApp sender into one
shared "wa" bucket (Telegram → "telegram", WA auto → "auto"). So one chatty
team member exhausting the 30 RPM / $5-day budget denied service to every other
WhatsApp user — the opposite of "scope her work to each and every user".

These tests drive the real key-derivation (`_quota_user_key`) and the real
`user_quota` enforcement, and assert the USER-VISIBLE property: two different
WhatsApp senders are independent — saturating sender A's rate cap leaves sender
B fully served.
"""
import asyncio

import pytest

from aria_service.routes.aria import ChatRequest, _quota_user_key
from aria_service.intel import user_quota


def test_whatsapp_senders_get_distinct_buckets():
    # Two different WhatsApp senders (no JWT user_id; per-sender id in session_id).
    a = ChatRequest(message="hi", session_id="wa_351911111111swhatsappnet")
    b = ChatRequest(message="hi", session_id="wa_351922222222swhatsappnet")
    ka, kb = _quota_user_key(a), _quota_user_key(b)
    assert ka != kb, "distinct WhatsApp senders must map to distinct quota buckets"
    # And neither collapses to the bare channel prefix (the old bug).
    assert ka != "wa" and kb != "wa"


def test_telegram_and_auto_channels_not_collapsed():
    t1 = ChatRequest(message="x", session_id="telegram_111")
    t2 = ChatRequest(message="x", session_id="telegram_222")
    assert _quota_user_key(t1) != _quota_user_key(t2)
    a1 = ChatRequest(message="x", session_id="auto_111")
    a2 = ChatRequest(message="x", session_id="auto_222")
    assert _quota_user_key(a1) != _quota_user_key(a2)
    # Cross-channel never collide either.
    assert _quota_user_key(t1) != _quota_user_key(a1)


def test_web_user_unchanged_and_per_user():
    # Web proxy injects a stable per-account slug as user_id; quota must key on it
    # (NOT on the per-conversation session_id), so quota survives new conversations.
    w = ChatRequest(message="x", user_id="antoniocorrei",
                    session_id="antoniocorrei_1707123456789")
    assert _quota_user_key(w) == "antoniocorrei"
    # A second conversation by the same web user → same bucket.
    w2 = ChatRequest(message="x", user_id="antoniocorrei",
                     session_id="antoniocorrei_1707999999999")
    assert _quota_user_key(w) == _quota_user_key(w2)


def test_empty_falls_back_to_anon():
    assert _quota_user_key(ChatRequest(message="x")) == "anon"


def test_one_whatsapp_sender_saturating_rpm_does_not_block_another():
    """The user-visible isolation property the bug violated."""
    async def _run():
        a = ChatRequest(message="hi", session_id="wa_351933333333swhatsappnet")
        b = ChatRequest(message="hi", session_id="wa_351944444444swhatsappnet")
        ka, kb = _quota_user_key(a), _quota_user_key(b)

        # Saturate sender A's per-user RPM bucket.
        for _ in range(user_quota.USER_RPM_CAP + 2):
            user_quota._mem_rpm_add(ka)

        allowed_a, _ = await user_quota.check(ka)
        allowed_b, _ = await user_quota.check(kb)
        assert allowed_a is False, "sender A should be rate-capped after saturation"
        assert allowed_b is True, (
            "sender B must STILL be served — distinct WhatsApp senders share no "
            "quota bucket (regression of the wa_-collapse bug)"
        )
    asyncio.run(_run())
