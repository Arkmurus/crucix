"""R-F2392 — capability test for Brave-escalated region sourcing (Phase A gate #2).

Drives the REAL crediting path: student._study_weak_regional_cells(), the function
reading_session() calls to lift the weakest topic×region heatmap cells.

Root cause it fixes: the R-F1947 stall — a floor cell stays UNCREDITED forever when
the FREE search stack cannot find region-specific content, so detect_regions() never
confirms the region and update_regional_mastery() never fires. R-F2392 escalates the
region-targeted query to the live Brave-primary search (R-F2318, masked aria_search)
for cells the free stack missed, bounded by a per-session budget.

These tests assert the INTEGRITY bounds hold:
  1. A cell the free stack missed gets credited AFTER Brave sources region content
     (and only because detect_regions() confirms it on the real Brave content).
  2. The region-detection gate still REJECTS non-region content — Brave content that
     does NOT mention the region does NOT credit the cell (records a data-starved gap).
  3. Escalation stays OFF when no Brave key is configured (quota safety).
"""
from __future__ import annotations

import types

import pytest

from aria_service.intel import student
from aria_service.intel import web_search as _ws


@pytest.fixture(autouse=True)
def _no_seed(monkeypatch):
    # R-F2965 (C3): seeding now DEFAULTS ON. These tests assert the isolated
    # single-cell Brave-escalation contract (exact free→brave pass sequence), so
    # disable seeding here to avoid seeded-cell noise. Seeding is exercised by
    # test_rf2433 and test_rf2965.
    monkeypatch.setenv("ARIA_STUDENT_SEED_ALL_REGIONS", "0")


# ── region-tagged vs non-region content fixtures ──────────────────────────────
def _region_fact():
    # detect_regions() maps "saudi/uae/gcc" → "gulf".
    return types.SimpleNamespace(
        value="Saudi Arabia and UAE GCC defence procurement tender 2026",
        context="Saudi Arabia, UAE and the wider GCC / Gulf market awarded a major "
                "defence procurement contract via EDGE Group under Vision 2030.",
        source_url="https://example.test/gulf-defence",
    )


def _generic_fact():
    return types.SimpleNamespace(
        value="Generic defence market update",
        context="A defence procurement newsletter with no specific country mentioned.",
        source_url="https://example.test/generic",
    )


async def _noop_async(*a, **k):
    return None


def _wire_brave_env(monkeypatch, *, key: str | None = "test-key"):
    """Make _brave_available True (key present, not globally off) + spy the scope toggle."""
    monkeypatch.setattr(_ws, "BRAVE_API_KEY", key or "", raising=False)
    monkeypatch.setattr(_ws, "_BRAVE_GLOBALLY_OFF", False, raising=False)
    toggles: list[bool] = []
    monkeypatch.setattr(_ws, "enable_brave_for_scope", lambda on=True: toggles.append(bool(on)))
    return toggles


def _spy_update(calls):
    async def _upd(topics, regions, correct, weight=1.0):
        calls.append((list(topics), list(regions), correct, weight))
    return _upd


@pytest.mark.asyncio
async def test_brave_escalation_credits_cell_free_stack_missed(monkeypatch):
    """A floor cell the FREE stack cannot ground gets credited once Brave sources
    genuinely region-specific content (via_brave=True) — the credit still flows only
    through the unchanged detect_regions() gate."""
    weak_cell = {"topic": "technical", "region": "gulf", "score": 0.459}

    async def _fake_heatmap():
        return {"floor_breach_cells": [weak_cell], "weak_cells": [weak_cell],
                "gate_2_floor_target": 0.70}
    monkeypatch.setattr(student, "get_regional_heatmap", _fake_heatmap)

    toggles = _wire_brave_env(monkeypatch)

    # Stateful explore: FREE pass (language_fanout="auto") returns non-region content;
    # BRAVE pass (language_fanout="off") returns region-tagged content.
    seen_passes: list[str] = []

    async def _fake_explore(*a, **k):
        is_brave = k.get("language_fanout") == "off"
        seen_passes.append("brave" if is_brave else "free")
        return types.SimpleNamespace(facts=[_region_fact() if is_brave else _generic_fact()])

    stored: list = []
    async def _fake_store(*a, **k):
        stored.append(k or a)
        return {"action": "created"}
    monkeypatch.setattr(student.kb, "store_fact", _fake_store)

    calls: list = []
    monkeypatch.setattr(student, "update_regional_mastery", _spy_update(calls))

    # R-F2660: crediting now gates on an HONEST recall grade. This test isolates
    # the Brave-escalation MECHANIC (a missed cell gets sourced + reaches the
    # credit path), so stub the grader to PASS; grade gating is covered by
    # test_rf2660.
    async def _grade_pass(*a, **k):
        return True
    monkeypatch.setattr("aria_service.autonomous.tasks._grade_researched_cell", _grade_pass)

    # ── Act: drive the real crediting path with the injectable explore ──────────
    result = await student._study_weak_regional_cells(explore=_fake_explore, max_cells=1)

    # ── Assert ──────────────────────────────────────────────────────────────────
    # Both passes ran: free first (missed), then Brave (grounded).
    assert seen_passes == ["free", "brave"], f"expected free→brave escalation, got {seen_passes}"
    # Brave scope was enabled for the escalation and reset after.
    assert toggles == [True, False], f"Brave scope must toggle on then off; got {toggles}"
    # The EXACT floor cell was credited (correct=True).
    cell_calls = [(t, r, c, w) for (t, r, c, w) in calls
                  if t == ["technical"] and r == ["gulf"]]
    assert cell_calls, f"cell not credited; update calls={calls}"
    assert all(c for (t, r, c, w) in cell_calls), "credit must be correct=True"
    # Region content was stored, and the result attributes it to Brave.
    assert stored, "expected the Brave-sourced region fact to be stored"
    assert any(x["topic"] == "technical" and x["region"] == "gulf" and x["via_brave"]
               for x in result), f"result must flag via_brave=True: {result}"


