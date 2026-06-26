"""Guardian panic / SOS (R-F1981).

The instant counterpart to the check-in dead-man's switch: the user sends a panic
phrase ("panic", "SOS", "I'm in danger") and ARIA immediately alerts EVERY contact
in their trusted circle. Pressing the panic button IS the standing consent, so the
action runs through the Action Gateway as a PRE-AUTHORIZED EMERGENCY — the same
hardened path as a missed check-in, just fired now instead of on a timer.

Empty-circle safe: with nobody to alert it returns a clear "circle is empty" so the
user knows their SOS reached no one (the worst silent failure to avoid).
"""
from __future__ import annotations

import logging

from . import circle as _circle
from . import gateway as _gw

logger = logging.getLogger("aria.guardian.panic")


async def trigger(user: str, send_fn: "_gw.SendFn", note: str = "") -> dict:
    """Fire an immediate SOS to the user's whole trusted circle. Returns
    {ok, alerted, total, error?}. ``ok`` is True only if EVERY contact was
    reached; a partial/total failure escalates via the gateway (EMERGENCY)."""
    user = (user or "").strip()
    if not user:
        return {"ok": False, "error": "no user"}
    contacts = await _circle.list_circle(user)
    if not contacts:
        return {"ok": False, "alerted": 0, "total": 0, "error": "empty_circle"}

    note = (note or "").strip()[:300]
    alert = ("🚨 EMERGENCY — your contact triggered an ARIA panic alert and may need "
             "help RIGHT NOW." + (f"\nThey said: {note}" if note else "")
             + "\nPlease try to reach them immediately.")

    alerted = 0
    for c in contacts:
        req = _gw.ActionRequest(
            user=user, kind="panic_alert", risk=_gw.RiskClass.EMERGENCY,
            recipient_jid=c.get("jid", ""), message=alert, pre_authorized=True,
            meta={"contact": c.get("name", "")},
        )
        res = await _gw.execute(req, send_fn)
        if res.get("ok"):
            alerted += 1
        else:
            logger.error("[guardian.panic] %s — alert to %s did NOT deliver",
                         user, c.get("name", "?"))
    return {"ok": alerted == len(contacts), "alerted": alerted, "total": len(contacts)}
