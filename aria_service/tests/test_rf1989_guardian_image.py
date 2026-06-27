"""R-F1989 — Guardian image send/forward to a trusted contact.

Drives the real capability surface (image_relay.forward) through the Action
Gateway with an injected send_fn, asserting the user-visible outcomes:
  * an image to a CIRCLE contact delivers (risk=SEND_IMAGE, the bytes are passed),
  * a recipient NOT in the circle is refused and nothing sends,
  * the panic kill-switch blocks the send,
  * empty / invalid / oversized images are refused before any send.
"""
import asyncio
import base64

from aria_service.guardian import gateway as gw
from aria_service.guardian import circle as circle
from aria_service.guardian import image_relay as image_relay

_IMG = base64.b64encode(b"\x89PNG\r\n\x1a\n fake image bytes").decode("ascii")


def _stub_send(ok=True):
    sent = []
    async def _send(req):
        sent.append(req)
        return ok
    return _send, sent


def test_image_to_circle_contact_delivers():
    async def run():
        send, sent = _stub_send()
        u = "rf1989_i1"
        await circle.add_contact(u, "Mum", "447700900111", "mother")
        r = await image_relay.forward(u, "Mum", _IMG, "outside the cafe", send)
        assert r["ok"] and r["status"] == "delivered"
        assert r["to_name"] == "Mum"
        assert len(sent) == 1
        assert sent[0].risk == gw.RiskClass.SEND_IMAGE
        assert sent[0].image_b64 == _IMG
        assert sent[0].caption == "outside the cafe"
        assert "447700900111" in sent[0].recipient_jid
    asyncio.run(run())


def test_image_to_non_circle_is_refused():
    async def run():
        send, sent = _stub_send()
        u = "rf1989_i2"   # empty circle
        r = await image_relay.forward(u, "+44 7700 900222", _IMG, "", send)
        assert r["ok"] is False and r["status"] == "not_in_circle"
        assert not sent, "must NOT send an image to a non-circle recipient"
    asyncio.run(run())


def test_image_blocked_by_kill_switch():
    async def run():
        send, sent = _stub_send()
        u = "rf1989_i3"
        await circle.add_contact(u, "Mum", "447700900333", "mother")
        await gw.pause(u)
        r = await image_relay.forward(u, "Mum", _IMG, "", send)
        assert r["ok"] is False and r["status"] == "refused_paused"
        assert not sent
        await gw.resume(u)
    asyncio.run(run())


def test_empty_and_invalid_image_refused():
    async def run():
        send, sent = _stub_send()
        u = "rf1989_i4"
        await circle.add_contact(u, "Mum", "447700900444", "mother")
        r = await image_relay.forward(u, "Mum", "", "", send)
        assert r["ok"] is False and "no image" in r["error"]
        assert not sent
    asyncio.run(run())


def test_oversized_image_refused():
    async def run():
        send, sent = _stub_send()
        u = "rf1989_i5"
        await circle.add_contact(u, "Mum", "447700900555", "mother")
        big = base64.b64encode(b"\0" * (5 * 1024 * 1024 + 1)).decode("ascii")
        r = await image_relay.forward(u, "Mum", big, "", send)
        assert r["ok"] is False and "too large" in r["error"]
        assert not sent
    asyncio.run(run())


def test_wa_send_image_fn_refuses_without_image_or_recipient():
    """The production image send_fn must not attempt a network send when the
    payload is incomplete — it returns False (no recipient / no image bytes)."""
    from aria_service.guardian.delivery import wa_send_image_fn

    async def run():
        send = wa_send_image_fn()
        no_recipient = gw.ActionRequest(
            user="u", kind="send_image", risk=gw.RiskClass.SEND_IMAGE,
            recipient_jid="", image_b64=_IMG)
        assert await send(no_recipient) is False
        no_image = gw.ActionRequest(
            user="u", kind="send_image", risk=gw.RiskClass.SEND_IMAGE,
            recipient_jid="447700900000@s.whatsapp.net", image_b64="")
        assert await send(no_image) is False
    asyncio.run(run())
