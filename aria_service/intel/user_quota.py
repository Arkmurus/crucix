"""Per-user rate-limit and daily cost cap.

Audit finding H3: the existing rate_limiter is GLOBAL (one RPM budget
shared across every caller) and the autonomous cost circuit breaker is
also global. A single user hammering the chat endpoint could consume
the entire Anthropic RPM budget and silently deny service to everyone
else, with no accountability or visibility.

This module adds a second, per-user layer:

  * a sliding-window request counter (per-user RPM)
  * a daily cost counter keyed by UTC day (per-user USD spend)
  * env-var caps:
        ARIA_USER_RPM_CAP            (default 30)
        ARIA_USER_DAILY_COST_USD_CAP (default 5.00)
  * an allow-list for operators who should bypass caps entirely
        ARIA_USER_QUOTA_UNLIMITED="antonio,ari,ops"

Both counters have an in-memory fast path AND a Redis mirror (via the
existing intel.redis_store helper). If Redis is unavailable the
in-memory counters still enforce the cap on this process — so the cap
degrades gracefully instead of failing open.

Usage pattern from the route handler (chat_ep / chat_stream_ep):

    allowed, reason = await user_quota.check(user_id)
    if not allowed:
        raise HTTPException(status_code=429, detail=reason)
    # ... run the chat ...
    await user_quota.record(user_id, cost_usd=result_cost)

This is intentionally lightweight — no database, no billing system.
It is a DoS / cost-runaway guardrail, not a commercial metering layer.
"""
from __future__ import annotations

import logging
import os
import time
from collections import deque

from . import redis_store as rs

logger = logging.getLogger("aria.user_quota")


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)) or default)
    except (TypeError, ValueError):
        return default


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)) or default)
    except (TypeError, ValueError):
        return default


# Defaults are conservative but not draconian. 30 RPM = 1 every 2s, which
# is plenty for interactive chat but hard to sustain programmatically.
USER_RPM_CAP = _env_int("ARIA_USER_RPM_CAP", 30)
USER_DAILY_COST_CAP_USD = _env_float("ARIA_USER_DAILY_COST_USD_CAP", 5.00)

# Sliding window for RPM check.
_RPM_WINDOW_S = 60.0
# In-memory: deque of (epoch_s) timestamps per user for RPM.
_rpm_hits: dict[str, deque] = {}
# In-memory: (utc_day, spent_usd) tuple per user for daily cost.
_cost_mem: dict[str, tuple[str, float]] = {}

_USER_RPM_KEY_FMT = "crucix:user:rpm:{user}:{hour}"
_USER_COST_KEY_FMT = "crucix:user:cost:{user}:{date}"


def _utc_day() -> str:
    return time.strftime("%Y-%m-%d", time.gmtime())


def _unlimited_users() -> set[str]:
    raw = os.getenv("ARIA_USER_QUOTA_UNLIMITED", "") or ""
    return {s.strip().lower() for s in raw.split(",") if s.strip()}


def is_unlimited(user_id: str) -> bool:
    if not user_id:
        return False
    uid = user_id.strip().lower()
    trusted = _unlimited_users()
    if uid in trusted:
        return True
    for t in trusted:
        if uid.startswith(t + "_") or uid.startswith(t + "-"):
            return True
    return False


def _mem_rpm_count(user_id: str) -> int:
    now = time.time()
    dq = _rpm_hits.get(user_id)
    if dq is None:
        return 0
    # Drop entries older than the window.
    while dq and dq[0] < now - _RPM_WINDOW_S:
        dq.popleft()
    return len(dq)


def _mem_rpm_add(user_id: str) -> int:
    now = time.time()
    dq = _rpm_hits.setdefault(user_id, deque())
    dq.append(now)
    while dq and dq[0] < now - _RPM_WINDOW_S:
        dq.popleft()
    return len(dq)


def _mem_cost_get(user_id: str) -> float:
    today = _utc_day()
    entry = _cost_mem.get(user_id)
    if not entry or entry[0] != today:
        return 0.0
    return entry[1]


