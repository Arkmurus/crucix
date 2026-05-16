"""R-F582 (2026-05-16) — AIS gap detector tests."""
from __future__ import annotations

import pytest


def _pos(ts: int, lat: float, lon: float) -> dict:
    return {"timestamp": ts, "lat": lat, "lon": lon}


# ── Math helpers ──────────────────────────────────────────────────────


def test_haversine_zero_distance():
    from aria_service.intel.sources.ais_gap_detector import _haversine_km
    assert _haversine_km((10.0, 20.0), (10.0, 20.0)) == 0.0


def test_haversine_known_distance():
    """London → New York great-circle is ~5570 km (within 5%)."""
    from aria_service.intel.sources.ais_gap_detector import _haversine_km
    london = (51.5074, -0.1278)
    nyc = (40.7128, -74.0060)
    d = _haversine_km(london, nyc)
    assert 5300 < d < 5800, f"London→NYC ≈ 5570 km; got {d}"


# ── Detection contract ────────────────────────────────────────────────


def test_empty_input():
    from aria_service.intel.sources.ais_gap_detector import detect_gaps
    out = detect_gaps([], vessel_id="MV TEST")
    assert out["total_gaps"] == 0
    assert "insufficient_position_data" in out["signals"]


def test_single_position():
    from aria_service.intel.sources.ais_gap_detector import detect_gaps
    out = detect_gaps([_pos(1000, 0.0, 0.0)], vessel_id="V1")
    assert out["total_gaps"] == 0


