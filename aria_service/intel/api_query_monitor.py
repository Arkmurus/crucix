"""api_query_monitor — public API query-pattern anomaly detection
(R-F83, 2026-05-09).

Why this module exists
──────────────────────
Per peer review of the architecture doc: 'Once ENABLE_PUBLIC_API=1,
paid keys can probe ARIA's knowledge gaps systematically. Query-
pattern analysis (per-key entity-coverage heatmap, anomalous drilling
on a single counterparty) is needed before the API is enterprise-ready.'

Three patterns that signal probing rather than legitimate use:
  1. Drilling — single API key submits 10+ queries about the same
     counterparty entity in a tight time window
  2. Coverage probing — single key sweeps systematically through ARIA's
     knowledge surface (asks about 20+ different entities in one
     session, each query at low depth — looking for which ones ARIA
     knows about)
  3. Refusal mining — single key submits queries known to trip
     constitutional refusals, presumably to learn ARIA's refusal
     vocabulary (related to PI-REFUSAL-INVERSION from R-F80)

Recording happens at the public API gate (lib/api_keys/routes.mjs on
seenode) — but the analysis lives here so the operator gets a unified
view via /api/aria/security/api-monitor/<key>.

This module is the FLY-SIDE analyser; the SEENODE-SIDE recorder writes
into Redis at the same key the analyser reads. They communicate
through Redis, no direct call.

Public API
──────────
    record_query(key_id, query, entity_extracted=None,
                  refused=False) -> None
        Log one query event. Redis-backed sliding window (last 24h).

    analyze_key(key_id, window_hours=24) -> dict
        Compute the three pattern scores for a key over the window.

    high_risk_keys(window_hours=24, threshold=0.6) -> list[dict]
        Cross-key sweep: which keys are scoring above threshold on
        any pattern? For the operator dashboard.
"""
from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger("aria.api_query_monitor")

# Redis key shape — per-key sliding window of recent events
_REDIS_KEY_PREFIX = "crucix:aria:apimon:"
_REDIS_KEYS_INDEX = "crucix:aria:apimon:keys"  # set of all key_ids seen

# Sliding window TTL — 48h on the per-key list, 7d on the index
_EVENT_TTL_SECONDS = 48 * 3600
_INDEX_TTL_SECONDS = 7 * 24 * 3600

# Pattern thresholds
_DRILLING_THRESHOLD       = 10   # queries per entity per window
_COVERAGE_PROBE_THRESHOLD = 20   # distinct entities per window
_REFUSAL_MINING_RATE      = 0.25 # % of queries refused

# Rough entity extractor — uppercase Token Token (Title Case 2-4 words).
# Production version would use the existing entity_resolver but this
# avoids a heavyweight dep on the seenode hot path.
_ENTITY_RE = re.compile(
    r"\b([A-Z][A-Za-z]{2,}(?:\s+[A-Z][A-Za-z]{2,}){1,3})\b"
)


def _extract_entity(query: str) -> str | None:
    """Pull the first capitalised proper-noun phrase from the query.

    Skips question-starting words (What/Where/Who/Is) so 'Who is Wagner Group'
    extracts 'Wagner Group' not 'Who is'.
    """
    if not query:
        return None
    text = query.strip()
    text = re.sub(r"^(?:Who|What|Where|When|Why|How|Is|Are|Do|Does|Can|Tell|Give)\s+(?:is|are|the|me|a|an)?\s*",
                  "", text, flags=re.IGNORECASE)
    m = _ENTITY_RE.search(text)
    return m.group(1) if m else None


async def record_query(
    key_id: str,
    query: str,
    *,
    entity_extracted: str | None = None,
    refused: bool = False,
) -> None:
    """Record one query event for later analysis. Best-effort; never raises."""
    if not key_id or not query:
        return
    try:
        from . import redis_store as rs
    except Exception:
        return
    entity = entity_extracted or _extract_entity(query)
    event = {
        "ts":       datetime.now(timezone.utc).isoformat(),
        "query":    query[:500],
        "entity":   entity,
        "refused":  bool(refused),
    }
    rkey = f"{_REDIS_KEY_PREFIX}{key_id}"
    try:
        existing = await rs.get_json(rkey)
        if not isinstance(existing, list):
            existing = []
        existing.append(event)
        # Cap the per-key list at 500 events to bound memory
        if len(existing) > 500:
            existing = existing[-500:]
        await rs.set_json(rkey, existing, ex=_EVENT_TTL_SECONDS)
        # Track key in the index set so the cross-key sweep finds it
        idx = await rs.get_json(_REDIS_KEYS_INDEX)
        if not isinstance(idx, list):
            idx = []
        if key_id not in idx:
            idx.append(key_id)
            # Keep the index modest — drop old ids if it grows past 1000
            if len(idx) > 1000:
                idx = idx[-1000:]
            await rs.set_json(_REDIS_KEYS_INDEX, idx, ex=_INDEX_TTL_SECONDS)
    except Exception as e:
        logger.debug("api_query_monitor record failed: %s", e)


