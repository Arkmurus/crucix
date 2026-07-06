"""continuous_update — ensures every domain re-cycles within its
max-staleness window (R-F90, 2026-05-09).

Why this module exists
──────────────────────
Phase 2 of the independence roadmap. ARIA's existing autonomous engine
fires tasks on cron schedules. R-F88 tracks per-domain freshness.
R-F89 maps coverage to domains × jurisdictions. This module is the
glue: it inspects R-F88's freshness state, identifies stale domains,
and surfaces them to the autonomous engine as priority targets for
the next cycle.

The contract: every domain must see at least one fresh signal per
its max-staleness window. Stale domains get a `priority_targets`
record that the autonomous engine reads when picking the next task.

This is the "ARIA's brain sees, hears, and knows everything" mechanism
— continuously detecting blind spots and self-driving toward them.

How it works
────────────
1. Read freshness from learning_progress (R-F88) — get stale_domains()
2. Read coverage gaps from coverage_heatmap (R-F89) — get gap_targets()
3. Compose a priority list: combine staleness urgency + coverage gap
4. Write the priority list to Redis at a known key the autonomous
   engine reads on every poll cycle (60s)
5. The autonomous engine's next task selection prefers tasks tagged
   for stale-or-gap domains over its default round-robin

This module is RECOMMEND-ONLY — it doesn't fire tasks itself. The
autonomous engine retains full control over what runs when. This
preserves the existing safety gates (cost cap, rate limit, dry-run).

Public API
──────────
    compute_priorities() -> dict
    write_priorities() -> dict   — recommend-and-persist combo
    read_priorities() -> dict    — what the autonomous engine sees
    summary() -> dict
"""
from __future__ import annotations
from .engine_wiring import wire_failure

import logging
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger("aria.continuous_update")

_REDIS_KEY = "crucix:aria:continuous_update:priorities"
_TTL_SECONDS = 24 * 3600


async def _redis():
    from . import redis_store as rs
    return rs


def _urgency_score(hours_since_refresh: float | None,
                    max_staleness_hours: int) -> float:
    """Score in [0, 1+]; >1 means past staleness window.

    Used to rank stale domains: a domain that is 2× past its window
    is more urgent than one that is 1.1× past.
    """
    if hours_since_refresh is None or max_staleness_hours <= 0:
        return 1.5  # never refreshed = high urgency
    return round(hours_since_refresh / max_staleness_hours, 3)


