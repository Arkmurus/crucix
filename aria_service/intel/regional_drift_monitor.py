"""R-F2957 — regional-mastery drift monitor (Phase A gate #2 compounding observability).

The Topic×Region regional mastery (the imaria.io/aria-brain Mastery Heat Map,
Phase A gate #2) was a pure CURRENT snapshot: `crucix:aria:student:regional_mastery`
is overwritten in place on every EWMA update (student.update_regional_mastery),
so there was NO way to tell whether a cell — or the gate-#2 floor — is trending
UP or DOWN over days. "Is she compounding?" was unanswerable without live probing.

This module ships the time-series substrate, mirroring `brier_drift_monitor`
(which does the same for TOPIC mastery / gate #1) but keyed on the regional grid:

  1. snapshot_regional() — captures floor / mean / count≥0.70 / cell_count and the
     FULL per-cell score map to a Redis list (last 30 snapshots, NO TTL per
     CLAUDE.md §7 infinite memory). Per-cell map is UNBOUNDED — CLAUDE.md §1 names
     `floor_breach_cells[:20]` truncation a forbidden "measure-less" clamp, so the
     durable record must not truncate.
  2. compute_regional_drift(window_hours) — diffs the latest snapshot against the
     one closest to N hours ago: floor delta, count≥0.70 delta, per-cell deltas,
     best/worst movers. Floor delta is `latest - baseline`, so a POSITIVE floor
     delta means the gate-#2 floor rose (compounding).
  3. floor_velocity(window_hours) — the headline "is she growing?" signal used by
     the /student/mastery/compounding endpoint + the aria-brain Compounding panel.

IMPORTANT (CLAUDE.md §1): the honest floor is EXPECTED to DROP when new cells are
seeded (INITIAL_MASTERY=0.5) or cells get re-graded honestly — that is the gate
becoming EARNED, not a regression. Judge compounding by this time-series VELOCITY
across many cells, never by a single-snapshot floor.

Surfaces via GET /api/aria/student/mastery/compounding (R-F2958).
"""
from __future__ import annotations

import json
import logging
import time
from typing import Any

from . import redis_store as rs

logger = logging.getLogger("aria.regional_drift_monitor")

# NOTE: distinct from brier's crucix:brier_drift:snapshots (topic-level). This is
# the region×topic series. lpush newest-first + ltrim to a bounded ring; no TTL.
_SNAPSHOT_KEY = "crucix:aria:student:regional_mastery_snapshots"
_MAX_SNAPSHOTS = 30  # ~30 daily snapshots at 4/day cadence ≈ a week of history

# GATE_2_FLOOR_TARGET lives in student.py; imported lazily to avoid an import cycle.
_GATE_2_TARGET = 0.70


async def snapshot_regional() -> dict[str, Any]:
    """Capture a timestamped regional-mastery snapshot to Redis.

    Computes aggregates from the FULL heatmap (not the truncated weak/floor
    lists). Returns the snapshot for caller logging. Errors are non-fatal — a
    Redis blip or not-ready store just skips this snapshot; the next lands fine.

    Skips the write entirely when the heatmap has 0 cells (a not-ready store,
    R-F2664) so the series is never poisoned with a no-data floor=None row.
    """
    try:
        from . import student
        hm = await student.get_regional_heatmap()
    except Exception as e:
        logger.warning("snapshot_regional: get_regional_heatmap failed: %s", e)
        return {"error": str(e)}

    heatmap = (hm or {}).get("heatmap", {}) or {}
    cells: dict[str, float] = {}
    for topic, regions in heatmap.items():
        for region, score in (regions or {}).items():
            try:
                cells[f"{topic}:{region}"] = round(float(score), 4)
            except (TypeError, ValueError):
                continue

    cell_count = len(cells)
    if cell_count == 0:
        # Not-ready store / empty heatmap — do NOT record a null-floor row that
        # would corrupt velocity math. Skip silently; next cadence retries.
        logger.debug("snapshot_regional: 0 cells (store not ready?) — skipping snapshot")
        return {"skipped": "no_cells"}

    scores = list(cells.values())
    floor = min(scores)
    mean = round(sum(scores) / cell_count, 4)
    count_ge_target = sum(1 for s in scores if s >= _GATE_2_TARGET)

    snapshot = {
        "ts": time.time(),
        "floor": round(floor, 4),
        "mean": mean,
        "count_ge_070": count_ge_target,
        "cell_count": cell_count,
        "gate_2_target": _GATE_2_TARGET,
        "cells": cells,  # UNBOUNDED per §1 — no [:N] truncation on the durable record
    }
    try:
        await rs.lpush(_SNAPSHOT_KEY, json.dumps(snapshot))
        await rs.ltrim(_SNAPSHOT_KEY, 0, _MAX_SNAPSHOTS - 1)
    except Exception as e:
        logger.warning("snapshot_regional: redis write failed: %s", e)
    return snapshot


