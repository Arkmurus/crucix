"""ARIA registry coverage vault — what we can look up, and whether it is LIVE.

R-F2863.

WHY
───
`capability_manifest` answers "which jurisdictions have an adapter". It cannot
answer "is that adapter actually working right now". A source that is REGISTERED
but DEAD looks identical to one that works — and it reaches a customer as a
confident empty result rather than an honest data gap. That is a false clean
about our own capability, which is the one kind this platform cannot afford.

THE HONESTY RULE
────────────────
Liveness is TRI-STATE and defaults to UNPROVEN:

    live is True   -> we OBSERVED a successful lookup (timestamp is the evidence)
    live is False  -> we OBSERVED failures with no intervening success
    live is None   -> never observed. NOT "probably fine".

Being configured is not evidence of working. Nothing in this module upgrades a
source to live without an observation.

WHY OBSERVED, NOT PINGED
────────────────────────
Liveness is recorded from REAL `lookup_entity` calls rather than a synthetic
ping. A synthetic probe measures whether a health URL answers; this measures
whether the thing customers actually depend on returned data. It also costs
nothing extra — the call already happened.

An EMPTY result is deliberately neither: a registry that correctly answers "no
such company" is WORKING, so counting it as a failure would suspend a healthy
source — but it did not prove liveness either, so it must not mark it live.
"""
from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger("aria.registry_coverage")

# Durable, no TTL — §7: knowledge does not expire.
_KEY = "crucix:aria:registry:coverage"

# Jurisdictions served OUTSIDE the adapter dispatch table, so they are not
# reported as uncovered. GB has a dedicated Companies House branch in
# dd_orchestrator._run_identity rather than an entry in registry_adapters.
_COVERED_ELSEWHERE = {"GB": "companies_house"}

_VALID_OUTCOMES = ("success", "error", "empty")

# Bound every store touch: this surface must stay answerable when the store is sick.
_READ_TIMEOUT_S = float(__import__("os").getenv("ARIA_REGISTRY_COVERAGE_TIMEOUT_S", "3") or 3)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


async def _load() -> dict | None:
    """STRICT read. Returns None when the store could not be read.

    Deliberately strict: `get_json()` collapses a StoreReadError to None, the
    caller reads that as "empty", writes it back, and WIPES the durable history
    — the clobber class behind R-F2664, R-F2852 and R-F2854. Here the caller
    must be able to tell "no data yet" ({}) from "could not read" (None).
    """
    import asyncio

    from . import redis_store as rs
    try:
        # Hard-bounded. A wedged state_store must not hang a reader — this
        # surface is meant to be queryable exactly when things are going wrong,
        # and the event-loop starvation history makes an unbounded await here a
        # foot-gun. A timeout is indistinguishable from any other read failure:
        # both mean "could not read", which degrades to unproven, never to live.
        data = await asyncio.wait_for(rs.get_json_strict(_KEY), timeout=_READ_TIMEOUT_S)
    except Exception as exc:
        logger.warning("[registry_coverage] state read deferred, skipping write: %s", exc)
        return None
    return data if isinstance(data, dict) else {}


async def record_outcome(iso2: str, adapter: str, outcome: str) -> bool:
    """Record what a real registry lookup did. Returns True if persisted.

    Never raises — a bookkeeping failure must not break a DD run.
    """
    iso2 = (iso2 or "").upper().strip()
    if not iso2 or outcome not in _VALID_OUTCOMES:
        return False

    state = await _load()
    if state is None:
        return False        # transient read failure -> SKIP the write, never clobber

    entry = dict(state.get(iso2) or {})
    entry["adapter"] = adapter or entry.get("adapter") or ""
    entry["last_seen_at"] = _now_iso()
    if outcome == "success":
        entry["last_success_at"] = _now_iso()
        entry["consecutive_failures"] = 0
    elif outcome == "error":
        entry["last_failure_at"] = _now_iso()
        entry["consecutive_failures"] = int(entry.get("consecutive_failures") or 0) + 1
    else:                                   # "empty" — working, but proves nothing
        entry["last_empty_at"] = _now_iso()
        entry.setdefault("consecutive_failures", 0)
    entry["observations"] = int(entry.get("observations") or 0) + 1

    state[iso2] = entry
    try:
        import asyncio as _a
        from . import redis_store as rs
        await _a.wait_for(rs.set_json(_KEY, state), timeout=_READ_TIMEOUT_S)
        return True
    except Exception as exc:
        logger.warning("[registry_coverage] persist failed: %s", exc)
        return False


