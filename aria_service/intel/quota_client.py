"""R-F2835 — ask the web tier whether this user may run another DD.

WHY THIS EXISTS. The per-tier DD quota (5/month on free, lib/billing/tiers.mjs) was
enforced ONLY on the web path (server.mjs:3546). A DD triggered from CHAT runs as a
tool inside this process and never traverses that route, so it consumed nothing — a
grep of aria_service/ for ddRunsPerMonth|dd_runs_per_month|dd_quota returns ZERO
hits. A free user capped at 50 messages/day could trigger up to 50 DD runs per day:
ten times the MONTHLY cap, every day. Revenue leak, and §17 cost-cap exposure.

WHY THE BRAIN ASKS RATHER THAN COUNTS. Billing belongs to the web tier, which owns
users, tiers and Stripe. A second counter here would be a second source of truth and
would drift — the same failure that produced the nav-entitlement drift in R-F2822.

FAIL-OPEN, LOUDLY. If the web tier cannot be reached, the run is ALLOWED and
reported as uncounted. Denying a paying customer because an internal hop hiccuped is
worse than one uncounted run, and the §17 $300/mo cap is the hard backstop. But the
degradation is never silent (§21a): it is logged and surfaced in the return value.
"""
from __future__ import annotations

import logging
import os
from typing import Any

logger = logging.getLogger("aria.quota_client")

_TIMEOUT_S = 5.0


class QuotaExceeded(Exception):
    """Raised when the web tier reports the caller is over their plan limit."""

    def __init__(self, reason: str, current: int = 0, cap: int = 0):
        super().__init__(reason)
        self.reason = reason
        self.current = current
        self.cap = cap


async def consume_dd_quota(user_id: str, *, kind: str = "ddRun") -> dict[str, Any]:
    """Consume one unit of `kind` for `user_id`. Returns the verdict dict.

    Raises nothing on infrastructure failure — see the fail-open note above. The
    caller decides what to do with `allowed=False`.
    """
    if not user_id:
        # System / autonomous runs have no user. They are governed by the §17 cost
        # cap and the autonomy guardrails, not by a customer plan.
        return {"allowed": True, "exempt": "no_user_id"}

    base = (os.getenv("ARIA_WEB_INTERNAL_URL", "") or "").rstrip("/")
    token = os.getenv("ARIA_INTERNAL_TOKEN", "") or os.getenv("ARIA_API_TOKEN", "")
    if not base or not token:
        logger.warning(
            "[R-F2835] quota check skipped: ARIA_WEB_INTERNAL_URL/ARIA_INTERNAL_TOKEN "
            "not configured — DD runs are NOT being counted against plan limits",
        )
        return {"allowed": True, "degraded": True, "reason": "quota service not configured"}

    try:
        import httpx  # local import: keeps module import cheap at boot

        async with httpx.AsyncClient(timeout=_TIMEOUT_S) as client:  # no-breaker: internal 6PN hop, fail-open
            resp = await client.post(
                f"{base}/api/internal/quota/consume",
                json={"user_id": user_id, "kind": kind},
                headers={"Authorization": f"Bearer {token}"},
            )
        if resp.status_code != 200:
            logger.warning(
                "[R-F2835] quota service returned HTTP %s — allowing uncounted",
                resp.status_code,
            )
            return {"allowed": True, "degraded": True,
                    "reason": f"quota service HTTP {resp.status_code}"}
        data = resp.json()
    except Exception as e:  # noqa: BLE001 — never fail a DD on a telemetry-class hop
        logger.warning("[R-F2835] quota check failed (%s) — allowing uncounted", e)
        return {"allowed": True, "degraded": True, "reason": f"quota service error: {e}"}

    # Never infer allowance from an absent field: an unparseable answer is degraded,
    # not a silent pass.
    if not isinstance(data, dict) or "allowed" not in data:
        logger.warning("[R-F2835] quota service returned an unrecognised body — allowing uncounted")
        return {"allowed": True, "degraded": True, "reason": "unrecognised quota response"}
    return data
