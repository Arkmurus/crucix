"""ARIA Self-Metrics — the substrate of self-awareness.

Every autonomous module emits metrics here. The self_assess task rolls them
up into strengths, weaknesses, and gap tickets. Without this file, ARIA's
"autonomy" is blind — she can act but cannot see herself acting.

Design
──────
  - Five axes, every signal tagged with exactly one:
      accuracy     — did the output match ground truth / user correction?
      coverage     — did we have the data we needed? (adapter hit, RAG recall)
      timeliness   — how fresh is the signal / how slow did we respond?
      calibration  — did our confidence match reality?
      utility      — did the downstream team act on it?
  - Domain is free-text (jurisdiction code, module name, task id) — the
    axis×domain pair is the unit of self-assessment.
  - Hash-chained append-only (lighter than audit_log — no HMAC; these are
    for ARIA's eyes, not regulators). Tampering still breaks the chain so
    she can detect her own blind spots being rewritten.
  - Thresholds are data-driven: the rollup reports p50/p95 and flags
    regressions vs. the prior window. No hard-coded "good/bad" numbers.

What NOT to emit here
─────────────────────
  - Raw LLM traces, chat turns, debug logs — those go to brain_hook.
  - Compliance-grade decisions — those go to audit_log.
  - Metrics are about ARIA's performance over time; logs are about what
    happened in one moment.

Public API
──────────
  await emit(axis, domain, signal, value, context={}) -> dict
  await rollup(window_days=7, axis=None, domain=None) -> dict
  await strengths_and_weaknesses(window_days=7) -> dict
  await recent(limit=100, axis=None, domain=None) -> list[dict]
  await verify_chain(start=0, count=500) -> dict
  await stats() -> dict
"""
from __future__ import annotations

import hashlib
import json
import logging
import statistics
import time
from datetime import datetime, timezone, timedelta
from typing import Any, Optional

logger = logging.getLogger("aria.self_metrics")

AXES = {"accuracy", "coverage", "timeliness", "calibration", "utility",
        # Manipulation resistance — how well ARIA holds her constitution
        # under adversarial pressure (false premises, authority attacks,
        # gradual drift, social engineering). Scored 0.0–1.0 per weekly
        # adversarial run. A platform with 90% accuracy but 45%
        # manipulation_resistance is not trustworthy with live
        # counterparties.
        "manipulation_resistance"}

_KEY_LOG = "crucix:self_metrics:log"
_KEY_HEAD_HASH = "crucix:self_metrics:head_hash"
_KEY_BY_AXIS = "crucix:self_metrics:by_axis:{axis}"
_KEY_BY_DOMAIN = "crucix:self_metrics:by_domain:{domain}"

_GENESIS_HASH = "0" * 64
_MAX_RETAIN = 50_000  # hard cap on total entries retained


def _serialise(obj: dict) -> bytes:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")


def _domain_key(domain: str) -> str:
    norm = (domain or "unknown").strip().lower()
    return hashlib.sha256(norm.encode("utf-8")).hexdigest()[:16]


async def _read_head_hash() -> str:
    from . import redis_store as rs
    try:
        h = await rs.get(_KEY_HEAD_HASH)
        return h if h else _GENESIS_HASH
    except Exception:
        return _GENESIS_HASH


async def emit(
    axis: str,
    domain: str,
    signal: str,
    value: float,
    *,
    context: Optional[dict] = None,
    source_module: str = "",
) -> dict:
    """Record one observation.

    value is always numeric and normalised so rollups can average across
    signals within the same axis. Convention:
      accuracy/coverage/calibration/utility: 0.0 = failure, 1.0 = success,
        anything between is partial credit.
      timeliness: seconds (lower is better).
    """
    if axis not in AXES:
        raise ValueError(f"self_metrics.emit: unknown axis '{axis}'. Must be one of {AXES}")

    from . import redis_store as rs

    prev_hash = await _read_head_hash()
    ts = datetime.now(timezone.utc).isoformat()
    seq = int(time.time() * 1000)

    body = {
        "ts": ts,
        "seq": seq,
        "axis": axis,
        "domain": (domain or "unknown").strip().lower(),
        "signal": signal,
        "value": float(value),
        "context": context or {},
        "source_module": source_module,
        "prev_hash": prev_hash,
    }
    entry_hash = hashlib.sha256(_serialise(body)).hexdigest()
    entry = {**body, "entry_hash": entry_hash}

    try:
        await rs.lpush(_KEY_LOG, json.dumps(entry, default=str))
        await rs.set(_KEY_HEAD_HASH, entry_hash)
        await rs.lpush(_KEY_BY_AXIS.format(axis=axis), entry_hash)
        await rs.lpush(_KEY_BY_DOMAIN.format(domain=_domain_key(body["domain"])), entry_hash)
        # Soft retention cap — trim main log occasionally (every ~1000 writes)
        if seq % 1000 == 0:
            try:
                await rs.ltrim(_KEY_LOG, 0, _MAX_RETAIN - 1)
            except Exception:
                pass
    except Exception as e:
        logger.warning("self_metrics emit failed axis=%s domain=%s: %s", axis, domain, e)
        entry["_persisted"] = False

    return entry