@pytest.mark.asyncio
async def test_brave_content_without_region_is_rejected(monkeypatch):
    """Honesty guard: even WITH Brave, if the sourced content does not mention the
    region the detect_regions() gate must REJECT it — no credit, and a data-starved
    gap is recorded (§21e)."""
    weak_cell = {"topic": "technical", "region": "gulf", "score": 0.459}

    async def _fake_heatmap():
        return {"floor_breach_cells": [weak_cell], "weak_cells": [weak_cell]}
    monkeypatch.setattr(student, "get_regional_heatmap", _fake_heatmap)

    _wire_brave_env(monkeypatch)

    # BOTH free and Brave passes return non-region content.
    async def _fake_explore(*a, **k):
        return types.SimpleNamespace(facts=[_generic_fact()])
    monkeypatch.setattr(student.kb, "store_fact", _noop_async)

    calls: list = []
    monkeypatch.setattr(student, "update_regional_mastery", _spy_update(calls))

    # Spy the §21e data-starved gap.
    from aria_service.intel import capability_gaps as _cg
    gaps: list = []
    async def _fake_gap(*a, **k):
        gaps.append(k)
        return None
    monkeypatch.setattr(_cg, "record_gap", _fake_gap)

    result = await student._study_weak_regional_cells(explore=_fake_explore, max_cells=1)

    # NOT credited — the gate rejected non-region content on BOTH passes.
    assert not any(t == ["technical"] and r == ["gulf"] for (t, r, c, w) in calls), (
        f"must NOT credit a cell when no pass is region-grounded; calls={calls}")
    assert not result, f"no cell should be reported credited; got {result}"
    # A per-cell data-starved gap was queued for the coder/operator (§21e).
    import asyncio as _aio
    await _aio.sleep(0)  # let the create_task'd record_gap run
    assert any("technical:gulf" in (g.get("source") or "") for g in gaps), (
        f"expected a §21e data-starved gap for technical:gulf; got {gaps}")


@pytest.mark.asyncio
async def test_no_brave_key_means_no_escalation(monkeypatch):
    """Quota safety: with no Brave key configured, the escalation must NOT fire —
    the loop runs the free pass ONLY and never touches the Brave scope."""
    weak_cell = {"topic": "technical", "region": "gulf", "score": 0.459}

    async def _fake_heatmap():
        return {"floor_breach_cells": [weak_cell], "weak_cells": [weak_cell]}
    monkeypatch.setattr(student, "get_regional_heatmap", _fake_heatmap)

    # No key → _brave_available False.
    toggles = _wire_brave_env(monkeypatch, key="")

    passes: list[str] = []
    async def _fake_explore(*a, **k):
        passes.append("brave" if k.get("language_fanout") == "off" else "free")
        return types.SimpleNamespace(facts=[_generic_fact()])
    monkeypatch.setattr(student.kb, "store_fact", _noop_async)
    monkeypatch.setattr(student, "update_regional_mastery", _spy_update([]))

    await student._study_weak_regional_cells(explore=_fake_explore, max_cells=1)

    assert passes == ["free"], f"escalation must not fire without a Brave key; got {passes}"
    assert toggles == [], f"Brave scope must never be touched without a key; got {toggles}"
