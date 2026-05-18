"""R-F685 (2026-05-18) — curated seed facts contribute mastery signal.

Parallel-agent DD 2026-05-18 noted that latam_asia_pac_seed.seed_facts()
writes 35 curated facts to the knowledge store but never updates
_regional_cache. Result: the operator's manual seed work (R-F646-era)
moved the knowledge base but not the heatmap floor — gate #2 stayed
stuck.

R-F685 has seed_facts() also call update_regional_mastery() per fact
with weight=0.05 (alpha ≈ 0.005). Each fact becomes a light positive
mastery signal for its (topic, region) cell. The operator vouching
for the data is a correctness signal — the EWMA absorbs it without
overpowering real quiz/chat signal.

The seed reports the count in its return dict (`mastery_updated`) so
the operator can verify the scaffold ran.

A mastery-update failure does NOT count as a seed error — mastery is
a soft signal layer atop the authoritative knowledge store.
"""
from __future__ import annotations

import asyncio


def test_rf685_seed_returns_mastery_updated_count(monkeypatch):
    """The seed_facts() return dict must include `mastery_updated`."""
    from aria_service.intel.knowledge_packs import latam_asia_pac_seed
    from aria_service.intel import student

    # Stub knowledge.store_fact + search so seed runs without DB.
    from aria_service.intel import knowledge as _k

    async def fake_store_fact(**kwargs):
        return {"ok": True, "id": "fake"}

    async def fake_search(*a, **kw):
        return []  # marker not present → run full seed

    monkeypatch.setattr(_k, "store_fact", fake_store_fact)
    monkeypatch.setattr(_k, "search", fake_search, raising=False)

    # Capture every update_regional_mastery call so we can verify the
    # scaffold fires for each fact.
    captured: list[tuple[list[str], list[str], bool, float]] = []

    async def fake_update_mastery(topics, regions, correct, weight=1.0):
        captured.append((list(topics), list(regions), correct, weight))

    monkeypatch.setattr(student, "update_regional_mastery", fake_update_mastery)

    # Also stub brain_hook.absorb to avoid wiring it for this test.
    from aria_service.intel import brain_hook as _bh

    async def fake_absorb(**kw):
        return None

    monkeypatch.setattr(_bh, "absorb", fake_absorb)

    result = asyncio.run(latam_asia_pac_seed.seed_facts(skip_if_seeded=False))

    assert "mastery_updated" in result, result
    # Should equal the count of facts (≈35); the underlying _FACTS list
    # length is the upper bound. Lower bound: at least most facts.
    assert result["mastery_updated"] >= 30, (
        f"R-F685: mastery_updated count too low: {result}"
    )
    # Each capture should be weight=0.05 + correct=True.
    assert captured, "R-F685: no mastery updates captured"
    for topics, regions, correct, weight in captured:
        assert weight == 0.05, f"R-F685: wrong weight: {weight}"
        assert correct is True
        assert len(topics) == 1
        assert len(regions) == 1


def test_rf685_mastery_failure_does_not_break_seed(monkeypatch):
    """A failing update_regional_mastery must NOT count as a seed error.
    Mastery is a soft signal layer — the knowledge-store write is what
    actually matters."""
    from aria_service.intel.knowledge_packs import latam_asia_pac_seed
    from aria_service.intel import student
    from aria_service.intel import knowledge as _k
    from aria_service.intel import brain_hook as _bh

    async def fake_store_fact(**kwargs):
        return {"ok": True}

    async def fake_search(*a, **kw):
        return []

    async def boom_mastery(*a, **kw):
        raise RuntimeError("mastery store unreachable")

    async def fake_absorb(**kw):
        return None

    monkeypatch.setattr(_k, "store_fact", fake_store_fact)
    monkeypatch.setattr(_k, "search", fake_search, raising=False)
    monkeypatch.setattr(student, "update_regional_mastery", boom_mastery)
    monkeypatch.setattr(_bh, "absorb", fake_absorb)

    result = asyncio.run(latam_asia_pac_seed.seed_facts(skip_if_seeded=False))

    # Even though every mastery update raised, the seed itself succeeded.
    assert result["ok"] is True, f"R-F685: mastery failure broke seed: {result}"
    assert result["added"] > 0
    assert result["errors"] == 0
    # mastery_updated stays at 0 because every update raised.
    assert result["mastery_updated"] == 0


def test_rf685_skip_if_seeded_short_circuits_without_mastery(monkeypatch):
    """When the marker fact already exists, the seed returns early and
    must NOT touch mastery (idempotency)."""
    from aria_service.intel.knowledge_packs import latam_asia_pac_seed
    from aria_service.intel import student
    from aria_service.intel import knowledge as _k

    async def fake_store_fact(**kwargs):
        return {"ok": True}

    async def fake_search(*a, **kw):
        # Marker IS present — short-circuit
        return [{"content": "[KNOWLEDGE_PACK_MARKER] LatAm..."}]

    mastery_calls = 0

    async def spy_mastery(*a, **kw):
        nonlocal mastery_calls
        mastery_calls += 1

    monkeypatch.setattr(_k, "store_fact", fake_store_fact)
    monkeypatch.setattr(_k, "search", fake_search, raising=False)
    monkeypatch.setattr(student, "update_regional_mastery", spy_mastery)

    result = asyncio.run(latam_asia_pac_seed.seed_facts(skip_if_seeded=True))

    assert result.get("action") == "skipped"
    assert mastery_calls == 0, (
        f"R-F685: mastery touched on already-seeded run: {mastery_calls} calls"
    )
