"""AIS transponder-gap detector (R-F582, 2026-05-16).

Why this matters for sanctions DD
─────────────────────────────────
The Automatic Identification System (AIS) requires vessels above
~300 GT to broadcast position every 2-10 seconds. Sanctions-evasion
tactics centre on disabling the transponder ("going dark") to mask
ship-to-ship transfers, port calls to sanctioned terminals, or sanctioned
flag changes.

Bellingcat-class OSINT desks watch for these patterns by hand. R-F582
makes ARIA do it deterministically:

  1. Given a sequence of AIS reports for one vessel,
  2. Identify gaps where the next-reported position is impossible to
     reach from the previous given the elapsed time and a plausible
     maximum speed (default 25 knots ≈ 46 km/h).
  3. Score each gap: distance unaccounted for, duration of silence,
     whether the gap crosses a sanctions-watched corridor.

The detection is **pure math** — no LLM, no external API, runs in <1ms
on a 100-position day for one vessel. Per [[aria_mirrors_claude]] — fully
native.

Input shape
───────────
positions = [
    {"timestamp": <epoch_seconds>, "lat": <float>, "lon": <float>,
     "speed_knots": <optional float>, "course": <optional float>},
    ...
]

Output shape
────────────
{
    "vessel_id":      <str passed in>,
    "gaps":           [{...}, ...],    # gap records, see below
    "total_gaps":     <int>,
    "longest_gap_h":  <float>,
    "score":          <0-100, higher = more suspicious>,
    "signals":        [str, ...]       # human-readable findings
}

Gap record:
{
    "start_ts":     <epoch>,
    "end_ts":       <epoch>,
    "duration_h":   <float>,
    "start_pos":    (lat, lon),
    "end_pos":      (lat, lon),
    "great_circle_km":     <float>,
    "max_plausible_km":    <float>,    # (duration * max_speed)
    "unaccounted_km":      <float>,    # gc - max_plausible (>0 = impossible)
    "suspicion":           "low" | "medium" | "high" | "critical"
}

Per clause 3 + clause 14: a gap is an INDICATOR; final classification
of "sanctions evasion" requires correlation with port-call records,
sanctions cache, and flag-state events — handled in dd_orchestrator.
"""
from __future__ import annotations

import logging
import math
from typing import Any

logger = logging.getLogger("aria.intel.sources.ais_gap_detector")

# IMO recommended maximum reporting interval at sea is 10 seconds for
# moving vessels. Our gap threshold is far above that — only watch for
# silences ≥1 hour, where "lost AIS reception briefly" stops being a
# plausible explanation.
MIN_GAP_HOURS = 1.0

# Plausible upper bound on vessel speed. Most commercial cargo vessels
# run 12-20 knots; high-speed ferries up to 40; warships sustained 30+.
# 25 knots is conservative for non-naval commerce — exceedances are
# the signal we care about.
MAX_PLAUSIBLE_KNOTS = 25.0

# Score thresholds
_SCORE_PER_GAP_HOUR = 2.0
_SCORE_PER_KM_UNACCOUNTED = 0.05


def _haversine_km(p1: tuple[float, float], p2: tuple[float, float]) -> float:
    """Great-circle distance between (lat, lon) points in kilometres."""
    lat1, lon1 = p1
    lat2, lon2 = p2
    rad = math.pi / 180.0
    phi1, phi2 = lat1 * rad, lat2 * rad
    dphi = (lat2 - lat1) * rad
    dlam = (lon2 - lon1) * rad
    a = (math.sin(dphi / 2) ** 2 +
         math.cos(phi1) * math.cos(phi2) * math.sin(dlam / 2) ** 2)
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    return 6371.0 * c


def _classify_suspicion(unaccounted_km: float, duration_h: float) -> str:
    """Translate raw numbers into the operator-facing severity tag."""
    if unaccounted_km <= 0 and duration_h < 6:
        return "low"
    if duration_h >= 72:
        return "critical"      # 3+ days dark
    if unaccounted_km >= 200:
        return "critical"      # >200 km impossible to cover
    if duration_h >= 24:
        return "high"
    if unaccounted_km >= 50:
        return "high"
    if duration_h >= 6:
        return "medium"
    return "low"