def _status_for(entry: dict) -> tuple[bool | None, str]:
    """Derive (live, status) from observations only. Never assumes."""
    if int(entry.get("consecutive_failures") or 0) > 0:
        return False, "failing"
    if entry.get("last_success_at"):
        return True, "live"
    return None, "unproven"


async def coverage() -> dict[str, Any]:
    """Full inventory: every jurisdiction, its adapter, and its observed liveness.

    Also reports what is NOT covered, so "what else can we explore" is answerable
    from data rather than from memory.
    """
    from . import registry_adapters as ra

    observed = await _load()
    if observed is None:
        observed = {}       # rendering a read failure as "no observations" is safe:
                            # it degrades to unproven, never to a false live claim

    jurisdictions: dict[str, dict] = {}
    for iso2, fn in sorted(ra._DISPATCH.items()):
        adapter_name = getattr(fn, "__name__", "").removeprefix("_lookup_")
        entry = dict(observed.get(iso2) or {})
        live, status = _status_for(entry)
        recorded_adapter = entry.get("adapter") or ""
        try:
            reg_status = ra.RegistryStatus.for_adapter(recorded_adapter).value \
                if recorded_adapter else None
        except Exception:
            reg_status = None
        jurisdictions[iso2] = {
            "adapter": recorded_adapter or adapter_name,
            "registry_status": reg_status,
            "live": live,
            "status": status,
            "observations": int(entry.get("observations") or 0),
            "consecutive_failures": int(entry.get("consecutive_failures") or 0),
            "last_success_at": entry.get("last_success_at"),
            "last_failure_at": entry.get("last_failure_at"),
        }

    for iso2, adapter_name in _COVERED_ELSEWHERE.items():
        if iso2 not in jurisdictions:
            entry = dict(observed.get(iso2) or {})
            live, status = _status_for(entry)
            jurisdictions[iso2] = {
                "adapter": adapter_name, "registry_status": None,
                "live": live, "status": status,
                "observations": int(entry.get("observations") or 0),
                "consecutive_failures": int(entry.get("consecutive_failures") or 0),
                "last_success_at": entry.get("last_success_at"),
                "last_failure_at": entry.get("last_failure_at"),
            }

    manual_only = sorted(set(_hint_jurisdictions()) - set(jurisdictions))
    live_count = sum(1 for v in jurisdictions.values() if v["live"] is True)
    return {
        "jurisdictions": jurisdictions,
        "manual_only": manual_only,
        "summary": {
            "with_adapter": len(jurisdictions),
            "manual_only": len(manual_only),
            "live": live_count,
            "failing": sum(1 for v in jurisdictions.values() if v["live"] is False),
            # Named explicitly so nobody reads "not live" as "broken".
            "unproven": sum(1 for v in jurisdictions.values() if v["live"] is None),
        },
        "generated_at": _now_iso(),
    }


def _hint_jurisdictions() -> list[str]:
    """Every jurisdiction ARIA knows a registry for, covered or not."""
    try:
        from .dd_orchestrator import _NATIONAL_REGISTRY_HINTS
        return list(_NATIONAL_REGISTRY_HINTS)
    except Exception as exc:
        logger.debug("[registry_coverage] hint list unavailable: %s", exc)
        return []
