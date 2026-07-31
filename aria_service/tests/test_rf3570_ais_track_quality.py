"""R-F3570 — AIS gap analysis ran on a silently truncated track.

`detect_gaps` drops malformed positions with a bare `continue`. Dropping them is right;
doing it silently is not. A track arriving half-corrupt was analysed as though it were
complete, so the caller read "2 gaps" from 50 of 100 positions exactly as it would from
a full track — and a gap between two surviving points can itself be an artefact of the
points removed between them.

An AIS gap is evidence of possible dark activity, so understating both the coverage and
the uncertainty is the false-clean shape in miniature.
"""
from __future__ import annotations

from aria_service.intel.sources.ais_gap_detector import detect_gaps

_GOOD = [{"timestamp": i * 3600, "lat": 50.0 + i * 0.01, "lon": 1.0} for i in range(10)]
_BAD = [{"timestamp": "x", "lat": None, "lon": None} for _ in range(10)]
_OUT_OF_RANGE = [{"timestamp": 1, "lat": 999.0, "lon": 0.0}]


def test_every_result_reports_what_the_track_actually_was():
    """A consumer must never have to assume the input was complete."""
    for positions in (_GOOD, _GOOD + _BAD, [], _GOOD[:1]):
        r = detect_gaps(positions, vessel_id="V")
        assert "track_quality" in r, f"no coverage accounting for {len(positions)} rows"
        tq = r["track_quality"]
        assert tq["positions_submitted"] == len(positions)
        assert tq["positions_used"] + tq["positions_dropped"] == len(positions), (
            "every submitted position must be either used or counted as dropped"
        )


def test_a_materially_degraded_track_is_flagged():
    """PROVE RED: before this, 10 of 20 positions could vanish with no signal."""
    r = detect_gaps(_GOOD + _BAD, vessel_id="V")
    assert r["track_quality"]["positions_dropped"] == 10
    assert "degraded_track_coverage" in r["signals"]


def test_a_clean_track_is_not_flagged():
    """A signal that fires on everything gets ignored."""
    r = detect_gaps(_GOOD, vessel_id="V")
    assert r["track_quality"]["positions_dropped"] == 0
    assert "degraded_track_coverage" not in r["signals"]


def test_one_bad_ping_in_a_long_track_is_noise_not_a_signal():
    positions = [{"timestamp": i * 3600, "lat": 50.0, "lon": 1.0} for i in range(60)]
    positions.append({"timestamp": "bad", "lat": None, "lon": None})
    r = detect_gaps(positions, vessel_id="V")
    assert r["track_quality"]["positions_dropped"] == 1
    assert "degraded_track_coverage" not in r["signals"], (
        "a 1.6% loss must not fire the signal, or it becomes noise and gets muted"
    )


def test_out_of_range_coordinates_count_as_dropped_not_used():
    """The range check is a second discard path and was equally silent."""
    r = detect_gaps(_GOOD + _OUT_OF_RANGE, vessel_id="V")
    assert r["track_quality"]["positions_dropped"] == 1
    assert r["track_quality"]["positions_used"] == len(_GOOD)


def test_the_clean_verdict_carries_the_degradation_too():
    """The 'no gaps' return is where a truncated track does the most damage —
    'no gaps' from a degraded track must not read like 'no gaps' from a full one."""
    dense = [{"timestamp": i * 60, "lat": 50.0, "lon": 1.0} for i in range(20)]
    r = detect_gaps(dense + _BAD, vessel_id="V")
    assert r["total_gaps"] == 0, "fixture should produce the clean verdict"
    assert "no_gaps_detected_within_threshold" in r["signals"]
    assert "degraded_track_coverage" in r["signals"], (
        "the clean verdict hid the fact that half the track was discarded"
    )
    assert r["track_quality"]["positions_dropped"] == 10


def test_gap_results_are_unchanged_for_a_valid_track():
    """Accounting must not alter the analysis."""
    r = detect_gaps(_GOOD, vessel_id="V")
    assert r["vessel_id"] == "V"
    assert isinstance(r["gaps"], list)
    assert isinstance(r["score"], (int, float))
