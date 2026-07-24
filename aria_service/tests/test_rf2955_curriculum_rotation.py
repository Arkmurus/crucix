"""R-F2955 (C1) — coverage-rotation curriculum.

The old selection studied floor_breach_cells[:15] (itself [:20]-truncated) every
session, so below-floor cells ranked ~16+ were studied NEVER. _select_curriculum_cells
keeps the weakest head (incl. the argmin, which the gate closes on) and ROTATES the
rest via a durable cursor, so every below-floor cell gets periodic reinforcement.
Grading is unchanged (§1 no clamping) — this only changes WHICH cells are attempted.
"""
from __future__ import annotations

import asyncio
from unittest import mock


def _below(n):
    """n below-floor cells, weakest-first (score 0.05, 0.06, ...)."""
    return [{"topic": "procurement", "region": f"r{i:02d}", "score": round(0.05 + i * 0.01, 3)} for i in range(n)]


def _key(c):
    return f"{c['topic']}:{c['region']}"


def test_rf2955_argmin_always_studied_and_full_coverage_over_sessions():
    """Across enough sessions every below-floor cell is targeted >=1 time, AND the
    single weakest (argmin) is targeted EVERY session (the gate-closing cell)."""
    from aria_service.intel import student

    cells = _below(40)           # 40 below-floor cells, window 15, head 5
    argmin_key = _key(cells[0])  # r00 (0.05) — the weakest
    cursor = {"v": None}

    async def fake_get(key):
        return cursor["v"]

    async def fake_set(key, value, ex=None, **kw):
        cursor["v"] = value

    async def run():
        seen = set()
        with mock.patch.object(student.rs, "get", side_effect=fake_get), \
             mock.patch.object(student.rs, "set", side_effect=fake_set):
            # tail size = 40-5 = 35, window-for-tail = 15-5 = 10 → covered in ceil(35/10)=4 sessions
            for _ in range(6):
                picked = await student._select_curriculum_cells(cells, max_cells=15, head_n=5)
                keys = [_key(c) for c in picked]
                assert argmin_key in keys, "argmin must be studied every session"
                assert len(picked) <= 15
                seen.update(keys)
        return seen

    seen = asyncio.run(run())
    all_keys = {_key(c) for c in cells}
    missing = all_keys - seen
    assert not missing, f"every below-floor cell must be reached by rotation; missed {missing}"


def test_rf2955_small_set_returns_all_no_rotation_needed():
    """When the below-floor set fits in the window, all cells are returned."""
    from aria_service.intel import student

    cells = _below(8)

    async def fake_get(key):
        return None

    async def fake_set(key, value, ex=None, **kw):
        pass

    async def run():
        with mock.patch.object(student.rs, "get", side_effect=fake_get), \
             mock.patch.object(student.rs, "set", side_effect=fake_set):
            return await student._select_curriculum_cells(cells, max_cells=15, head_n=5)

    picked = asyncio.run(run())
    assert len(picked) == 8


def test_rf2955_survives_set_resizing():
    """A cursor beyond the current (shrunk) set must not crash — modulo wraps."""
    from aria_service.intel import student

    async def fake_get(key):
        return "999"  # stale large cursor from a previously larger set

    async def fake_set(key, value, ex=None, **kw):
        pass

    async def run():
        with mock.patch.object(student.rs, "get", side_effect=fake_get), \
             mock.patch.object(student.rs, "set", side_effect=fake_set):
            return await student._select_curriculum_cells(_below(12), max_cells=15, head_n=5)

    picked = asyncio.run(run())
    assert 0 < len(picked) <= 15


def test_rf2955_empty_below_returns_empty():
    from aria_service.intel import student
    assert asyncio.run(student._select_curriculum_cells([], max_cells=15)) == []
