"""R-F2990 — regional mastery heatmap: SAMPLES-based scaffold count.

DD of the gate-#2 heatmap found every % accurate and derived (real EWMA, strict-
read clobber-guarded), but the dashboard caption "N cells still at the ~50% initial
scaffold" used a score-near-0.50 proxy (score < 0.55) that conflated two OPPOSITE
states: genuine unmeasured scaffold, and measured-WEAK cells that real failing
recall-grades drove far below 0.50 (live: procurement×central_africa at 0.04, ALSO
listed in weak_cells). Counting a 0.04 cell as "unmeasured scaffold" was self-
contradictory. R-F2990 keys the split on the real `samples` signal: scaffold = a
cell with <=1 observation; a low score with >=2 observations is a MEASURED gap.

This drives the actual builder (get_regional_heatmap) and asserts the user-visible
cell_coverage split the dashboard reads.
"""
from __future__ import annotations

import asyncio

from aria_service.intel import student


def _run_with_cache(rm: dict) -> dict:
    prev = student._regional_cache
    student._regional_cache = rm  # _load_regional_mastery returns the cache when set
    try:
        return asyncio.run(student.get_regional_heatmap())
    finally:
        student._regional_cache = prev


def test_rf2990_scaffold_is_samples_not_score():
    rm = {
        # genuine scaffold — not yet measured (<=1 observation)
        "sanctions:west_africa":     {"score": 0.5,   "samples": 0},
        "sanctions:east_africa":     {"score": 0.475, "samples": 1},
        # measured, healthy (>=2 obs, above weak threshold)
        "sanctions:gulf":            {"score": 0.90,  "samples": 10},
        "compliance:europe":         {"score": 0.70,  "samples": 5},
        # MEASURED-weak — low score but MANY observations → NOT scaffold, IS weak
        "compliance:central_africa": {"score": 0.04,  "samples": 8},
        # noise that must be dropped (not counted anywhere)
        "sanctions:latam":           {"score": 0.5,   "samples": 0},   # dead region key
        "general:gulf":              {"score": 0.5,   "samples": 0},   # general topic
        "sanctions:global":          {"score": 0.5,   "samples": 0},   # global region
    }
    out = _run_with_cache(rm)
    cov = out["cell_coverage"]

    assert cov["sampled_cells"] == 5, "dropped keys (latam/general/global) must not count"
    assert cov["scaffold_cells"] == 2, "only the two <=1-sample cells are scaffold"
    assert cov["measured_cells"] == 3
    # the crux: the 0.04 cell has 8 samples → it is MEASURED-weak, NOT scaffold
    assert cov["measured_weak_cells"] == 1
    # and it must appear in weak_cells (score < 0.55), NOT be hidden as scaffold
    weak_keys = {(w["topic"], w["region"]) for w in out["weak_cells"]}
    assert ("compliance", "central_africa") in weak_keys

    # regression against the old proxy: score<0.55 would have counted 3 "scaffold"
    # (west_africa 0.5, east_africa 0.475, central_africa 0.04). samples-based = 2,
    # and it refuses to double-label the measured-weak cell.
    proxy_would_count = sum(
        1 for t, rr in out["heatmap"].items() for r, s in rr.items() if s < 0.55
    )
    assert proxy_would_count == 3 and cov["scaffold_cells"] == 2, \
        "samples-based split must exclude the measured-weak cell the proxy mislabeled"


def test_rf2990_all_unmeasured_when_all_low_samples():
    rm = {
        "sanctions:gulf":  {"score": 0.5, "samples": 1},
        "sanctions:mena":  {"score": 0.5, "samples": 0},
    }
    cov = _run_with_cache(rm)["cell_coverage"]
    assert cov["sampled_cells"] == 2
    assert cov["scaffold_cells"] == 2
    assert cov["measured_cells"] == 0
    assert cov["measured_weak_cells"] == 0