async def compute_priorities(
    *,
    max_priorities: int = 30,
) -> dict[str, Any]:
    """Build the priority list without persisting.

    Returns:
        {
          "computed_at": ISO timestamp,
          "stale_domains":  [...],   # from R-F88
          "coverage_gaps":  [...],   # from R-F89
          "priorities":     [...],   # ranked targets for next cycles
        }
    """
    from . import learning_progress as _lp
    from . import coverage_heatmap as _ch

    stale_records: list[dict[str, Any]] = []
    try:
        stale_records = await _lp.stale_domains()
    except Exception as e:
        logger.warning("continuous_update: stale_domains failed: %s", e)

    coverage: dict[str, Any] = {}
    try:
        # Coverage heatmap is expensive (O(domains × jurisdictions ×
        # corpus)) so we only build it once per priority recompute.
        coverage = await _ch.build_heatmap()
    except Exception as e:
        logger.warning("continuous_update: build_heatmap failed: %s", e)

    gaps = _ch.gap_targets(coverage, max_targets=50) if coverage else []

    # Combine: a domain that is BOTH stale AND has coverage gaps gets
    # the highest priority. Build a domain → priority score map.
    domain_priorities: dict[str, dict[str, Any]] = {}

    # Layer 1 — staleness contributes urgency
    for s in stale_records:
        d = s.get("domain") or ""
        if not d:
            continue
        urgency = _urgency_score(
            s.get("hours_since_refresh"),
            s.get("max_staleness_hours") or 168,
        )
        domain_priorities.setdefault(d, {
            "domain":              d,
            "urgency":             0.0,
            "coverage_gap_score":  0.0,
            "reasons":             [],
        })
        domain_priorities[d]["urgency"] = max(
            domain_priorities[d]["urgency"], urgency
        )
        domain_priorities[d]["reasons"].append(
            f"stale {s.get('hours_since_refresh')}h vs window {s.get('max_staleness_hours')}h"
        )

    # Layer 2 — coverage gaps contribute structural urgency
    for g in gaps:
        d = g.get("domain") or ""
        if not d:
            continue
        gap_score = g.get("priority", 0.0)
        domain_priorities.setdefault(d, {
            "domain":              d,
            "urgency":             0.0,
            "coverage_gap_score":  0.0,
            "reasons":             [],
        })
        domain_priorities[d]["coverage_gap_score"] = max(
            domain_priorities[d]["coverage_gap_score"], gap_score
        )
        domain_priorities[d]["reasons"].append(
            f"coverage gap × {g.get('jurisdiction', '?')} ({g.get('tier', '?')})"
        )

    # Composite score = urgency (max 2.0) + coverage_gap (max 1.5).
    # No normalisation — high absolute scores let the autonomous engine
    # see "this is genuinely urgent" vs "incremental".
    composed: list[dict[str, Any]] = []
    for d, rec in domain_priorities.items():
        rec["composite"] = round(rec["urgency"] + rec["coverage_gap_score"], 3)
        rec["narrative"] = (
            f"{d}: urgency {rec['urgency']:.2f} + gap {rec['coverage_gap_score']:.2f}; "
            + "; ".join(rec["reasons"][:3])
            + ("..." if len(rec["reasons"]) > 3 else "")
        )
        composed.append(rec)
    composed.sort(key=lambda r: -r["composite"])

    return {
        "computed_at":      datetime.now(timezone.utc).isoformat(),
        "stale_count":      len(stale_records),
        "coverage_gaps":    len(gaps),
        "priorities":       composed[:max_priorities],
        "coverage_score":   coverage.get("coverage_score") if coverage else None,
    }


async def write_priorities(*, max_priorities: int = 30) -> dict[str, Any]:
    """Compute + persist to Redis. The autonomous engine reads from
    `_REDIS_KEY` on every poll cycle."""
    out = await compute_priorities(max_priorities=max_priorities)
    try:
        rs = await _redis()
        await rs.set_json(_REDIS_KEY, out, ex=_TTL_SECONDS)
    except Exception as e:
        logger.warning("continuous_update: redis write failed: %s", e)
        out["redis_write_error"] = str(e)
    return out


async def read_priorities() -> dict[str, Any]:
    """Read the most recent priority list. Returns empty list if never
    computed."""
    try:
        rs = await _redis()
        existing = await rs.get_json(_REDIS_KEY)
    except Exception as e:
        return {"ok": False, "error": str(e), "priorities": []}
    if not isinstance(existing, dict):
        return {"priorities": [], "computed_at": None}
    return existing


async def get_top_priority_domains(n: int = 5) -> list[str]:
    """Convenience: return the top N domain names. Used by the
    autonomous engine's task selector to prefer tasks tagged for
    these domains."""
    pr = await read_priorities()
    items = pr.get("priorities") or []
    # R-F996 — wire to brain
    from .engine_wiring import wire_success, wire_failure
    wire_success(
        module="continuous_update",
        summary="Get Top Priority Domains",
        source_id="continuous_update:R-F996",
    )

    return [it["domain"] for it in items[:n] if it.get("domain")]


def summary() -> dict[str, Any]:
    return {
        "module":  "continuous_update",
        "purpose": "max-staleness contract over autonomous engine",
        "data_sources": ["learning_progress (R-F88)", "coverage_heatmap (R-F89)"],
        "redis_key": _REDIS_KEY,
    }

# R-F2119 §21a — wire failure handler for continuous_update
try:
    wire_failure(module="continuous_update", detail="module shutdown",
                gap_type="engine_failure", source="continuous_update:shutdown")
except Exception:
    pass
