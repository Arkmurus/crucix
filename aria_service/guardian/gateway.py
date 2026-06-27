"""Guardian Action Gateway (R-F1979) — the ONE hardened door.

Every real-world Guardian action passes through execute():
  classify risk → panic kill-switch → consent gate → deliver → audit → §25 outcome.

Design invariants (safety-critical):
  * A non-consented action is REFUSED, never silently sent.
  * The panic kill-switch blocks ALL outbound action ("ARIA stop" = ARIA stops).
  * Sensitive recipients must be in the user's trusted CIRCLE (circle.py).
  * Every attempt is audited (audit.py) AND reports a delivery outcome (outcome_wire)
    so the brain KNOWS whether it actually happened — and a failed SAFETY action
    ESCALATES (records a gap), never silently drops.
  * The delivery is INJECTED (send_fn) so the gateway is provider-agnostic and
    fully testable; prod injects the aria-wa /send call.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Awaitable, Callable

from . import audit as _audit
from . import circle as _circle
from ..intel import redis_store as rs

logger = logging.getLogger("aria.guardian.gateway")

_PAUSE_KEY = "crucix:guardian:paused:{user}"


class RiskClass(str, Enum):
    NOTIFY_ME = "notify_me"          # ARIA tells the USER something — no external action
    SEND_AS_USER = "send_as_user"    # ARIA sends a message AS the user to someone
    SEND_IMAGE = "send_image"        # ARIA sends an image AS the user to a circle contact
    ALERT_CIRCLE = "alert_circle"    # ARIA alerts a trusted-circle contact
    SHARE_LOCATION = "share_location"  # ARIA shares the user's location with a contact
    EMERGENCY = "emergency"          # panic / dead-man's-switch escalation


class ConsentTier(str, Enum):
    AUTO = "auto"                    # no confirmation
    CONFIRM = "confirm"              # one-tap confirmation required
    CIRCLE_ONLY = "circle_only"      # recipient MUST be in the trusted circle
    PRE_AUTHORIZED = "pre_authorized"  # standing consent (emergency/check-in, armed in advance)


# Required consent per risk class — the policy table.
_REQUIRED: dict[RiskClass, ConsentTier] = {
    RiskClass.NOTIFY_ME: ConsentTier.AUTO,
    RiskClass.SEND_AS_USER: ConsentTier.CONFIRM,
    # An image carries far more (a face, a location, a document) than text, so it is
    # restricted to the trusted CIRCLE — enrolment IS the consent. A raw number is
    # refused for images (relax later if the operator wants images to anyone).
    RiskClass.SEND_IMAGE: ConsentTier.CIRCLE_ONLY,
    RiskClass.ALERT_CIRCLE: ConsentTier.CIRCLE_ONLY,
    RiskClass.SHARE_LOCATION: ConsentTier.CIRCLE_ONLY,
    RiskClass.EMERGENCY: ConsentTier.PRE_AUTHORIZED,
}


@dataclass
class ActionRequest:
    user: str
    kind: str                       # human label, e.g. "send_text", "checkin_alert"
    risk: RiskClass
    recipient_jid: str = ""         # who it goes to (empty for NOTIFY_ME)
    message: str = ""
    image_b64: str = ""             # base64 image payload (SEND_IMAGE only)
    caption: str = ""               # optional caption for an image send
    confirmed: bool = False         # one-tap confirmation supplied (CONFIRM tier)
    pre_authorized: bool = False    # standing consent supplied (EMERGENCY tier)
    # R-F1992: operator self-test of the Guardian chain. Runs the FULL path
    # (kill-switch, consent, circle, real delivery attempt) so the test is
    # meaningful, but a delivery FAILURE in test mode must not pollute the
    # production error ledger — it logs at WARNING with a [TEST] marker and
    # never resets Phase A gate #3 (CLAUDE.md §1) nor records a safety-failure
    # gap. A REAL panic failure (dry_run=False) still ERRORs + escalates (§25).
    dry_run: bool = False
    meta: dict = field(default_factory=dict)


# A send_fn delivers the action and returns True on success.
SendFn = Callable[[ActionRequest], Awaitable[bool]]


async def is_paused(user: str) -> bool:
    try:
        return ((await rs.get(_PAUSE_KEY.format(user=user))) or "") == "1"
    except Exception:
        return False  # fail-open on a read error so a transient blip can't freeze safety


async def pause(user: str) -> None:
    """Panic kill-switch: stop ALL Guardian outbound action for this user."""
    try:
        await rs.set(_PAUSE_KEY.format(user=user), "1")
    except Exception as e:
        logger.error("[guardian.gateway] PAUSE write failed for %s: %s", user, e)


async def resume(user: str) -> None:
    try:
        await rs.set(_PAUSE_KEY.format(user=user), "0")
    except Exception:
        pass


async def _consent_ok(req: ActionRequest) -> tuple[bool, str]:
    tier = _REQUIRED[req.risk]
    if tier == ConsentTier.AUTO:
        return True, ""
    if tier == ConsentTier.CONFIRM:
        return (req.confirmed, "" if req.confirmed else "needs one-tap confirmation")
    if tier == ConsentTier.CIRCLE_ONLY:
        if not req.recipient_jid:
            return False, "no recipient"
        if not await _circle.is_in_circle(req.user, req.recipient_jid):
            return False, "recipient is not in your trusted circle"
        return True, ""
    if tier == ConsentTier.PRE_AUTHORIZED:
        return (req.pre_authorized, "" if req.pre_authorized else "not pre-authorized")
    return False, "unknown consent tier"


async def execute(req: ActionRequest, send_fn: SendFn) -> dict:
    """Run an action through the gateway. Returns a result dict:
      {ok, status, audited, outcome} where status ∈
      delivered | refused_paused | refused_consent | failed."""
    # 1. Panic kill-switch — stops everything.
    if await is_paused(req.user):
        await _audit.record(req.user, req.kind, {"risk": req.risk.value,
                            "recipient": _redact(req.recipient_jid)}, outcome="refused_paused")
        return {"ok": False, "status": "refused_paused"}

    # 2. Consent gate.
    ok, why = await _consent_ok(req)
    if not ok:
        await _audit.record(req.user, req.kind, {"risk": req.risk.value,
                            "recipient": _redact(req.recipient_jid), "reason": why},
                            outcome="refused_consent")
        return {"ok": False, "status": "refused_consent", "reason": why}

    # 3. Deliver (injected). 4. Audit. 5. §25 outcome (+ escalate on safety failure).
    delivered = False
    err = ""
    try:
        delivered = bool(await send_fn(req))
    except Exception as e:
        err = str(e)[:200]
        delivered = False

    # R-F1992: a self-test that fails delivery is an ATTENDED test (the caller
    # sees ok/false in the response) — not a silent §25 failure the brain must
    # self-heal from. Keep it out of the ERROR ledger so it can't reset gate #3.
    if req.dry_run:
        status = "test_delivered" if delivered else "test_failed"
        await _audit.record(req.user, req.kind, {"risk": req.risk.value,
                            "recipient": _redact(req.recipient_jid), "error": err,
                            "dry_run": True}, outcome=status)
        # Deliberately NOT wired to the §25 proprioception ledger — a self-test
        # must not skew the real per-channel delivery success rate.
        if not delivered:
            logger.warning("[guardian][TEST] %s → %s did NOT deliver (%s) — "
                           "self-test, gate #3 unaffected", req.kind,
                           _redact(req.recipient_jid), err or "no error")
        return {"ok": delivered, "status": status, "error": err, "dry_run": True}

    status = "delivered" if delivered else "failed"
    await _audit.record(req.user, req.kind, {"risk": req.risk.value,
                        "recipient": _redact(req.recipient_jid), "error": err},
                        outcome=status)
    await _wire_outcome(req, delivered, err)

    if not delivered and req.risk == RiskClass.EMERGENCY:
        # A SAFETY action that did not deliver is the worst case — escalate loudly.
        await _escalate_safety_failure(req, err)

    return {"ok": delivered, "status": status, "error": err}


async def _wire_outcome(req: ActionRequest, delivered: bool, err: str) -> None:
    try:
        from ..intel.outcome_wire import OutcomeRecord, record_outcome
        await record_outcome(OutcomeRecord(
            surface="guardian",
            request_id=f"{req.user}:{req.kind}:{int(time.time()*1000)}",
            intended_result=req.kind,
            actual_outcome="delivered_real_answer" if delivered else "send_failed",
            latency_ms=0,
            detail=err or req.risk.value,
        ))
    except Exception:
        pass


async def _escalate_safety_failure(req: ActionRequest, err: str) -> None:
    logger.error("[guardian] SAFETY action FAILED to deliver: %s → %s (%s)",
                 req.kind, _redact(req.recipient_jid), err)
    try:
        from ..intel.capability_gaps import record_gap
        await record_gap(gap_type="guardian_safety_delivery_failure",
                         detail=f"{req.kind} to {_redact(req.recipient_jid)} failed: {err}",
                         source="guardian.gateway")
    except Exception:
        pass


def _redact(jid: str) -> str:
    """Audit/log redaction — keep the last 4 digits only (PII minimisation)."""
    digits = "".join(ch for ch in (jid or "") if ch.isdigit())
    return ("***" + digits[-4:]) if digits else ""
