"""R-F2178 — per-limb liveness registry (living-organism proprioception).

The brain's REACTIVE nerves are wired: every limb (aria-wa, aria-web, aria-searxng)
reports FAILURES to /api/aria/brain/signal. But no limb reports that it is ALIVE,
so the brain cannot affirmatively answer "is the WA / web / search limb up right
now?" — the §25.3 per-channel health requirement. It only learns a limb is dark
when a user request happens to fail.

This registry closes that gap. Each limb POSTs a periodic heartbeat
(/api/aria/liveness/beat); the brain stores the latest beat per limb, marks a limb
STALE when its beat is older than the limb's expected interval, and records a
coder-visible capability gap so a dark limb self-heals (or alerts) instead of
being discovered only on a failed request.

Pure-ish + best-effort: never raises into a caller; degrades to empty when Redis
is unavailable.
"""
from __future__ import annotations

import time

from . import redis_store as rs

_KEY_PREFIX = "crucix:aria:liveness:"
# Floor for staleness: a limb is stale when its last beat is older than
# max(this, 3 × the limb's declared interval). 3× tolerates one missed beat plus
# jitter before alarming.
_DEFAULT_STALE_AFTER_S = 600  # 10 min
# Keep a dead limb's key for a day so a permanently-down limb eventually clears,
# long after stale-detection has already fired.
_BEAT_TTL_S = 86_400


async def record_beat(limb: str, status: str = "alive", interval_s: int = 0,
                      meta: dict | None = None) -> dict:
    """Store the latest heartbeat for ``limb``. ``interval_s`` is how often the
    limb intends to beat (drives the staleness threshold). Best-effort."""
    name = (limb or "").strip().lower()[:40] or "unknown"
    entry = {
        "limb": name,
        "status": (status or "alive").strip()[:40] or "alive",
        "ts": time.time(),
        "interval_s": int(interval_s or 0),
        "meta": meta if isinstance(meta, dict) else {},
    }
    try:
        await rs.set_json(_KEY_PREFIX + name, entry, ex=_BEAT_TTL_S)
    except Exception:  # noqa: BLE001 — never break the caller
        pass
    return entry


def _stale_after_s(entry: dict) -> float:
    iv = 0.0
    try:
        iv = float(entry.get("interval_s") or 0)
    except (TypeError, ValueError):
        iv = 0.0
    return max(_DEFAULT_STALE_AFTER_S, iv * 3) if iv > 0 else _DEFAULT_STALE_AFTER_S


async def get_liveness(now: float | None = None) -> dict:
    """Return per-limb liveness {limbs: {name: {...alive, stale, age_s}}}.
    A limb is alive iff its last beat is fresh AND its reported status is 'alive'."""
    n = float(now if now is not None else time.time())
    limbs: dict[str, dict] = {}
    try:
        keys = await rs.scan_keys(_KEY_PREFIX + "*") or []
        for k in keys:
            e = await rs.get_json(k)
            if not isinstance(e, dict):
                continue
            try:
                age = n - float(e.get("ts", 0) or 0)
            except (TypeError, ValueError):
                continue
            stale = age > _stale_after_s(e)
            limbs[str(e.get("limb") or k)] = {
                **e,
                "age_s": round(age, 1),
                "stale": stale,
                "alive": (not stale) and e.get("status", "alive") == "alive",
            }
    except Exception:  # noqa: BLE001
        pass
    return {"limbs": limbs, "checked_at": n}


async def check_stale_and_gap(now: float | None = None) -> list[str]:
    """Record a coder-visible capability gap for each limb whose heartbeat has gone
    stale, so a dark limb is acted on (not discovered on a failed request). Returns
    the stale limb names. Called by the brain's liveness watchdog. Never raises."""
    stale: list[str] = []
    try:
        from .engine_wiring import wire_failure, wire_success  # §21a brain wiring
        live = await get_liveness(now=now)
        for name, e in (live.get("limbs") or {}).items():
            if e.get("stale"):
                stale.append(name)
        if stale:
            from . import capability_gaps as _cg
            for name in stale:
                age = (live["limbs"][name] or {}).get("age_s", "?")
                await _cg.record_gap(
                    gap_type="timeout",
                    detail=(f"limb '{name}' heartbeat is STALE ({age}s since last "
                            f"beat) — it stopped reporting liveness to the brain "
                            f"(crashed, wedged, or network-partitioned). Check the "
                            f"{name} app health and restart/redeploy if down."),
                    source="liveness_watchdog",
                    severity="HIGH",
                    title=f"limb down: {name}",
                )
            # §21a/§21d failure branch → brain (canonical wiring token).
            wire_failure(module="liveness",
                         detail=f"{len(stale)} limb(s) stale: {', '.join(stale)}",
                         gap_type="timeout", source="liveness_watchdog")
        else:
            # §21a success branch → metric: the organism's limbs are all alive.
            n_limbs = len((live.get("limbs") or {}))
            wire_success(module="liveness",
                         summary=f"liveness sweep: all {n_limbs} limb(s) alive")
    except Exception:  # noqa: BLE001
        pass
    return stale