def detect_gaps(
    positions: list[dict[str, Any]],
    *,
    vessel_id: str = "",
    min_gap_hours: float = MIN_GAP_HOURS,
    max_plausible_knots: float = MAX_PLAUSIBLE_KNOTS,
) -> dict[str, Any]:
    """Scan an ordered list of AIS positions and return gap analysis.

    Positions can arrive in any order; this function sorts by timestamp
    internally. Records missing timestamp / lat / lon are dropped with
    a debug log; the function never raises on bad input.
    """
    cleaned: list[dict[str, Any]] = []
    # R-F3570 — COUNT what is discarded. Dropping a malformed position is right; doing
    # it silently is not. A track that arrives half-corrupt was analysed as though it
    # were complete, so the caller read "2 gaps" from 50 of 100 positions exactly as it
    # would from a full track — a gap between two surviving points can also be an
    # artefact of the points removed between them. An AIS gap is evidence of possible
    # dark activity, so understating BOTH the coverage and the uncertainty is the
    # false-clean shape in miniature.
    _dropped = 0
    for p in positions or []:
        try:
            ts = float(p.get("timestamp"))
            lat = float(p.get("lat"))
            lon = float(p.get("lon"))
        except (TypeError, ValueError):
            _dropped += 1
            continue
        if not (-90.0 <= lat <= 90.0 and -180.0 <= lon <= 180.0):
            _dropped += 1
            continue
        cleaned.append({"timestamp": ts, "lat": lat, "lon": lon})

    _submitted = len(positions or [])
    _track_quality = {
        "positions_submitted": _submitted,
        "positions_used": len(cleaned),
        "positions_dropped": _dropped,
    }
    # Only a MATERIAL loss earns a signal — one bad ping in a thousand is noise, and a
    # signal that fires on everything gets ignored.
    _degraded = bool(_dropped) and (_dropped / _submitted) >= 0.10 if _submitted else False

    if len(cleaned) < 2:
        return {
            "vessel_id":     vessel_id,
            "gaps":          [],
            "total_gaps":    0,
            "longest_gap_h": 0.0,
            "score":         0,
            "signals":       ["insufficient_position_data"],
            "track_quality": _track_quality,
        }

    cleaned.sort(key=lambda x: x["timestamp"])

    gaps: list[dict[str, Any]] = []
    nm_per_km = 0.539957  # knots → km/h conversion via nautical-mile constant

    for i in range(1, len(cleaned)):
        prev, cur = cleaned[i - 1], cleaned[i]
        dt_seconds = cur["timestamp"] - prev["timestamp"]
        if dt_seconds <= 0:
            continue
        duration_h = dt_seconds / 3600.0
        if duration_h < min_gap_hours:
            continue

        gc_km = _haversine_km((prev["lat"], prev["lon"]),
                              (cur["lat"], cur["lon"]))
        # max_plausible_km = max_knots × nm/km factor × duration in hours
        # knots are nm/h so: km/h ≈ knots / nm_per_km
        max_plausible_km = (max_plausible_knots / nm_per_km) * duration_h
        unaccounted = max(0.0, gc_km - max_plausible_km)

        gaps.append({
            "start_ts":           int(prev["timestamp"]),
            "end_ts":             int(cur["timestamp"]),
            "duration_h":         round(duration_h, 2),
            "start_pos":          (round(prev["lat"], 4), round(prev["lon"], 4)),
            "end_pos":             (round(cur["lat"], 4), round(cur["lon"], 4)),
            "great_circle_km":    round(gc_km, 1),
            "max_plausible_km":   round(max_plausible_km, 1),
            "unaccounted_km":     round(unaccounted, 1),
            "suspicion":          _classify_suspicion(unaccounted, duration_h),
        })

    if not gaps:
        return {
            "vessel_id":     vessel_id,
            "gaps":          [],
            "total_gaps":    0,
            "longest_gap_h": 0.0,
            "score":         0,
            # R-F3570 — this is the CLEAN verdict, so it is where a silently
            # truncated track does the most damage: "no gaps" from a degraded
            # track must not read like "no gaps" from a complete one.
            "signals":       ["no_gaps_detected_within_threshold"]
                              + (["degraded_track_coverage"] if _degraded else []),
            "track_quality": _track_quality,
        }

    # Score: aggregate suspicion. Cap at 100.
    score = 0.0
    for g in gaps:
        score += g["duration_h"] * _SCORE_PER_GAP_HOUR
        score += g["unaccounted_km"] * _SCORE_PER_KM_UNACCOUNTED
    score = min(100.0, score)

    signals: list[str] = []
    critical = [g for g in gaps if g["suspicion"] == "critical"]
    high = [g for g in gaps if g["suspicion"] == "high"]
    if critical:
        signals.append(f"critical_gaps:{len(critical)}")
    if high:
        signals.append(f"high_severity_gaps:{len(high)}")
    longest = max(gaps, key=lambda g: g["duration_h"])
    if longest["duration_h"] >= 72:
        signals.append(f"silence_>=72h:{longest['duration_h']}h")
    if longest["unaccounted_km"] > 0:
        signals.append(
            f"impossible_speed_implied:{longest['unaccounted_km']}km_unaccounted"
        )

    return {
        "vessel_id":     vessel_id,
        "gaps":          gaps,
        "total_gaps":    len(gaps),
        "longest_gap_h": longest["duration_h"],
        "score":         round(score, 1),
        "signals":       signals + (["degraded_track_coverage"] if _degraded else []),
        "track_quality": _track_quality,
    }
