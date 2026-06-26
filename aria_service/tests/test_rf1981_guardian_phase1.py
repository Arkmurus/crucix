"""R-F1981 — Guardian Phase 1: send-as-you (CONFIRM) + panic (instant SOS).

These drive the real capability surfaces (relay.propose/confirm/cancel,
panic.trigger) through the Action Gateway with an injected send_fn, asserting the
user-visible outcomes: nothing sends without confirmation, a confirmed send
delivers exactly once and is consumed, a panic alerts the WHOLE circle as a
pre-authorized EMERGENCY, an empty circle fails loudly (not silently), and the
panic kill-switch blocks both.
"""
import asyncio

from aria_service.guardian import gateway as gw
from aria_service.guardian import circle as circle
from aria_service.guardian import relay as relay
from aria_service.guardian import panic as panic


def _stub_send(ok=True):
    sent = []
    async def _send(req):
        sent.append(req)
        return ok
    return _send, sent


# ── send-as-you ─────────────────────────────────────────────────────────────
def test_propose_stages_but_does_not_send_then_confirm_delivers():
    async def run():
        send, sent = _stub_send()
        u = "rf1981_s1"
        # propose stages a circle contact, sends nothing
        await circle.add_contact(u, "Mum", "447700900111", "mother")
        p = await relay.propose(u, "Mum", "running 10 min late, sorry!")
        assert p["ok"] and p["staged"] and p["to_name"] == "Mum"
        assert not sent, "propose must NOT send"
        # confirm delivers exactly once, from the user's number
        r = await relay.confirm(u, send)
        assert r["status"] == "delivered"
        assert len(sent) == 1
        assert sent[0].risk == gw.RiskClass.SEND_AS_USER and sent[0].confirmed is True
        assert "447700900111" in sent[0].recipient_jid
        # the staged message is consumed — a second confirm finds nothing
        r2 = await relay.confirm(u, send)
        assert r2["status"] == "nothing_staged"
        assert len(sent) == 1, "must not re-send a consumed message"
    asyncio.run(run())


def test_propose_accepts_raw_number_not_in_circle():
    async def run():
        send, sent = _stub_send()
        u = "rf1981_s2"   # empty circle
        p = await relay.propose(u, "+44 7700 900222", "hello")
        assert p["ok"] and p["to_masked"].endswith("0222")
        r = await relay.confirm(u, send)
        assert r["status"] == "delivered" and "447700900222" in sent[0].recipient_jid
    asyncio.run(run())


def test_propose_unknown_name_without_number_is_refused():
    async def run():
        u = "rf1981_s3"
        p = await relay.propose(u, "Bob", "hi")   # not in circle, not a number
        assert p["ok"] is False and "Bob" in p["error"]
    asyncio.run(run())


def test_cancel_clears_the_staged_send():
    async def run():
        send, sent = _stub_send()
        u = "rf1981_s4"
        await relay.propose(u, "+447700900333", "draft")
        c = await relay.cancel(u)
        assert c["was_staged"] is True
        r = await relay.confirm(u, send)
        assert r["status"] == "nothing_staged" and not sent
    asyncio.run(run())


def test_send_as_you_blocked_by_panic_kill_switch():
    async def run():
        send, sent = _stub_send()
        u = "rf1981_s5"
        await relay.propose(u, "+447700900444", "hi")
        await gw.pause(u)
        r = await relay.confirm(u, send)
        assert r["status"] == "refused_paused" and not sent
        await gw.resume(u)
    asyncio.run(run())


# ── panic / SOS ─────────────────────────────────────────────────────────────
def test_panic_alerts_whole_circle_as_emergency():
    async def run():
        send, sent = _stub_send()
        u = "rf1981_p1"
        await circle.add_contact(u, "Mum", "447700900001", "mother")
        await circle.add_contact(u, "Dad", "447700900002", "father")
        r = await panic.trigger(u, send, note="being followed")
        assert r["ok"] and r["alerted"] == 2 and r["total"] == 2
        assert len(sent) == 2
        assert all(s.risk == gw.RiskClass.EMERGENCY and s.pre_authorized for s in sent)
        assert all("being followed" in s.message for s in sent)
    asyncio.run(run())


def test_panic_with_empty_circle_fails_loudly():
    async def run():
        send, sent = _stub_send()
        u = "rf1981_p2"   # no circle
        r = await panic.trigger(u, send)
        assert r["ok"] is False and r["error"] == "empty_circle" and not sent
    asyncio.run(run())


def test_panic_blocked_by_kill_switch():
    async def run():
        send, sent = _stub_send()
        u = "rf1981_p3"
        await circle.add_contact(u, "Mum", "447700900009", "mother")
        await gw.pause(u)
        r = await panic.trigger(u, send)
        assert r["ok"] is False and r["alerted"] == 0 and not sent
        await gw.resume(u)
    asyncio.run(run())
