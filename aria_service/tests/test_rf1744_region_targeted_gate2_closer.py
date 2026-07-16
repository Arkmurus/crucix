"""R-F1744 — capability test for the region-targeted gate-#2 closer.

Drives the REAL broken path: student.reading_session(). Before R-F1744 no
code path read region-specific content for a weak topic×REGION gate-#2 cell
and lifted that cell via update_regional_mastery — reading_session's
feed-selection is region-blind and research_engine routes its region-targeted
hits to the spider (coverage), not to regional mastery.

This test asserts the user-visible gate-#2 outcome: a weak cell
(competitor_intel:southern_africa) gets its regional mastery lifted when
reading_session fetches region-grounded content for it.
"""
from __future__ import annotations

import types

import pytest

from aria_service.intel import student


@pytest.mark.asyncio
async def test_reading_session_lifts_weak_regional_cell(monkeypatch):
    # ── Arrange ────────────────────────────────────────────────────────────
    # Weak gate-#2 cell below the floor target.
    weak_cell = {"topic": "competitor_intel", "region": "southern_africa", "score": 0.515}

    async def _fake_heatmap():
        return {"floor_breach_cells": [weak_cell], "weak_cells": [weak_cell],
                "gate_2_floor_target": 0.70}

    monkeypatch.setattr(student, "get_regional_heatmap", _fake_heatmap)

    # No RSS articles + no proactive queue → the main reading loop is a no-op,
    # so the ONLY thing that can lift regional mastery is the R-F1744 branch.
    from aria_service.intel import researcher as _res
    async def _no_rss(*a, **k):
        return []
    monkeypatch.setattr(_res, "_fetch_rss", _no_rss)

    from aria_service.intel import proactive as _prc
    async def _empty_queue(*a, **k):
        return []
    monkeypatch.setattr(_prc, "get_reading_queue", _empty_queue)
    async def _noop_drain(*a, **k):
        return None
    monkeypatch.setattr(_prc, "drain_reading_queue", _noop_drain)

    # Region-grounded explore result: text that detect_regions() maps to
    # southern_africa (so the lift is read-grounded, not blind).
    fact = types.SimpleNamespace(
        value="South Africa Denel signs SADC defence procurement deal",
        context="Denel and Paramount competitors in the South Africa / southern Africa "
                "defence market won a 2026 procurement tender.",
        source_url="https://example.test/southern-africa-defence",
    )
    explore_result = types.SimpleNamespace(facts=[fact])

    from aria_service.intel import web_explorer as _we
    async def _fake_explore(*a, **k):
        return explore_result
    monkeypatch.setattr(_we, "explore", _fake_explore)

    # Stub fact storage (avoid real backend writes).
    stored = []
    async def _fake_store_fact(*a, **k):
        stored.append(k or a)
        return {"action": "created"}
    monkeypatch.setattr(student.kb, "store_fact", _fake_store_fact)

    # Spy on update_regional_mastery — the gate-#2 mover.
    calls = []
    real_update = student.update_regional_mastery
    async def _spy_update(topics, regions, correct, weight=1.0):
        calls.append((list(topics), list(regions), correct, weight))
        return await real_update(topics, regions, correct, weight)
    monkeypatch.setattr(student, "update_regional_mastery", _spy_update)

    # R-F2660: the R-F1744 lift now gates on an HONEST recall grade (was hardcoded
    # correct=True). This test verifies the reading_session→lift MECHANIC end-to-end,
    # so stub the grader to PASS; the grade gating itself is covered by test_rf2660.
    async def _grade_pass(*a, **k):
        return True
    monkeypatch.setattr("aria_service.autonomous.tasks._grade_researched_cell", _grade_pass)

    # ── Act ────────────────────────────────────────────────────────────────
    result = await student.reading_session(num_articles=2)

    # ── Assert ─────────────────────────────────────────────────────────────
    # 1. The R-F1744 branch lifted the EXACT weak cell.
    assert ("competitor_intel", "southern_africa") in [
        (t[0], r[0]) for (t, r, c, w) in calls if t and r
    ], f"update_regional_mastery not called for the weak cell; calls={calls}"

    # 2. It was a positive (correct=True) reinforcement.
    cell_calls = [c for (t, r, c, w) in calls
                  if t == ["competitor_intel"] and r == ["southern_africa"]]
    assert cell_calls and all(cell_calls), "cell lift must be correct=True"

    # 3. The user-visible result surfaces the lifted cell.
    rs = result.get("regional_studied") or []
    assert any(x["topic"] == "competitor_intel" and x["region"] == "southern_africa"
               for x in rs), f"regional_studied missing the cell: {rs}"

    # 4. Region-grounded content was actually stored for the cell.
    assert stored, "expected at least one grounded fact stored for the cell"


@pytest.mark.asyncio
async def test_no_lift_when_content_not_region_grounded(monkeypatch):
    """Honesty guard: if fetched content does NOT mention the region,
    detect_regions() won't confirm it and the cell must NOT be lifted."""
    weak_cell = {"topic": "competitor_intel", "region": "southern_africa", "score": 0.515}

    async def _fake_heatmap():
        return {"floor_breach_cells": [weak_cell], "weak_cells": [weak_cell]}
    monkeypatch.setattr(student, "get_regional_heatmap", _fake_heatmap)

    from aria_service.intel import researcher as _res
    monkeypatch.setattr(_res, "_fetch_rss", lambda *a, **k: _aempty())
    from aria_service.intel import proactive as _prc
    monkeypatch.setattr(_prc, "get_reading_queue", lambda *a, **k: _aempty())
    monkeypatch.setattr(_prc, "drain_reading_queue", lambda *a, **k: _anone())

    # Content with NO southern-africa signal.
    fact = types.SimpleNamespace(
        value="Generic defence market update",
        context="A defence procurement newsletter with no specific country mentioned.",
        source_url="https://example.test/generic",
    )
    from aria_service.intel import web_explorer as _we
    async def _fake_explore(*a, **k):
        return types.SimpleNamespace(facts=[fact])
    monkeypatch.setattr(_we, "explore", _fake_explore)
    async def _fake_store_fact(*a, **k):
        return {"action": "created"}
    monkeypatch.setattr(student.kb, "store_fact", _fake_store_fact)

    calls = []
    async def _spy_update(topics, regions, correct, weight=1.0):
        calls.append((list(topics), list(regions)))
    monkeypatch.setattr(student, "update_regional_mastery", _spy_update)

    result = await student.reading_session(num_articles=2)

    assert (["competitor_intel"], ["southern_africa"]) not in calls, (
        "must NOT lift a cell when content is not region-grounded"
    )
    assert not (result.get("regional_studied") or [])


async def _aempty():
    return []


async def _anone():
    return None