async def _read_snapshots() -> list[dict[str, Any]]:
    """Return all stored snapshots newest-first. Best-effort — a corrupt entry
    is skipped, not surfaced."""
    try:
        raw = await rs.lrange(_SNAPSHOT_KEY, 0, _MAX_SNAPSHOTS - 1)
    except Exception as e:
        logger.warning("_read_snapshots: redis read failed: %s", e)
        return []
    out: list[dict[str, Any]] = []
    for item in raw or []:
        try:
            out.append(json.loads(item))
        except Exception:
            continue
    return out


def _pick_baseline(snapshots: list[dict[str, Any]], window_hours: int) -> dict[str, Any] | None:
    """Given newest-first snapshots, return the one closest to (but no newer
    than) window_hours before the latest. Falls back to the oldest available."""
    if len(snapshots) < 2:
        return None
    latest = snapshots[0]
    now = latest.get("ts") or time.time()
    cutoff = now - (max(1, int(window_hours)) * 3600)
    for s in snapshots[1:]:
        if (s.get("ts") or 0) <= cutoff:
            return s
    # Not enough history for the requested window — best-effort with the oldest.
    return snapshots[-1]


async def compute_regional_drift(window_hours: int = 24) -> dict[str, Any]:
    """Diff the most-recent regional snapshot against the snapshot closest to
    window_hours ago. Returns floor/count deltas + per-cell deltas + movers.

    Deltas are `latest - baseline`, so a POSITIVE floor delta = the gate-#2
    floor ROSE (compounding); a positive count_ge_070 delta = more cells crossed
    the 0.70 target.
    """
    snapshots = await _read_snapshots()
    if len(snapshots) < 2:
        return {
            "ok": False,
            "reason": "need at least 2 snapshots; have %d" % len(snapshots),
            "snapshots": len(snapshots),
        }

    latest = snapshots[0]
    baseline = _pick_baseline(snapshots, window_hours)
    if baseline is None:
        return {"ok": False, "reason": "no_baseline", "snapshots": len(snapshots)}

    floor_delta = round((latest.get("floor") or 0) - (baseline.get("floor") or 0), 4)
    mean_delta = round((latest.get("mean") or 0) - (baseline.get("mean") or 0), 4)
    count_delta = int((latest.get("count_ge_070") or 0) - (baseline.get("count_ge_070") or 0))

    latest_cells = latest.get("cells") or {}
    baseline_cells = baseline.get("cells") or {}
    cell_deltas: dict[str, float] = {}
    for cell, latest_score in latest_cells.items():
        base_score = baseline_cells.get(cell)
        if base_score is None:
            continue
        cell_deltas[cell] = round(float(latest_score) - float(base_score), 4)

    risers = sorted(cell_deltas.items(), key=lambda kv: kv[1], reverse=True)
    fallers = sorted(cell_deltas.items(), key=lambda kv: kv[1])

    return {
        "ok": True,
        "window_hours": int(window_hours),
        "latest_ts": latest.get("ts"),
        "baseline_ts": baseline.get("ts"),
        "floor_delta": floor_delta,
        "mean_delta": mean_delta,
        "count_ge_070_delta": count_delta,
        "latest_floor": latest.get("floor"),
        "latest_count_ge_070": latest.get("count_ge_070"),
        "latest_cell_count": latest.get("cell_count"),
        "top_risers": [kv for kv in risers if kv[1] > 0][:5],
        "top_fallers": [kv for kv in fallers if kv[1] < 0][:5],
        "cell_deltas": cell_deltas,
    }


async def floor_velocity(window_hours: int = 168) -> dict[str, Any]:
    """The headline "is she compounding?" signal: floor movement + cells≥0.70
    trend over the window (default 7 days). Returns a compact summary the
    /student/mastery/compounding endpoint and the aria-brain panel render.
    """
    snapshots = await _read_snapshots()
    if len(snapshots) < 2:
        latest = snapshots[0] if snapshots else {}
        return {
            "ok": False,
            "reason": "insufficient_history",
            "snapshots": len(snapshots),
            "latest_floor": latest.get("floor"),
            "latest_count_ge_070": latest.get("count_ge_070"),
            "latest_cell_count": latest.get("cell_count"),
        }
    drift = await compute_regional_drift(window_hours=window_hours)
    if not drift.get("ok"):
        return {"ok": False, "reason": drift.get("reason"), "snapshots": len(snapshots)}

    span_h = 0.0
    try:
        span_h = max(0.0, ((drift.get("latest_ts") or 0) - (drift.get("baseline_ts") or 0)) / 3600.0)
    except Exception:
        span_h = 0.0
    floor_delta = drift.get("floor_delta") or 0.0
    per_week = round(floor_delta * (168.0 / span_h), 4) if span_h > 0 else None

    # compounding=True when the floor rose OR more cells crossed the target over
    # the span. A flat/negative reading with new cells appearing is EXPECTED
    # (seeding lowers the floor first, §1) — the endpoint/panel annotate this.
    compounding = (floor_delta > 0) or ((drift.get("count_ge_070_delta") or 0) > 0)

    return {
        "ok": True,
        "window_hours": int(window_hours),
        "span_hours": round(span_h, 2),
        "floor_delta": floor_delta,
        "floor_delta_per_week": per_week,
        "count_ge_070_delta": drift.get("count_ge_070_delta"),
        "latest_floor": drift.get("latest_floor"),
        "latest_count_ge_070": drift.get("latest_count_ge_070"),
        "latest_cell_count": drift.get("latest_cell_count"),
        "compounding": compounding,
        "top_risers": drift.get("top_risers"),
        "top_fallers": drift.get("top_fallers"),
    }


