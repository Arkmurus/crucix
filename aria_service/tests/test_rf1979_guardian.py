"""R-F1979 — ARIA Guardian Action Gateway + check-in/dead-man's switch.

Safety-critical, so the tests pin the invariants: consent gating, the panic
kill-switch, the encrypted trusted circle, the tamper-evident audit chain, and
the full check-in lifecycle (arm → all-clear, and arm → miss → alert-circle,
idempotently and only to enrolled contacts).
"""
import asyncio
import os

from cryptography.fernet import Fernet

from aria_service.guardian import gateway as gw
from aria_service.guardian import circle as circle
from aria_service.guardian import checkin as checkin
from aria_service.guardian import audit as audit


def _stub_send(ok=True):
    sent = []
    async def _send(req):
        sent.append(req)
        return ok
    return _send, sent


# ── Gateway: consent gating ────────────────────────────────────────────────
def test_notify_me_is_auto_but_send_as_user_needs_confirmation():
    async def run():
        send, sent = _stub_send()
        # NOTIFY_ME → auto, delivers
        r = await gw.execute(gw.ActionRequest("rf1979_g1", "notify", gw.RiskClass.NOTIFY_ME,
                                              message="hi"), send)
        assert r["status"] == "delivered"
        # SEND_AS_USER without confirmation → refused
        r2 = await gw.execute(gw.ActionRequest("rf1979_g1", "send", gw.RiskClass.SEND_AS_USER,
                                               recipient_jid="441234@s.whatsapp.net",
                                               message="hi", confirmed=False), send)
        assert r2["status"] == "refused_consent"
        # with confirmation → delivers
        r3 = await gw.execute(gw.ActionRequest("rf1979_g1", "send", gw.RiskClass.SEND_AS_USER,
                                               recipient_jid="441234@s.whatsapp.net",
                                               message="hi", confirmed=True), send)
        assert r3["status"] == "delivered"
    asyncio.run(run())


def test_alert_circle_requires_circle_membership():
    async def run():
        send, sent = _stub_send()
        u = "rf1979_g2"
        # not in circle → refused
        r = await gw.execute(gw.ActionRequest(u, "alert", gw.RiskClass.ALERT_CIRCLE,
                                              recipient_jid="447700900000@s.whatsapp.net",
                                              message="x"), send)
        assert r["status"] == "refused_consent" and "circle" in r.get("reason", "")
        # enrol → now allowed
        await circle.add_contact(u, "Mum", "447700900000", "mother")
        r2 = await gw.execute(gw.ActionRequest(u, "alert", gw.RiskClass.ALERT_CIRCLE,
                                               recipient_jid="447700900000@s.whatsapp.net",
                                               message="x"), send)
        assert r2["status"] == "delivered"
    asyncio.run(run())


def test_panic_kill_switch_blocks_everything():
    async def run():
        send, sent = _stub_send()
        u = "rf1979_g3"
        await gw.pause(u)
        r = await gw.execute(gw.ActionRequest(u, "notify", gw.RiskClass.NOTIFY_ME, message="hi"), send)
        assert r["status"] == "refused_paused"
        assert not sent, "nothing may be sent while paused"
        await gw.resume(u)
        r2 = await gw.execute(gw.ActionRequest(u, "notify", gw.RiskClass.NOTIFY_ME, message="hi"), send)
        assert r2["status"] == "delivered"
    asyncio.run(run())


# ── Trusted circle: encryption round-trip + membership ─────────────────────
def test_circle_encrypts_and_resolves(monkeypatch):
    monkeypatch.setenv("ARIA_GUARDIAN_VAULT_KEY", Fernet.generate_key().decode())
    circle._FERNET_CACHE = None  # reset cached cipher so the test key is used
    async def run():
        u = "rf1979_c1"
        await circle.add_contact(u, "Sam", "+44 7700 900123", "friend")
        lst = await circle.list_circle(u)
        assert any(c["name"] == "Sam" and c["jid"].startswith("447700900123") for c in lst)
        assert await circle.is_in_circle(u, "447700900123@s.whatsapp.net") is True
        assert await circle.resolve(u, "Sam") == "447700900123@s.whatsapp.net"
        # at-rest value must be ciphertext, not the raw number
        raw = await circle._load(u)
        assert raw[0]["jid"].startswith("enc:"), "contact jid must be encrypted at rest"
        await circle.remove_contact(u, "Sam")
        assert await circle.is_in_circle(u, "447700900123@s.whatsapp.net") is False
    asyncio.run(run())
    circle._FERNET_CACHE = None


