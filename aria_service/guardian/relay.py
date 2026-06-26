"""Guardian send-as-you (R-F1981).

ARIA sends a WhatsApp message FROM the user's own linked number — but only after
an explicit one-tap confirmation. This is the SEND_AS_USER risk class, whose
consent tier is CONFIRM, so it is a deliberate two-step:

  1. propose(user, to, message) — resolve the recipient, STAGE the message, and
     return a masked preview. Nothing is sent yet.
  2. confirm(user, send_fn)     — the user said "send it" → the staged message is
     delivered through the Action Gateway (which audits + wires the outcome).

A staged message is single-per-user and short-lived (10 min TTL) so a stale
confirmation can never fire an old message. The recipient is resolved against the
trusted circle by name first; a raw phone number is allowed (CONFIRM still gates
it) so the user can text anyone, exactly like typing it themselves.
"""
from __future__ import annotations

import logging
import time

from . import circle as _circle
from . import gateway as _gw
from ..intel import redis_store as rs

logger = logging.getLogger("aria.guardian.relay")

_PENDING_KEY = "crucix:guardian:pending_send:{user}"
_PENDING_TTL = 600  # seconds — a staged send expires after 10 min


def _mask(jid: str) -> str:
    digits = "".join(ch for ch in (jid or "") if ch.isdigit())
    return ("***" + digits[-4:]) if digits else ""


async def propose(user: str, to: str, message: str) -> dict:
    """Stage a send-as-you. Resolves `to` (circle name first, else a raw number)
    and stores it pending confirmation. Returns a masked preview — sends nothing."""
    user = (user or "").strip()
    message = (message or "").strip()
    if not user:
        return {"ok": False, "error": "no user"}
    if not message:
        return {"ok": False, "error": "nothing to send"}
    if len(message) > 4000:
        return {"ok": False, "error": "message too long"}

    # Resolve recipient: a trusted-circle name wins; otherwise accept a phone number.
    jid = await _circle.resolve(user, to)
    to_name = (to or "").strip()
    if not jid:
        njid = _circle._norm_jid(to)
        if not njid:
            return {"ok": False, "error": f"I don't know who \"{to}\" is — add them to your "
                                          "circle, or give me a phone number."}
        jid = njid
        to_name = _mask(jid)

    record = {"jid": jid, "to_name": to_name, "message": message, "staged_at": time.time()}
    try:
        await rs.set_json(_PENDING_KEY.format(user=user), record, ex=_PENDING_TTL)
    except Exception as e:
        logger.warning("[guardian.relay] stage failed for %s: %s", user, e)
        return {"ok": False, "error": "could not stage the message"}
    return {"ok": True, "staged": True, "to_name": to_name,
            "to_masked": _mask(jid), "preview": message[:300]}


async def confirm(user: str, send_fn: "_gw.SendFn") -> dict:
    """The user confirmed — deliver the staged message through the gateway."""
    user = (user or "").strip()
    rec = None
    try:
        rec = await rs.get_json(_PENDING_KEY.format(user=user))
    except Exception:
        rec = None
    if not rec:
        return {"ok": False, "status": "nothing_staged",
                "error": "nothing to send — tell me \"text <name> saying …\" first"}

    req = _gw.ActionRequest(
        user=user, kind="send_text", risk=_gw.RiskClass.SEND_AS_USER,
        recipient_jid=rec.get("jid", ""), message=rec.get("message", ""),
        confirmed=True, meta={"to_name": rec.get("to_name", "")},
    )
    res = await _gw.execute(req, send_fn)
    # Clear the pending regardless of delivery result: a confirmed send is consumed;
    # a failure surfaces via the gateway's outcome/escalation, not a silent re-fire.
    try:
        await rs.delete(_PENDING_KEY.format(user=user))
    except Exception:
        pass
    res["to_name"] = rec.get("to_name", "")
    res["to_masked"] = _mask(rec.get("jid", ""))
    return res


async def cancel(user: str) -> dict:
    user = (user or "").strip()
    try:
        existed = await rs.get_json(_PENDING_KEY.format(user=user)) is not None
        await rs.delete(_PENDING_KEY.format(user=user))
    except Exception:
        existed = False
    return {"ok": True, "was_staged": existed}


async def pending(user: str) -> dict | None:
    """Inspect the staged send (masked) without sending — for status/UX."""
    try:
        rec = await rs.get_json(_PENDING_KEY.format(user=(user or "").strip()))
    except Exception:
        rec = None
    if not rec:
        return None
    return {"to_name": rec.get("to_name", ""), "to_masked": _mask(rec.get("jid", "")),
            "preview": (rec.get("message", "") or "")[:300]}
