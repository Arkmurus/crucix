"""R-F2283 — gate-#2 free-loop accelerator (no spend, no metric gaming).

Drives the extracted _study_weak_regional_cells() — the region-targeted crediting
path reading_session now calls — and asserts the NEW behavior:
  1. throughput: 6 articles/cell requested (was 3);
  2. read-grounded credit still lifts the exact cell (via update_regional_mastery);
  3. an UNCREDITED floor cell records a per-CELL §21e capability gap (the existing
     update_mastery gap is TOPIC-level only and can't target a region).

The end-to-end reading_session path is covered by test_rf1744.
"""
from __future__ import annotations

import asyncio
import types

import pytest

from aria_service.intel import student


@pytest.fixture(autouse=True)
def _no_seed(monkeypatch):
    # R-F2965 (C3): seeding now DEFAULTS ON. These tests assert the pre-seed
    # cell-selection contract (e.g. the max_cells cap), so disable seeding here.
    monkeypatch.setenv("ARIA_STUDENT_SEED_ALL_REGIONS", "0")


@pytest.mark.asyncio
async def test_rf2283_throughput_and_grounded_lift(monkeypatch):
    cell = {"topic": "competitor_intel", "region": "southern_africa", "score": 0.52}

    async def _hm():
        return {"floor_breach_cells": [cell], "weak_cells": [cell]}
    monkeypatch.setattr(student, "get_regional_heatmap", _hm)

    seen = {}
    grounded = types.SimpleNamespace(
        value="South Africa Denel SADC defence procurement tender",
        context="Denel and Paramount, South Africa / southern Africa defence market, 2026.",
        source_url="https://ex.test/sa",
    )

    async def _explore(**k):
        seen.update(k)
        return types.SimpleNamespace(facts=[grounded])

    async def _store(*a, **k):
        return {"action": "created"}
    monkeypatch.setattr(student.kb, "store_fact", _store)

    calls = []
    async def _spy(topics, regions, correct, weight=1.0):
        calls.append((list(topics), list(regions), correct))
    monkeypatch.setattr(student, "update_regional_mastery", _spy)

    # R-F2660: crediting now gates on an HONEST recall grade, not a participation
    # trophy. This test isolates the throughput + grounded-lift MECHANIC, so stub
    # the grader to PASS; the grade gating itself is covered by test_rf2660.
    async def _grade_pass(*a, **k):
        return True
    monkeypatch.setattr("aria_service.autonomous.tasks._grade_researched_cell", _grade_pass)

    studied = await student._study_weak_regional_cells(explore=_explore)

    # (1) throughput bump: 6 articles/cell requested
    assert seen.get("max_results") == 6, seen
    assert seen.get("cost_free") is True
    # (2) grounded content + PASSING honest grade → cell credited correct=True
    assert (["competitor_intel"], ["southern_africa"], True) in calls, calls
    assert any(x["topic"] == "competitor_intel" and x["region"] == "southern_africa"
               for x in studied), studied


@pytest.mark.asyncio
async def test_rf2283_default_targets_more_than_ten_cells(monkeypatch):
    # 15 distinct valid floor cells → the helper must target >10 (old cap was 10).
    regions = [r for r in student.REGIONS if r != "global"][:15]
    cells = [{"topic": "competitor_intel", "region": r, "score": 0.5} for r in regions]

    async def _hm():
        return {"floor_breach_cells": cells, "weak_cells": cells}
    monkeypatch.setattr(student, "get_regional_heatmap", _hm)

    queried = []
    async def _explore(**k):
        queried.append(k.get("query"))
        return types.SimpleNamespace(facts=[])  # no facts → no credit
    async def _store(*a, **k):
        return {}
    monkeypatch.setattr(student.kb, "store_fact", _store)
    async def _spy(*a, **k):
        return None
    monkeypatch.setattr(student, "update_regional_mastery", _spy)
    from aria_service.intel import capability_gaps as _cg
    async def _rg(*a, **k):
        return None
    monkeypatch.setattr(_cg, "record_gap", _rg)

    await student._study_weak_regional_cells(explore=_explore)
    assert len(queried) > 10, f"R-F2283 should target more than the old 10-cell cap; got {len(queried)}"
    assert len(queried) <= 15


@pytest.mark.asyncio
async def test_rf2283_uncredited_cell_records_percell_gap(monkeypatch):
    cell = {"topic": "compliance", "region": "east_africa", "score": 0.5}

    async def _hm():
        return {"floor_breach_cells": [cell], "weak_cells": [cell]}
    monkeypatch.setattr(student, "get_regional_heatmap", _hm)

    # Content that does NOT mention the region → detect_regions won't confirm →
    # uncredited → must record a per-cell gap.
    ungrounded = types.SimpleNamespace(
        value="generic defence newsletter", context="no country named here",
        source_url="https://ex.test/x",
    )
    async def _explore(**k):
        return types.SimpleNamespace(facts=[ungrounded])
    async def _store(*a, **k):
        return {}
    monkeypatch.setattr(student.kb, "store_fact", _store)
    async def _spy(*a, **k):
        return None
    monkeypatch.setattr(student, "update_regional_mastery", _spy)

    gaps = []
    from aria_service.intel import capability_gaps as _cg
    async def _rg(*, gap_type, detail, source, **k):
        gaps.append(source)
    monkeypatch.setattr(_cg, "record_gap", _rg)

    studied = await student._study_weak_regional_cells(explore=_explore)
    await asyncio.sleep(0.05)  # let the fire-and-forget record_gap task run

    assert not studied, "ungrounded content must not credit the cell"
    assert any("compliance:east_africa" in s for s in gaps), (
        f"expected a per-CELL §21e gap for compliance:east_africa; got {gaps}"
    )