# ── B2 (R-F2960): score-stagnation stall detection ──────────────────────────
# research_engine._is_stalled catches only INGEST failure (attempts>0, ingests==0).
# A below-floor cell that ingests content but whose HONEST grade stays False —
# so its score never rises — was invisible. This closes that gap by watching the
# time-series: a below-floor cell flat across the window is STALLED, and we
# classify WHY using the live sample count (starved = no/one sample; grade_failing
# = has samples but the recall grade keeps failing). It NEVER modifies a score.

_STALL_EPS = 0.01  # a cell that moved < 1pp across the window is "flat"


async def detect_stalled_cells(
    window_hours: int = 168, *, min_snapshots: int = 3, eps: float = _STALL_EPS,
) -> list[dict[str, Any]]:
    """Return below-floor cells whose score is flat (< eps) across the window.

    Requires >= min_snapshots of history so early-boot flatness (every cell
    legitimately new) is not mistaken for a stall. Each result carries a `why`:
      - "starved":       <=1 sample — the content pipeline can't ground the cell.
      - "grade_failing": has samples but the honest recall grade keeps failing.
    Read-only — records nothing, mutates nothing.
    """
    snaps = await _read_snapshots()
    if len(snaps) < max(2, int(min_snapshots)):
        return []
    latest = snaps[0]
    baseline = _pick_baseline(snaps, window_hours)
    if not baseline:
        return []
    latest_cells = latest.get("cells") or {}
    base_cells = baseline.get("cells") or {}

    # Live sample counts for the starved-vs-grade_failing classification.
    samples: dict[str, int] = {}
    try:
        from . import student
        rm = await student._load_regional_mastery()
        for k, v in (rm or {}).items():
            if isinstance(v, dict):
                samples[k] = int(v.get("samples", 0) or 0)
    except Exception as e:
        logger.debug("detect_stalled_cells: sample lookup failed: %s", e)

    stalled: list[dict[str, Any]] = []
    for cell, score in latest_cells.items():
        try:
            if float(score) >= _GATE_2_TARGET:
                continue  # only below-floor cells matter for gate #2
        except (TypeError, ValueError):
            continue
        base = base_cells.get(cell)
        if base is None:
            continue  # not in the baseline (new cell) — can't judge a stall yet
        if abs(float(score) - float(base)) < eps:
            n = samples.get(cell, 0)
            why = "starved" if n <= 1 else "grade_failing"
            stalled.append({"cell": cell, "score": round(float(score), 4), "samples": n, "why": why})
    stalled.sort(key=lambda c: c["score"])
    return stalled


async def record_stalled_gaps(window_hours: int = 168, *, min_snapshots: int = 3) -> dict[str, Any]:
    """Detect stalled below-floor cells and record ONE aggregate `stalled_cell`
    capability gap (deduped) so the operator/coder can act — without spamming a
    gap per cell. Returns a summary. Called from the regional snapshot loop.
    """
    stalled = await detect_stalled_cells(window_hours, min_snapshots=min_snapshots)
    if not stalled:
        return {"stalled": 0, "recorded": False}

    starved = [c for c in stalled if c["why"] == "starved"]
    grade_failing = [c for c in stalled if c["why"] == "grade_failing"]
    worst = ", ".join(f"{c['cell']}={c['score']}({c['why']})" for c in stalled[:6])
    detail = (
        f"{len(stalled)} gate-#2 regional cells STALLED (flat below 0.70 over "
        f"{window_hours}h): {len(starved)} starved (content pipeline can't ground "
        f"them), {len(grade_failing)} grade-failing (ingesting but recall grade "
        f"keeps failing). Worst: {worst}. Starved cells need a content-source lever "
        f"(SearXNG/Brave/feed); grade-failing cells need more grounded reads to cross "
        f"the recall threshold."
    )
    recorded = False
    try:
        from . import capability_gaps as _cg
        await _cg.record_gap(
            gap_type="stalled_cell",
            detail=detail[:600],
            source="regional_drift_monitor:B2",
            severity="HIGH",
            title=f"{len(stalled)} regional mastery cells stalled below gate-#2 floor",
        )
        recorded = True
    except Exception as e:
        logger.warning("record_stalled_gaps: record_gap failed: %s", e)

    return {
        "stalled": len(stalled),
        "starved": len(starved),
        "grade_failing": len(grade_failing),
        "recorded": recorded,
        "cells": stalled,
    }
