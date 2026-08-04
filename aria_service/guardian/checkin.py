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
import os
import time

from . import gateway as _gw
from . import circle as _circle
from ..intel import redis_store as rs

logger = logging.getLogger("aria.guardian.checkin")

_CHECKIN_KEY = "crucix:guardian:checkin:{user}"
_ACTIVE_SET = "crucix:guardian:checkin_active"   # set of user ids with an armed check-in
_MAX_MINUTES = 24 * 60

# R-F1981 — two-stage check-in: at the deadline ARIA pings the USER ("are you
# safe?"); only if they still don't reply within this grace window does she
# escalate to the trusted circle. This makes "check on me" actually check on YOU
# (and works even with an empty circle), not just silently arm a circle alert.
_ESCALATE_GRACE_S = float(os.getenv("ARIA_GUARDIAN_GRACE_SECONDS", "120"))


async def arm(user: str, minutes: float, message: str = "", origin_chat: str = "") -> dict:
    """Arm (or re-arm) a check-in for `minutes` from now. One active per user.
    `origin_chat` is the chat the request came from (group or DM jid); the stage-1
    self-ping is delivered there so the user actually SEES it (R-F1982)."""
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
        "self_pinged": False,       # stage 1 (ping the user) not done yet
        "escalate_deadline": None,  # set when stage 1 fires
        "origin_chat": (origin_chat or "").strip(),  # where to deliver the self-ping
    }
    await rs.set_json(_CHECKIN_KEY.format(user=user), record, ex=int(mins * 60) + 7 * 86400)
    await _active_add(user)
    return {"ok": True, "deadline": record["deadline"], "minutes": mins,
            "grace_seconds": _ESCALATE_GRACE_S}


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
    """Drive every armed check-in through the two-stage flow.

    Stage 1 — at the deadline with no all-clear: ping the USER ("are you safe?")
              and start a short grace window. (The user still controls the
              outcome: replying "all clear" disarms.)
    Stage 2 — grace window also missed: alert the trusted CIRCLE (EMERGENCY).

    Returns the number of check-ins that ESCALATED to the circle (stage 2). A
    stage-1 self-ping is not an escalation. Idempotent and fail-safe: a delivery
    failure leaves the check-in armed so the next tick retries, and a failed
    circle alert escalates via the gateway's safety-failure path."""
    now = now if now is not None else time.time()
    users = await _active_members()
    fired = 0
    for user in list(users or []):
        # R-F3713 — read STRICTLY here. `_disarm` below DELETES the record, so
        # treating an unreadable store as "nothing armed" cancels a live safety
        # timer permanently. Skip the user instead: the timer stays armed and
        # the next reconcile pass (60s) retries. Firing a minute late is
        # recoverable; a deleted dead-man's switch is not.
        try:
            rec = await _get(user, strict=True)
        except CheckinStoreUnavailable as _cse:
            logger.error(
                "[R-F3713] guardian check-in for %s UNREADABLE (%s) — leaving the "
                "timer ARMED and retrying next pass; refusing to disarm on a "
                "store failure", user, _cse,
            )
            try:  # §21a — a safety timer we cannot read must reach the brain
                from ..intel.engine_wiring import wire_failure as _wf
                _wf(module="guardian.checkin",
                    detail=f"check-in record unreadable for {user}: {str(_cse)[:120]} "
                           f"— timer left armed, NOT disarmed",
                    gap_type="data_integrity", source="guardian.checkin:R-F3713")
            except Exception:
                pass
            continue
        if not rec or rec.get("fired"):
            await _disarm(user)
            continue
        if now < float(rec.get("deadline", now + 1)):
            continue  # still in-window — legitimately pending

        # ── Stage 1: ping the user themselves ───────────────────────────────
        if not rec.get("self_pinged"):
            body = rec.get("message") or ""
            grace_min = max(1, int(round(_ESCALATE_GRACE_S / 60)))
            ping = ("⏰ ARIA safety check-in — are you safe? Reply \"all clear\" to "
                    "stand me down."
                    + (f"\n(Your note: {body})" if body else "")
                    + f"\nIf I don't hear back in ~{grace_min} min I'll alert your "
                    "trusted circle.")
            req = _gw.ActionRequest(
                user=user, kind="checkin_ping", risk=_gw.RiskClass.NOTIFY_ME,
                recipient_jid=(rec.get("origin_chat") or user), message=ping,
            )
            await _gw.execute(req, send_fn)   # best-effort; circle alert is the hard guarantee
            rec["self_pinged"] = True
            rec["escalate_deadline"] = now + _ESCALATE_GRACE_S
            await _save(user, rec)
            continue

        # ── Stage 2: grace also missed → escalate to the circle ─────────────
        if now < float(rec.get("escalate_deadline", now + 1)):
            continue  # user pinged; still inside the grace window
        contacts = await _circle.list_circle(user)
        if not contacts:
            # The user was pinged at stage 1, so this is NOT a silent drop — but
            # tell them their SOS reached no one, then stop (don't loop).
            try:
                await _gw.execute(_gw.ActionRequest(
                    user=user, kind="checkin_no_circle", risk=_gw.RiskClass.NOTIFY_ME,
                    recipient_jid=(rec.get("origin_chat") or user),
                    message=("⚠️ You didn't confirm you're safe and your trusted circle "
                             "is empty, so I couldn't alert anyone. Add contacts with "
                             "\"add <name> <number> to my circle\".")), send_fn)
            except Exception:
                pass
            logger.warning("[guardian.checkin] %s missed check-in but circle EMPTY — pinged user, cannot escalate", user)
            await _disarm(user)
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


class CheckinStoreUnavailable(RuntimeError):
    """R-F3713 — the check-in record could not be READ.

    Distinct from "no check-in is armed". On a SAFETY timer those must never
    collapse into the same answer.
    """


async def _get(user: str, *, strict: bool = False) -> dict | None:
    """Read a user's check-in record.

    R-F3713 — A STORE FAILURE IS NOT "NO TIMER ARMED".

    THE DEFECT: this swallowed every exception and returned ``None``, which is
    also what an absent record returns. ``reconcile`` then did:

        rec = await _get(user)
        if not rec or rec.get("fired"):
            await _disarm(user)      # <-- DELETES the record
            continue

    So a transient store error did not merely hide the timer — it **cancelled**
    it, permanently, and `_disarm` removed the record so nothing could recover
    it. A dead-man's switch silently disarmed by a Redis blip is the worst
    failure this module can have: the alarm simply never goes off, and the
    person it was protecting has no way to know.

    ``strict=True`` raises instead, so the reconcile loop can SKIP the user and
    leave the timer armed. An armed timer that fires late is recoverable; one
    that was deleted is not.
    """
    try:
        return await (rs.get_json_strict(_CHECKIN_KEY.format(user=user)) if strict
                      else rs.get_json(_CHECKIN_KEY.format(user=user)))
    except Exception as exc:
        if strict:
            raise CheckinStoreUnavailable(str(exc)) from exc
        return None


async def _save(user: str, rec: dict) -> None:
    """Persist an updated check-in record (e.g. after the stage-1 self-ping),
    preserving a sane TTL so a pending safety timer can't quietly evaporate."""
    try:
        ttl = int(_ESCALATE_GRACE_S) + 7 * 86400
        await rs.set_json(_CHECKIN_KEY.format(user=user), rec, ex=ttl)
    except Exception as e:
        logger.warning("[guardian.checkin] save failed for %s: %s", user, e)


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