def test_no_gaps_when_reporting_regularly():
    """Capability: vessel reporting every 5 min for 24h → 0 gaps."""
    from aria_service.intel.sources.ais_gap_detector import detect_gaps
    positions = []
    t0 = 1_700_000_000
    for i in range(0, 24 * 60 // 5):  # every 5 min for 24h
        positions.append(_pos(t0 + i * 300, 1.0 + i * 0.001, 1.0))
    out = detect_gaps(positions, vessel_id="V2")
    assert out["total_gaps"] == 0
    assert out["score"] == 0


def test_short_gap_below_threshold_ignored():
    """Capability: a 45-minute AIS dropout is below the 1h threshold
    → not flagged. This avoids false positives from ordinary reception
    glitches."""
    from aria_service.intel.sources.ais_gap_detector import detect_gaps
    positions = [
        _pos(1_700_000_000, 0.0, 0.0),
        _pos(1_700_000_000 + 45 * 60, 0.01, 0.01),
    ]
    out = detect_gaps(positions, vessel_id="V3")
    assert out["total_gaps"] == 0


def test_long_silence_flagged():
    """Capability: a 4h gap at sea is the classic dark-period signal."""
    from aria_service.intel.sources.ais_gap_detector import detect_gaps
    positions = [
        _pos(1_700_000_000, 0.0, 0.0),
        _pos(1_700_000_000 + 4 * 3600, 0.1, 0.1),  # ~15.6 km in 4h
    ]
    out = detect_gaps(positions, vessel_id="V4")
    assert out["total_gaps"] == 1
    g = out["gaps"][0]
    assert g["duration_h"] == 4.0
    # 15.6 km vs 4h × 25 knots × ~1.852 = ~185 km plausible → 0 unaccounted
    assert g["unaccounted_km"] == 0.0
    # 4h is in the "low" bucket for sustained dark; signals empty for low-severity
    # but the gap is still counted.
    assert g["suspicion"] in ("low", "medium")


def test_impossible_distance_flagged_critical():
    """Capability: vessel reports at point A, then 2h later at point B
    1000 km away. Impossible at 25 knots (max ~92 km in 2h).
    Should be flagged as critical."""
    from aria_service.intel.sources.ais_gap_detector import detect_gaps
    positions = [
        _pos(1_700_000_000, 0.0, 0.0),
        # ~1000 km east in 2h
        _pos(1_700_000_000 + 2 * 3600, 0.0, 9.0),  # ~1000 km along equator
    ]
    out = detect_gaps(positions, vessel_id="V5")
    assert out["total_gaps"] == 1
    g = out["gaps"][0]
    assert g["unaccounted_km"] > 800, f"got {g['unaccounted_km']} km unaccounted"
    assert g["suspicion"] == "critical"
    assert out["score"] > 30
    assert any("impossible_speed_implied" in s for s in out["signals"])


def test_multi_day_silence_flagged_critical():
    """Capability: 4 days dark — even at low speeds — is critical."""
    from aria_service.intel.sources.ais_gap_detector import detect_gaps
    positions = [
        _pos(1_700_000_000, 0.0, 0.0),
        _pos(1_700_000_000 + 4 * 86400, 5.0, 5.0),  # 4 days, plausible distance
    ]
    out = detect_gaps(positions, vessel_id="V6")
    assert out["total_gaps"] == 1
    g = out["gaps"][0]
    assert g["duration_h"] >= 96
    assert g["suspicion"] == "critical"
    assert any("silence_>=72h" in s for s in out["signals"])


def test_handles_unsorted_input():
    """Capability: input positions may arrive in any order (typical of
    AIS aggregators). detect_gaps must sort internally."""
    from aria_service.intel.sources.ais_gap_detector import detect_gaps
    positions = [
        _pos(1_700_000_000 + 3 * 3600, 0.05, 0.05),
        _pos(1_700_000_000, 0.0, 0.0),
        _pos(1_700_000_000 + 6 * 3600, 0.1, 0.1),
    ]
    out = detect_gaps(positions, vessel_id="V7")
    # 3h + 3h gaps each — 2 gaps total (both above 1h threshold)
    assert out["total_gaps"] == 2


def test_handles_bad_input_rows():
    """Capability: dropped records on malformed input — never raises."""
    from aria_service.intel.sources.ais_gap_detector import detect_gaps
    positions = [
        {"timestamp": "garbage", "lat": 0.0, "lon": 0.0},
        {"timestamp": 1_700_000_000, "lat": "bad", "lon": 0.0},
        {"timestamp": 1_700_000_000, "lat": 200.0, "lon": 0.0},  # out of range
        _pos(1_700_000_000, 0.0, 0.0),
        _pos(1_700_000_000 + 3 * 3600, 0.1, 0.1),
    ]
    out = detect_gaps(positions, vessel_id="V8")
    assert out["total_gaps"] == 1   # only the 2 valid positions counted


def test_duplicate_timestamps_skipped():
    """Capability: same-timestamp records (which would be a 0-duration
    delta) must not produce a gap entry."""
    from aria_service.intel.sources.ais_gap_detector import detect_gaps
    positions = [
        _pos(1_700_000_000, 0.0, 0.0),
        _pos(1_700_000_000, 0.0, 0.0),
        _pos(1_700_000_000, 0.0, 0.0),
    ]
    out = detect_gaps(positions, vessel_id="V9")
    assert out["total_gaps"] == 0


def test_vessel_id_round_trip():
    from aria_service.intel.sources.ais_gap_detector import detect_gaps
    out = detect_gaps([_pos(1, 0.0, 0.0), _pos(2 + 3600 * 2, 0.0, 0.1)],
                      vessel_id="IMO9837492")
    assert out["vessel_id"] == "IMO9837492"


def test_score_caps_at_100():
    """Capability: extremely suspicious traces (many critical gaps)
    must not over-saturate the score field."""
    from aria_service.intel.sources.ais_gap_detector import detect_gaps
    # 10 multi-day gaps with impossible distances. Keep lat within
    # ±90° by cycling longitude only (longitude wrap is fine for the
    # haversine).
    positions = [_pos(1_700_000_000, 0.0, 0.0)]
    for i in range(1, 11):
        positions.append(_pos(
            1_700_000_000 + i * 96 * 3600,  # 4 days apart
            (i % 8) * 5.0,                  # lat ≤ 35°, well within range
            (i * 20.0) % 180.0,             # huge lon jumps, wrap-safe
        ))
    out = detect_gaps(positions, vessel_id="V_BAD")
    assert out["score"] <= 100
    assert out["total_gaps"] == 10
