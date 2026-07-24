"""R-F2987 — coverage heatmap: "freshness unknown" must not count ABSENT cells.

DD of the live Coverage Heatmap found the numbers accurate and derived, but ONE
honesty-of-presentation defect: `staleness_unknown_cells` counted every EMPTY
(absent) cell as "freshness unknown". Live that produced `617 freshness unknown ==
617 absent` exactly — conflating "no data" with "freshness unmeasured", and hiding
that freshness IS measured for 100% of populated cells (250/250, 0 present-but-
undated). An empty cell is a GAP (already `gap_count`), not a freshness deficiency.

This test pins the honest semantics: staleness_unknown counts ONLY populated cells
whose facts are undated; absent cells are excluded and disclosed as gaps.
"""
from __future__ import annotations

from aria_service.intel.coverage_heatmap import _compute_score


def test_rf2987_absent_cell_not_counted_as_freshness_unknown():
    # 1 populated+dated-stale, 1 populated+undated, 2 absent.
    matrix = {"d1": {
        "j1": {"tier": "deep", "is_stale": True},    # populated, known stale
        "j2": {"tier": "thin", "is_stale": None},    # populated, UNDATED → unknown
        "j3": {"tier": "absent", "is_stale": None},  # empty → GAP, NOT unknown
        "j4": {"tier": "absent", "is_stale": None},  # empty → GAP, NOT unknown
    }}
    juris = ["j1", "j2", "j3", "j4"]
    _score, summ = _compute_score(matrix, ["d1"], juris)

    assert summ["gap_count"] == 2, "absent cells are gaps"
    # the fix: only the populated+undated cell is 'freshness unknown', NOT the 2 empties
    assert summ["staleness_unknown_cells"] == 1, \
        f"only populated+undated cells are freshness-unknown, got {summ['staleness_unknown_cells']}"
    assert summ["stale_cells"] == 1
    assert summ["populated_cells"] == 2
    # freshness measured = populated that ARE dated = 1 (the stale one)
    assert summ["freshness_measured_cells"] == 1


def test_rf2987_no_longer_equals_absent_when_all_populated_are_dated():
    """The live artifact: when every populated cell is dated, freshness-unknown must
    be 0 — NOT equal to the absent count."""
    matrix = {"d1": {
        "j1": {"tier": "deep", "is_stale": False},   # populated, fresh (dated)
        "j2": {"tier": "moderate", "is_stale": True},# populated, stale (dated)
        "j3": {"tier": "absent", "is_stale": None},  # empty
    }}
    _score, summ = _compute_score(matrix, ["d1"], ["j1", "j2", "j3"])
    assert summ["gap_count"] == 1
    assert summ["staleness_unknown_cells"] == 0, "all populated cells dated → 0 unknown, not == absent"
    assert summ["staleness_unknown_cells"] != summ["gap_count"] or summ["gap_count"] == 0


def test_rf2987_r2332_contract_preserved():
    """Regression: R-F2332's populated-but-unknown deep cells are STILL disclosed
    (they are populated, so the scoping change keeps them counted)."""
    matrix = {"d1": {"j1": {"tier": "deep", "is_stale": None},
                     "j2": {"tier": "deep", "is_stale": None}}}
    score, summ = _compute_score(matrix, ["d1"], ["j1", "j2"])
    assert score == 1.0, "unknown-freshness deep cells still score full 1.0 (not penalised)"
    assert summ["stale_cells"] == 0
    assert summ["staleness_unknown_cells"] == 2, "populated undated cells still disclosed"
