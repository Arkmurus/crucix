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


async def trigger(user: str, send_fn: "_gw.SendFn", note: str = "",
                  dry_run: bool = False) -> dict:
    """Fire an immediate SOS to the user's whole trusted circle. Returns
    {ok, alerted, total, error?}. ``ok`` is True only if EVERY contact was
    reached; a partial/total failure escalates via the gateway (EMERGENCY).

    R-F1992: ``dry_run`` runs the full chain for an operator self-test but
    keeps delivery failures OUT of the production error ledger (so a panic
    button test can't reset Phase A gate #3 — CLAUDE.md §1). A real SOS
    (dry_run=False) still logs ERROR + escalates on a failed delivery (§25)."""
    user = (user or "").strip()
    if not user:
        return {"ok": False, "error": "no user"}
    contacts = await _circle.list_circle(user)
    if not contacts:
        # R-F4252 (C-219) — THE WORST SILENT FAILURE, and it was the one branch
        # that reached nothing. This module's own docstring names it: "Empty-circle
        # safe: with nobody to alert it returns a clear 'circle is empty' so the
        # user knows their SOS reached no one (the worst silent failure to avoid)."
        # It returned a dict and emitted NOTHING — no log, no gap, no brain signal.
        # An SOS that alerted nobody was invisible to ARIA unless a caller happened
        # to render the return value.
        #
        # It never reaches the Action Gateway (which is what carries every OTHER
        # panic outcome to `record_gap`), so the transitive wiring the audit sees
        # for the rest of this file does not cover it.
        _report(user, ok=False, alerted=0, total=0, reason="empty_circle",
                dry_run=dry_run)
        return {"ok": False, "alerted": 0, "total": 0, "error": "empty_circle",
                "dry_run": dry_run}

    note = (note or "").strip()[:300]
    alert = ("🚨 EMERGENCY — your contact triggered an ARIA panic alert and may need "
             "help RIGHT NOW." + (f"\nThey said: {note}" if note else "")
             + "\nPlease try to reach them immediately.")

    alerted = 0
    for c in contacts:
        req = _gw.ActionRequest(
            user=user, kind="panic_alert", risk=_gw.RiskClass.EMERGENCY,
            recipient_jid=c.get("jid", ""), message=alert, pre_authorized=True,
            dry_run=dry_run, meta={"contact": c.get("name", "")},
        )
        res = await _gw.execute(req, send_fn)
        if res.get("ok"):
            alerted += 1
        elif dry_run:
            # Self-test: attended failure, surfaced in the return value — NOT an
            # ERROR (would reset gate #3). The gateway already logged a [TEST] line.
            logger.warning("[guardian.panic][TEST] %s — alert to %s did NOT "
                           "deliver (self-test)", user, c.get("name", "?"))
        else:
            logger.error("[guardian.panic] %s — alert to %s did NOT deliver",
                         user, c.get("name", "?"))
    _ok = alerted == len(contacts)
    # R-F4252 — §21a wants BOTH branches, and §25 makes this the sharpest case
    # there is: "for ANY action ARIA takes that produces a result for a user ...
    # she must KNOW whether the intended result was actually produced." A
    # successful SOS is a rare, high-significance safety event and reached no
    # sink either.
    _report(user, ok=_ok, alerted=alerted, total=len(contacts),
            reason="" if _ok else ("none_reached" if alerted == 0 else "partial"),
            dry_run=dry_run)
    return {"ok": _ok, "alerted": alerted,
            "total": len(contacts), "dry_run": dry_run}


def _report(user: str, *, ok: bool, alerted: int, total: int,
            reason: str, dry_run: bool) -> None:
    """R-F4252 — ONE outcome signal per SOS. Never raises.

    Deliberately one signal per panic, not one per contact: the gateway already
    records each individual delivery failure (`guardian_safety_delivery_failure`),
    so per-contact reporting here would double-count. What the gateway CANNOT say
    is whether the SOS as a whole reached the circle — 0-of-3 and 3-of-3 look
    identical from inside a single delivery.

    A panic is rare, so a per-event signal carries no flood risk (the shape that
    has twice filled a 500-slot ledger); the debounce those cases needed would be
    actively harmful here, because two SOS events in a minute is exactly when you
    want both.

    `dry_run` emits NOTHING. R-F1992 established that an operator self-test must
    not enter the production error ledger; the same reasoning applies to the gap
    ledger, and the [TEST] log line plus the return value are the self-test's
    channel. A test that pages like a real emergency trains people to ignore it.
    """
    if dry_run:
        return
    try:
        from ..intel.engine_wiring import wire_success as _ws, wire_failure as _wf
        if ok:
            _ws(
                module="guardian_panic",
                summary=f"SOS delivered to {alerted}/{total} contacts",
                detail=(f"panic alert for {user} reached the whole trusted "
                        f"circle ({alerted}/{total})")[:600],
                source_id=f"guardian_panic:delivered:{user}",
            )
        else:
            _wf(
                module="guardian_panic",
                detail=(
                    f"SOS for {user} did NOT reach the trusted circle "
                    f"({alerted}/{total}, reason={reason}). "
                    + ("The circle is EMPTY — the user has no emergency contacts, "
                       "so the panic button cannot alert anyone. This is a "
                       "configuration gap, not a delivery fault: adding a contact "
                       "is the fix, retrying is not."
                       if reason == "empty_circle" else
                       "Delivery failed; per-contact detail is in "
                       "guardian_safety_delivery_failure.")
                )[:600],
                gap_type="guardian_safety_delivery_failure",
                source=f"guardian_panic:{reason}:{user}",
            )
    except Exception:
        # An observability bug must not break an emergency path.
        logger.debug("[R-F4252] panic outcome wiring failed", exc_info=True)
