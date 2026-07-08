"""Guardian outbound delivery (R-F1981).

The ONE place that turns an ``ActionRequest`` into an actual WhatsApp send, so
every Guardian capability (check-in reconcile, send-as-you, panic) delivers
through the identical, hardened hop instead of each re-implementing the httpx
call. The send goes through aria-wa's ``/api/wa-listener/send`` — which sends
from the linked WhatsApp number (i.e. the user's OWN number), so this is genuine
"send AS you", not a third-party relay.
"""
from __future__ import annotations

import os

import httpx

from . import gateway as _gw
from ..intel.engine_wiring import wire_success, wire_failure  # R-F2489 §21a success+failure

_WA_SEND_URL = "http://aria-wa.internal:5070/api/wa-listener/send"


def wa_send_fn(timeout: float = 8.0) -> "_gw.SendFn":
    """Build the production send_fn that delivers via aria-wa (the linked number).
    Returns True only on a 200 so the gateway's audit/outcome reflect reality."""
    tok = os.getenv("ARIA_INTERNAL_TOKEN", "")

    async def _send(req: "_gw.ActionRequest") -> bool:
        if not req.recipient_jid or not req.message:
            return False
        try:
            async with httpx.AsyncClient(timeout=timeout) as c:
                r = await c.post(
                    _WA_SEND_URL,
                    headers={"Authorization": f"Bearer {tok}",
                             "Content-Type": "application/json"},
                    json={"to": req.recipient_jid, "message": req.message},
                )
                # R-F2489 §21a — delivery outcome reaches the brain on BOTH
                # branches (was `except: return False` = dark), per §25.
                if r.status_code == 200:
                    wire_success(module="guardian_delivery", summary="wa_send delivered")
                    return True
                wire_failure(module="guardian_delivery",
                             detail=f"wa_send non-200: http_{r.status_code}",
                             gap_type="delivery_failure", source="guardian_delivery")
                return False
        except Exception as e:
            wire_failure(module="guardian_delivery",
                         detail=f"wa_send raised: {e}",
                         gap_type="delivery_failure", source="guardian_delivery")
            return False

    return _send


def wa_send_image_fn(timeout: float = 30.0) -> "_gw.SendFn":
    """Build the send_fn that delivers an IMAGE via aria-wa (the linked number).
    Sends ``req.image_b64`` with an optional ``req.caption``. A longer timeout
    than text because an image upload is larger. Returns True only on a 200."""
    tok = os.getenv("ARIA_INTERNAL_TOKEN", "")

    async def _send(req: "_gw.ActionRequest") -> bool:
        if not req.recipient_jid or not req.image_b64:
            return False
        try:
            async with httpx.AsyncClient(timeout=timeout) as c:
                r = await c.post(
                    _WA_SEND_URL,
                    headers={"Authorization": f"Bearer {tok}",
                             "Content-Type": "application/json"},
                    json={"to": req.recipient_jid, "image_b64": req.image_b64,
                          "caption": req.caption or ""},
                )
                # R-F2489 §21a — image delivery outcome reaches the brain on BOTH
                # branches (was `except: return False` = dark), per §25.
                if r.status_code == 200:
                    wire_success(module="guardian_delivery", summary="wa_send_image delivered")
                    return True
                wire_failure(module="guardian_delivery",
                             detail=f"wa_send_image non-200: http_{r.status_code}",
                             gap_type="delivery_failure", source="guardian_delivery")
                return False
        except Exception as e:
            wire_failure(module="guardian_delivery",
                         detail=f"wa_send_image raised: {e}",
                         gap_type="delivery_failure", source="guardian_delivery")
            return False

    return _send
