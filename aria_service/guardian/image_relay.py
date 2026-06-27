"""Guardian image send/forward (R-F1989).

ARIA forwards an image FROM the user's own linked number to a contact — but only
to a member of their trusted CIRCLE. WhatsApp/Baileys cannot capture from a phone
camera (no device-sensor access), so "share a photo" is implemented as forwarding
an image the user already has: they send ARIA an image with "send this to <name>"
and she relays it to that circle contact.

Circle membership IS the consent (CIRCLE_ONLY tier in the gateway) — an image
carries far more than text (a face, a location, a document), so it is not allowed
to arbitrary numbers. The send is single-step (no separate confirm): naming an
enrolled contact + attaching the image is the deliberate act. Everything still
flows through the Action Gateway, so the panic kill-switch, audit, and §25
delivery-outcome wiring all apply.
"""
from __future__ import annotations

import base64
import logging

from . import circle as _circle
from . import gateway as _gw

logger = logging.getLogger("aria.guardian.image_relay")

# A safety image is small; cap the decoded payload so a huge upload can't wedge
# the double hop (WA → brain → WA). WhatsApp images are typically well under this.
_MAX_IMAGE_BYTES = 5 * 1024 * 1024


def _decoded_len(image_b64: str) -> int:
    """Decoded byte length of a base64 string without materialising it twice."""
    try:
        return len(base64.b64decode(image_b64, validate=False))
    except Exception:
        return -1


async def forward(user: str, to: str, image_b64: str, caption: str,
                  send_fn: "_gw.SendFn") -> dict:
    """Forward an image to a trusted-circle contact. Returns
    {ok, status, to_name?, to_masked?, error?}. Refuses (does NOT send) if the
    recipient is not in the user's circle or the image is missing/too large."""
    user = (user or "").strip()
    if not user:
        return {"ok": False, "error": "no user"}
    image_b64 = (image_b64 or "").strip()
    if not image_b64:
        return {"ok": False, "error": "no image to send"}

    n = _decoded_len(image_b64)
    if n <= 0:
        return {"ok": False, "error": "image is not valid"}
    if n > _MAX_IMAGE_BYTES:
        return {"ok": False, "error": f"image too large ({n // (1024*1024)}MB, max "
                                      f"{_MAX_IMAGE_BYTES // (1024*1024)}MB)"}

    # Resolve recipient against the trusted circle ONLY — images don't go to raw
    # numbers (the gateway's CIRCLE_ONLY tier would refuse anyway; this gives a
    # clearer message and the contact's display name).
    jid = await _circle.resolve(user, to)
    if not jid:
        return {"ok": False, "status": "not_in_circle",
                "error": f"I can only send images to your trusted circle. \"{to}\" "
                         "isn't in it — add them with \"add <name> <number> to my circle\"."}

    # Find the display name for a friendly confirmation.
    to_name = (to or "").strip()
    for c in await _circle.list_circle(user):
        if _circle._norm_jid(c.get("jid", "")) == _circle._norm_jid(jid):
            to_name = c.get("name") or to_name
            break

    req = _gw.ActionRequest(
        user=user, kind="send_image", risk=_gw.RiskClass.SEND_IMAGE,
        recipient_jid=jid, image_b64=image_b64, caption=(caption or "").strip()[:1000],
        message=(caption or "(image)"),  # human label only; image is the payload
        meta={"to_name": to_name},
    )
    res = await _gw.execute(req, send_fn)
    res["to_name"] = to_name
    res["to_masked"] = _mask(jid)
    return res


def _mask(jid: str) -> str:
    digits = "".join(ch for ch in (jid or "") if ch.isdigit())
    return ("***" + digits[-4:]) if digits else ""