# ── Audit: tamper-evident chain ────────────────────────────────────────────
def test_audit_chain_detects_tampering():
    async def run():
        u = "rf1979_a1"
        await audit.record(u, "send_text", {"to": "x"}, outcome="delivered")
        await audit.record(u, "checkin_alert", {"to": "y"}, outcome="delivered")
        v = await audit.verify_chain(u)
        assert v["ok"] and v["length"] == 2
        # tamper: rewrite an entry's outcome in storage
        from aria_service.intel import redis_store as rs
        import json
        key = audit._AUDIT_KEY.format(user=u)
        raw = await rs.lrange(key, 0, 10)
        entries = [json.loads(r) for r in raw]
        entries[-1]["outcome"] = "FORGED"      # tamper the oldest (chain base)
        await rs.delete(key)
        for e in reversed(entries):
            await rs.lpush(key, json.dumps(e))
        v2 = await audit.verify_chain(u)
        assert v2["ok"] is False, "a tampered audit entry must break the chain"
    asyncio.run(run())


# ── Check-in / dead-man's switch lifecycle ─────────────────────────────────
def test_checkin_all_clear_disarms():
    async def run():
        u = "rf1979_k1"
        await checkin.arm(u, minutes=30)
        st = await checkin.status(u)
        assert st and st["armed"] is True
        await checkin.all_clear(u)
        assert await checkin.status(u) is None
    asyncio.run(run())


def test_checkin_miss_pings_user_then_alerts_circle_and_is_idempotent():
    async def run():
        send, sent = _stub_send(ok=True)
        u = "rf1979_k2"
        await circle.add_contact(u, "Mum", "447700900001", "mother")
        await circle.add_contact(u, "Dad", "447700900002", "father")
        await checkin.arm(u, minutes=30, message="walking home")
        # before deadline → nothing fires
        assert await checkin.reconcile(send, now=__import__("time").time()) == 0
        assert not sent
        deadline = (await checkin.status(u))["deadline"]
        # STAGE 1 — at the deadline ARIA pings the USER (not the circle yet)
        assert await checkin.reconcile(send, now=deadline + 1) == 0
        assert len(sent) == 1 and sent[0].risk == gw.RiskClass.NOTIFY_ME
        assert sent[0].recipient_jid == u, "stage 1 must ping the user themselves"
        # STAGE 2 — grace window also missed → alert BOTH circle contacts
        sent.clear()
        past = deadline + 1 + checkin._ESCALATE_GRACE_S + 1
        fired = await checkin.reconcile(send, now=past)
        assert fired == 1
        assert len(sent) == 2, "both circle contacts must be alerted"
        assert all(r.risk == gw.RiskClass.EMERGENCY and r.pre_authorized for r in sent)
        # idempotent — a second reconcile must NOT re-fire
        sent.clear()
        assert await checkin.reconcile(send, now=past + 10) == 0
        assert not sent
    asyncio.run(run())


def test_checkin_miss_with_empty_circle_pings_user_then_disarms():
    async def run():
        send, sent = _stub_send()
        u = "rf1979_k3"          # no circle enrolled
        await checkin.arm(u, minutes=30)
        deadline = (await checkin.status(u))["deadline"]
        # stage 1 still pings the user even with an empty circle (not a silent drop)
        assert await checkin.reconcile(send, now=deadline + 1) == 0
        assert len(sent) == 1 and sent[0].recipient_jid == u
        # stage 2 with empty circle → tell the user it couldn't escalate, then disarm
        sent.clear()
        past = deadline + 1 + checkin._ESCALATE_GRACE_S + 1
        assert await checkin.reconcile(send, now=past) == 0
        assert len(sent) == 1 and sent[0].kind == "checkin_no_circle"
        assert await checkin.status(u) is None, "an unfireable check-in must be disarmed, not looped"
    asyncio.run(run())