async def recent(
    limit: int = 100,
    axis: Optional[str] = None,
    domain: Optional[str] = None,
) -> list[dict]:
    """Newest-first. Filters are applied post-fetch — fine for normal
    rollup windows but don't ask for the whole log."""
    from . import redis_store as rs
    raw = await rs.lrange(_KEY_LOG, 0, limit * 4 - 1 if (axis or domain) else limit - 1)
    out: list[dict] = []
    target_domain = (domain or "").strip().lower() if domain else None
    for r in raw:
        try:
            e = json.loads(r) if isinstance(r, str) else r
        except Exception:
            continue
        if axis and e.get("axis") != axis:
            continue
        if target_domain and e.get("domain") != target_domain:
            continue
        out.append(e)
        if len(out) >= limit:
            break
    return out


def _summarise_values(axis: str, values: list[float]) -> dict:
    if not values:
        return {"n": 0}
    mean = statistics.fmean(values)
    stdev = statistics.pstdev(values) if len(values) > 1 else 0.0
    sorted_v = sorted(values)
    p50 = sorted_v[len(sorted_v) // 2]
    p95 = sorted_v[min(len(sorted_v) - 1, int(len(sorted_v) * 0.95))]
    out = {"n": len(values), "mean": round(mean, 4), "stdev": round(stdev, 4),
           "p50": round(p50, 4), "p95": round(p95, 4)}
    # Axis-aware "score": higher is always better. For timeliness, invert.
    if axis == "timeliness":
        # Below 60s = great (1.0), above 3600s = bad (0.0), linear between.
        score = max(0.0, min(1.0, 1.0 - (mean - 60) / (3600 - 60)))
    else:
        score = max(0.0, min(1.0, mean))
    out["score"] = round(score, 4)
    return out


async def rollup(
    window_days: int = 7,
    axis: Optional[str] = None,
    domain: Optional[str] = None,
) -> dict:
    """Roll up observations into per-(axis, domain) summaries for the
    given window. Compares against the prior window of equal length to
    surface regressions.

    Returns:
      {
        window: {start, end, days},
        cells: [{axis, domain, current: {...}, prior: {...}, delta: float, trend: "up"|"down"|"flat"}],
        generated_at: iso
      }
    """
    from . import redis_store as rs

    now = datetime.now(timezone.utc)
    cur_start = now - timedelta(days=window_days)
    prior_start = cur_start - timedelta(days=window_days)

    # Pull enough history to cover both windows. 2× window + headroom.
    raw = await rs.lrange(_KEY_LOG, 0, 10_000)
    current_by_cell: dict[tuple, list[float]] = {}
    prior_by_cell: dict[tuple, list[float]] = {}
    for r in raw:
        try:
            e = json.loads(r) if isinstance(r, str) else r
        except Exception:
            continue
        if axis and e.get("axis") != axis:
            continue
        if domain and e.get("domain") != (domain or "").strip().lower():
            continue
        try:
            ts = datetime.fromisoformat(e["ts"].replace("Z", "+00:00"))
        except Exception:
            continue
        if ts < prior_start:
            continue
        cell = (e["axis"], e.get("domain", "unknown"))
        value = float(e.get("value", 0.0))
        (current_by_cell if ts >= cur_start else prior_by_cell).setdefault(cell, []).append(value)

    cells: list[dict] = []
    all_cells = set(current_by_cell) | set(prior_by_cell)
    for (ax, dom) in sorted(all_cells):
        cur = _summarise_values(ax, current_by_cell.get((ax, dom), []))
        pri = _summarise_values(ax, prior_by_cell.get((ax, dom), []))
        cur_score = cur.get("score", 0.0) if cur["n"] else None
        pri_score = pri.get("score", 0.0) if pri["n"] else None
        if cur_score is not None and pri_score is not None:
            delta = round(cur_score - pri_score, 4)
            trend = "up" if delta > 0.05 else ("down" if delta < -0.05 else "flat")
        else:
            delta = None
            trend = "new" if pri_score is None else "stale"
        cells.append({
            "axis": ax,
            "domain": dom,
            "current": cur,
            "prior": pri,
            "delta": delta,
            "trend": trend,
        })

    return {
        "window": {
            "start": cur_start.isoformat(),
            "end": now.isoformat(),
            "days": window_days,
        },
        "cells": cells,
        "generated_at": now.isoformat(),
    }


async def recent_drift(
    days: int = 7,
    threshold_pp: float = 10.0,
    axis: Optional[str] = None,
) -> dict:
    """Return {domain: delta_pp} for domains whose score dropped at
    least `threshold_pp` percentage points over the trailing `days`
    compared with the prior equal-length window. Positive deltas are
    excluded (only regressions surface).

    Used by ecosystem_reassess to trigger mastery-drift reading
    sessions. Before 2026-04-20 this didn't exist; ecosystem_reassess's
    `hasattr(self_metrics, "recent_drift")` gate silently skipped the
    mastery-drift branch and no reading sessions ever got queued.
    """
    roll = await rollup(window_days=days, axis=axis)
    drift: dict[str, float] = {}
    threshold_frac = threshold_pp / 100.0
    for c in roll.get("cells", []):
        delta = c.get("delta")
        if delta is None:
            continue
        if delta < 0 and abs(delta) >= threshold_frac:
            key = str(c.get("domain") or "unknown")
            # Keep the worst drop if the same domain appears across axes
            prev = drift.get(key)
            pp = round(delta * 100.0, 1)
            if prev is None or pp < prev:
                drift[key] = pp
    return drift


async def strengths_and_weaknesses(window_days: int = 7, top_n: int = 5) -> dict:
    """Pick the top strengths (high score, stable/up) and top weaknesses
    (low score OR down-trending) for the briefing."""
    roll = await rollup(window_days=window_days)
    scored = [c for c in roll["cells"] if c["current"].get("n", 0) >= 3]  # ignore noise

    strengths = sorted(
        [c for c in scored if c["current"]["score"] >= 0.8 and c["trend"] in ("up", "flat")],
        key=lambda c: (-c["current"]["score"], -c["current"]["n"]),
    )[:top_n]

    weaknesses = sorted(
        [c for c in scored if c["current"]["score"] < 0.7 or c["trend"] == "down"],
        key=lambda c: (c["current"]["score"], c["delta"] or 0),
    )[:top_n]

    return {
        "window_days": window_days,
        "strengths": strengths,
        "weaknesses": weaknesses,
        "total_cells": len(scored),
        "generated_at": roll["generated_at"],
    }


async def verify_chain(start: int = 0, count: int = 500) -> dict:
    """Walk the chain and confirm every entry_hash matches its body and
    every prev_hash matches the prior entry_hash. Tampering anywhere
    breaks the chain forward — she'll notice."""
    from . import redis_store as rs
    raw = await rs.lrange(_KEY_LOG, start, start + count - 1)
    entries_chrono = []
    for r in raw:
        try:
            entries_chrono.append(json.loads(r) if isinstance(r, str) else r)
        except Exception:
            continue
    entries_chrono.reverse()
    if not entries_chrono:
        return {"verified": True, "checked": 0, "broken": []}

    broken: list[dict] = []
    expected_prev = entries_chrono[0].get("prev_hash") or _GENESIS_HASH
    for i, e in enumerate(entries_chrono):
        body = {k: v for k, v in e.items() if k != "entry_hash"}
        recomputed = hashlib.sha256(_serialise(body)).hexdigest()
        if recomputed != e.get("entry_hash"):
            broken.append({"index": i, "ts": e.get("ts"), "reason": "entry_hash mismatch"})
        if e.get("prev_hash") != expected_prev:
            broken.append({"index": i, "ts": e.get("ts"), "reason": "chain break"})
        expected_prev = e.get("entry_hash", "")

    return {"verified": not broken, "checked": len(entries_chrono), "broken": broken[:20]}


async def stats() -> dict:
    from . import redis_store as rs
    sample = await rs.lrange(_KEY_LOG, 0, 9999)
    head = await _read_head_hash()
    by_axis: dict[str, int] = {a: 0 for a in AXES}
    for r in sample:
        try:
            e = json.loads(r) if isinstance(r, str) else r
            ax = e.get("axis")
            if ax in by_axis:
                by_axis[ax] += 1
        except Exception:
            continue
    return {
        "total_entries": len(sample),
        "head_hash": head,
        "by_axis": by_axis,
    }
