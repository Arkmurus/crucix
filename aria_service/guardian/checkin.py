"""Guardian check-in / dead-man's switch (R-F1979) — the flagship capability.

"Check on me in 30 min." ARIA arms a timer. If the user confirms they're safe
("all clear") before the deadline, it disarms. If they DON'T, ARIA alerts every
contact in their trusted circle — through the Action Gateway as a PRE-AUTHORIZED
EMERGENCY action (the user armed it = standing consent). Durable in Redis so a
redeploy/restart cannot drop a pending safety timer (exactly the §25 silent-drop
class). Idempotent: an alert fires at most once per armed check-in.
"""
from __future__ import annotations

import logging
import time

from . import gateway as _gw
from . import circle as _circle
from ..intel import redis_store as rs

logger = logging.getLogger("aria.guardian.checkin")

_CHECKIN_KEY = "crucix:guardian:checkin:{user}"
_ACTIVE_SET = "crucix:guardian:checkin_active"   # set of user ids with an armed check-in
_MAX_MINUTES = 24 * 60


async def arm(user: str, minutes: float, message: str = "") -> dict:
    """Arm (or re-arm) a check-in for `minutes` from now. One active per user."""
    if not user:
        return {"ok": False, "error": "no user"}
    try:
        mins = max(1.0, min(float(minutes), _MAX_MINUTES))
    except (TypeError, ValueError):
        return {"ok": False, "error": "invalid minutes"}
    now = time.time()
    record = {
        "deadline": now + mins * 60.0,
        "message": (message or "").strip()[:300],
        "armed_at": now,
        "minutes": mins,
        "fired": False,
    }
    await rs.set_json(_CHECKIN_KEY.format(user=user), record, ex=int(mins * 60) + 7 * 86400)
    await _active_add(user)
    return {"ok": True, "deadline": record["deadline"], "minutes": mins}


async def all_clear(user: str) -> dict:
    """The user confirmed they're safe — disarm the pending check-in."""
    existed = await _get(user) is not None
    await _disarm(user)
    return {"ok": True, "was_armed": existed}


async def status(user: str) -> dict | None:
    rec = await _get(user)
    if not rec:
        return None
    return {
        "armed": not rec.get("fired"),
        "deadline": rec.get("deadline"),
        "seconds_left": max(0.0, float(rec.get("deadline", 0)) - time.time()),
        "minutes": rec.get("minutes"),
    }


async def reconcile(send_fn: "_gw.SendFn", now: float | None = None) -> int:
    """Fire alerts for every check-in whose deadline passed without an all-clear.
    Called by the Guardian reconcile loop. Returns the number of check-ins fired.
    Idempotent (a fired check-in is disarmed) and fail-safe (a delivery failure
    leaves the check-in armed so the next tick retries, and escalates via the
    gateway's safety-failure path)."""
    now = now if now is not None else time.time()
    users = await _active_members()
    fired = 0
    for user in list(users or []):
        rec = await _get(user)
        if not rec:
            await _disarm(user)
            continue
        if rec.get("fired"):
            await _disarm(user)
            continue
        if now < float(rec.get("deadline", now + 1)):
            continue  # still in-window — legitimately pending
        # Deadline missed with no all-clear → alert the circle.
        contacts = await _circle.list_circle(user)
        if not contacts:
            logger.warning("[guardian.checkin] %s missed a check-in but has an EMPTY circle — cannot alert", user)
            await _disarm(user)   # nothing to do; don't loop forever
            continue
        elapsed = int((now - float(rec.get("armed_at", now))) / 60)
        body = rec.get("message") or ""
        alert = (f"⚠️ SAFETY ALERT — your contact set a {int(rec.get('minutes', 0))}-min "
                 f"ARIA check-in {elapsed} min ago and has NOT confirmed they're safe."
                 + (f"\nThey left this note: {body}" if body else "")
                 + "\nPlease try to reach them now.")
        all_delivered = True
        for c in contacts:
            req = _gw.ActionRequest(
                user=user, kind="checkin_alert", risk=_gw.RiskClass.EMERGENCY,
                recipient_jid=c.get("jid", ""), message=alert, pre_authorized=True,
                meta={"contact": c.get("name", "")},
            )
            res = await _gw.execute(req, send_fn)
            all_delivered = all_delivered and res.get("ok")
        if all_delivered:
            fired += 1
            await _disarm(user)            # done — alerted everyone
        else:
            # leave armed (mark not-fired) so the next tick retries the failures;
            # the gateway already escalated the safety-delivery failure.
            logger.error("[guardian.checkin] %s alert had undelivered contacts — will retry", user)
    return fired


async def _get(user: str) -> dict | None:
    try:
        return await rs.get_json(_CHECKIN_KEY.format(user=user))
    except Exception:
        return None


async def _disarm(user: str) -> None:
    try:
        await rs.delete(_CHECKIN_KEY.format(user=user))
    except Exception:
        pass
    await _active_remove(user)


# ── active-user index (redis_store has no set ops, so a JSON list) ──────────
async def _active_members() -> list[str]:
    try:
        return list((await rs.get_json(_ACTIVE_SET)) or [])
    except Exception:
        return []


async def _active_add(user: str) -> None:
    try:
        members = await _active_members()
        if user not in members:
            members.append(user)
            await rs.set_json(_ACTIVE_SET, members[:10000])
    except Exception as e:
        logger.warning("[guardian.checkin] active_add failed for %s: %s", user, e)


async def _active_remove(user: str) -> None:
    try:
        members = await _active_members()
        if user in members:
            await rs.set_json(_ACTIVE_SET, [u for u in members if u != user])
    except Exception:
        pass