def _mem_cost_add(user_id: str, usd: float) -> float:
    today = _utc_day()
    entry = _cost_mem.get(user_id)
    if not entry or entry[0] != today:
        _cost_mem[user_id] = (today, max(0.0, usd))
        return _cost_mem[user_id][1]
    _cost_mem[user_id] = (today, entry[1] + max(0.0, usd))
    return _cost_mem[user_id][1]


async def check(user_id: str) -> tuple[bool, str]:
    """Return (allowed, reason). Reason is empty when allowed."""
    if not user_id or user_id == "anon":
        # Anon users still get quota — treat as a shared "anon" bucket.
        user_id = "__anon__"

    if is_unlimited(user_id):
        return True, ""

    # ── RPM sliding window (in-memory authoritative, Redis informational) ──
    mem_count = _mem_rpm_count(user_id)
    if mem_count >= USER_RPM_CAP:
        logger.warning(
            "[user_quota] user=%s RPM cap hit (%d/%d in-mem)",
            user_id, mem_count, USER_RPM_CAP,
        )
        return False, f"per-user rate cap reached ({USER_RPM_CAP}/min) — retry in ~{_RPM_WINDOW_S:.0f}s"

    # ── Daily cost ──
    mem_spent = _mem_cost_get(user_id)
    redis_spent = 0.0
    try:
        val = await rs.get(_USER_COST_KEY_FMT.format(user=user_id, date=_utc_day()))
        redis_spent = float(val or "0")
    except Exception:
        pass
    spent = max(mem_spent, redis_spent)
    if spent >= USER_DAILY_COST_CAP_USD:
        logger.warning(
            "[user_quota] user=%s daily cost cap hit ($%.4f / $%.2f)",
            user_id, spent, USER_DAILY_COST_CAP_USD,
        )
        return False, f"per-user daily cost cap reached (${USER_DAILY_COST_CAP_USD:.2f}) — resets 00:00 UTC"

    return True, ""


async def register_request(user_id: str) -> None:
    """Call immediately after check() returns allowed — bumps the RPM counter."""
    if not user_id or user_id == "anon":
        user_id = "__anon__"
    if is_unlimited(user_id):
        return
    _mem_rpm_add(user_id)
    try:
        hour = int(time.time() // 3600)
        key = _USER_RPM_KEY_FMT.format(user=user_id, hour=hour)
        new_count = await rs.incr(key)
        if new_count == 1:
            await rs.expire(key, 3600)
    except Exception as e:
        logger.debug("user_quota: Redis RPM incr failed (non-fatal): %s", e)


async def record_cost(user_id: str, cost_usd: float) -> None:
    """Record a chat cost against the user's daily budget."""
    if not user_id or user_id == "anon":
        user_id = "__anon__"
    if is_unlimited(user_id) or cost_usd <= 0:
        return
    _mem_cost_add(user_id, cost_usd)
    try:
        key = _USER_COST_KEY_FMT.format(user=user_id, date=_utc_day())
        if hasattr(rs, "incrbyfloat"):
            await rs.incrbyfloat(key, cost_usd)
        else:
            existing = float(await rs.get(key) or "0")
            await rs.set(key, f"{existing + cost_usd:.6f}")
        await rs.expire(key, 48 * 3600)
    except Exception as e:
        logger.debug("user_quota: Redis cost record failed (non-fatal): %s", e)


async def get_user_state(user_id: str) -> dict:
    """Expose current quota state for debugging / /quota endpoint."""
    if not user_id:
        user_id = "__anon__"
    # R-F996 — wire to brain
    from .engine_wiring import wire_success
    wire_success(
        module="user_quota",
        summary="Get User State",
        source_id="user_quota:R-F996",
    )

    return {
        "user": user_id,
        "unlimited": is_unlimited(user_id),
        "rpm_used": _mem_rpm_count(user_id),
        "rpm_cap": USER_RPM_CAP,
        "daily_cost_usd": round(_mem_cost_get(user_id), 6),
        "daily_cost_cap_usd": USER_DAILY_COST_CAP_USD,
    }