async def analyze_key(
    key_id: str,
    *,
    window_hours: int = 24,
) -> dict[str, Any]:
    """Score one API key against the three patterns over the window."""
    try:
        from . import redis_store as rs
    except Exception:
        return {"ok": False, "error": "redis unavailable"}
    rkey = f"{_REDIS_KEY_PREFIX}{key_id}"
    try:
        events = await rs.get_json(rkey)
    except Exception as e:
        return {"ok": False, "error": str(e)}
    if not isinstance(events, list):
        return {
            "key_id":     key_id,
            "window_hours": window_hours,
            "events":     0,
            "patterns":   {},
            "narrative":  f"No events recorded for {key_id} in the last {window_hours}h.",
        }

    # Filter to window
    cutoff = datetime.now(timezone.utc).timestamp() - (window_hours * 3600)
    recent: list[dict[str, Any]] = []
    for e in events:
        try:
            ts = datetime.fromisoformat(e["ts"]).timestamp()
            if ts >= cutoff:
                recent.append(e)
        except Exception:
            continue

    n = len(recent)
    if n == 0:
        return {
            "key_id":     key_id,
            "window_hours": window_hours,
            "events":     0,
            "patterns":   {},
            "narrative":  f"No events for {key_id} in the {window_hours}h window.",
        }

    # PATTERN 1 — DRILLING: max queries about a single entity
    entity_counts: dict[str, int] = {}
    for e in recent:
        ent = e.get("entity")
        if ent:
            entity_counts[ent] = entity_counts.get(ent, 0) + 1
    top_entity = max(entity_counts, key=entity_counts.get) if entity_counts else None
    drilling_count = entity_counts.get(top_entity, 0) if top_entity else 0
    drilling_score = min(drilling_count / _DRILLING_THRESHOLD, 1.0)

    # PATTERN 2 — COVERAGE PROBE: distinct entities asked about
    distinct_entities = len(entity_counts)
    coverage_score = min(distinct_entities / _COVERAGE_PROBE_THRESHOLD, 1.0)

    # PATTERN 3 — REFUSAL MINING: refusal rate
    refused = sum(1 for e in recent if e.get("refused"))
    refusal_rate = refused / n if n else 0.0
    refusal_mining_score = min(refusal_rate / _REFUSAL_MINING_RATE, 1.0) if refusal_rate >= _REFUSAL_MINING_RATE else 0.0

    composite = max(drilling_score, coverage_score, refusal_mining_score)

    flags: list[str] = []
    if drilling_score >= 1.0:
        flags.append(f"DRILLING ({drilling_count} queries about '{top_entity}')")
    if coverage_score >= 1.0:
        flags.append(f"COVERAGE_PROBE ({distinct_entities} distinct entities)")
    if refusal_mining_score >= 1.0:
        flags.append(f"REFUSAL_MINING ({refused}/{n} = {refusal_rate*100:.0f}% refused)")

    if flags:
        narrative = f"{key_id} HIGH-RISK: " + "; ".join(flags) + "."
    elif composite >= 0.6:
        narrative = (
            f"{key_id} elevated activity (drilling={drilling_score:.2f}, "
            f"coverage={coverage_score:.2f}, refusal={refusal_mining_score:.2f})."
        )
    else:
        narrative = f"{key_id} normal activity ({n} events, no pattern flags)."

    return {
        "key_id":         key_id,
        "window_hours":   window_hours,
        "events":         n,
        "distinct_entities": distinct_entities,
        "top_entity":     top_entity,
        "top_entity_count": drilling_count,
        "refused_count":  refused,
        "refusal_rate":   round(refusal_rate, 3),
        "patterns": {
            "drilling":         round(drilling_score, 3),
            "coverage_probe":   round(coverage_score, 3),
            "refusal_mining":   round(refusal_mining_score, 3),
        },
        "composite_score":   round(composite, 3),
        "high_risk":         composite >= 1.0,
        "narrative":         narrative,
    }


async def high_risk_keys(
    *,
    window_hours: int = 24,
    threshold: float = 0.6,
) -> dict[str, Any]:
    """Cross-key sweep — find every key scoring above threshold."""
    try:
        from . import redis_store as rs
    except Exception:
        return {"ok": False, "error": "redis unavailable"}
    try:
        idx = await rs.get_json(_REDIS_KEYS_INDEX)
    except Exception as e:
        return {"ok": False, "error": str(e)}
    if not isinstance(idx, list):
        return {"keys_checked": 0, "high_risk": []}

    high_risk: list[dict[str, Any]] = []
    for key_id in idx:
        report = await analyze_key(key_id, window_hours=window_hours)
        if report.get("composite_score", 0) >= threshold:
            high_risk.append(report)

    return {
        "keys_checked":  len(idx),
        "window_hours":  window_hours,
        "threshold":     threshold,
        "high_risk":     sorted(high_risk, key=lambda r: -r["composite_score"]),
    }


def summary() -> dict[str, Any]:
    return {
        "module":     "api_query_monitor",
        "patterns":   ["drilling", "coverage_probe", "refusal_mining"],
        "thresholds": {
            "drilling_per_entity": _DRILLING_THRESHOLD,
            "coverage_distinct":   _COVERAGE_PROBE_THRESHOLD,
            "refusal_rate":        _REFUSAL_MINING_RATE,
        },
    }
