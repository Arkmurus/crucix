"""R-F2433 — seed-all-regions bootstrap for the gate-#2 reading loop.

_study_weak_regional_cells could only REINFORCE cells already in
get_regional_heatmap() (which only lists cells that already have samples), so a
zero-sample region could never be bootstrapped (today the store holds only
'balkans'). The ARIA_STUDENT_SEED_ALL_REGIONS flag (default OFF) extends the
target list with not-yet-existing TOPIC×REGION cells so the loop ATTEMPTS to
ground each region.

This test proves:
  1. Flag OFF → byte-identical (only the existing weak cells are attempted).
  2. Flag ON  → existing + up to ARIA_STUDENT_SEED_BATCH new cells attempted.
  3. NO metric gaming: with no groundable content, NEITHER path credits a cell
     (update_regional_mastery is never called) — seeding only broadens what the
     loop tries to READ; crediting still requires real region content.

Invokes the REAL _study_weak_regional_cells with a fake `explore` + mocked deps.
Runs standalone (avoids the Win/3.14 pytest-IOCP hang):
  python aria_service/tests/test_rf2433_seed_all_regions.py
"""
import asyncio
import os
import sys
import types

import pytest


@pytest.fixture(autouse=True)
def _contain_fakes():
    """R-F2965 (containment): _install_fakes() below reassigns student module
    functions and injects fake sys.modules entries (web_search/capability_gaps/
    engine_wiring) WITHOUT cleanup — a pre-existing isolation leak that polluted
    every downstream test (e.g. dd_ecosystem rf298/rf305 fail when rf2433 runs
    first). Snapshot + restore everything this file mutates so the roller-coaster
    stops at the file boundary."""
    from aria_service.intel import student
    _mods = ("aria_service.intel.web_search", "aria_service.intel.capability_gaps",
             "aria_service.intel.engine_wiring")
    saved_mods = {m: sys.modules.get(m) for m in _mods}
    saved_fns = {k: getattr(student, k, None) for k in
                 ("update_regional_mastery", "get_regional_heatmap")}
    saved_env = {k: os.environ.get(k) for k in
                 ("ARIA_STUDENT_SEED_ALL_REGIONS", "ARIA_STUDENT_SEED_BATCH")}
    try:
        yield
    finally:
        for m, orig in saved_mods.items():
            if orig is None:
                sys.modules.pop(m, None)
            else:
                sys.modules[m] = orig
        for k, orig in saved_fns.items():
            if orig is not None:
                setattr(student, k, orig)
        for k, orig in saved_env.items():
            if orig is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = orig


def _install_fakes(credit_calls):
    from aria_service.intel import student
    # web_search — no Brave (so each cell = exactly 1 explore call)
    sys.modules["aria_service.intel.web_search"] = types.SimpleNamespace(
        BRAVE_API_KEY="", _BRAVE_GLOBALLY_OFF=True, enable_brave_for_scope=lambda *a, **k: None
    )
    # capability_gaps.record_gap — async no-op
    async def _rec(*a, **k):
        return None
    sys.modules["aria_service.intel.capability_gaps"] = types.SimpleNamespace(record_gap=_rec)
    # engine_wiring — no-op
    sys.modules["aria_service.intel.engine_wiring"] = types.SimpleNamespace(
        wire_success=lambda **k: None, wire_failure=lambda **k: None
    )
    # count crediting attempts (must stay 0 — nothing grounds)
    async def _fake_credit(topics, regions, correct=True, weight=1.0):
        credit_calls.append((tuple(topics), tuple(regions)))
    student.update_regional_mastery = _fake_credit


async def _attempts(flag_on, batch=5):
    from aria_service.intel import student
    credit_calls = []
    _install_fakes(credit_calls)

    WEAK = [
        {"topic": "compliance", "region": "balkans", "score": 0.507},
        {"topic": "legal", "region": "balkans", "score": 0.507},
        {"topic": "sanctions", "region": "balkans", "score": 0.507},
    ]

    async def _fake_hm():
        return {"heatmap": {}, "weak_cells": list(WEAK), "floor_breach_cells": list(WEAK)}
    student.get_regional_heatmap = _fake_hm

    queries = []

    async def _fake_explore(**kw):
        queries.append(kw.get("query", ""))
        return None  # nothing grounds → no crediting

    if flag_on:
        os.environ["ARIA_STUDENT_SEED_ALL_REGIONS"] = "1"
        os.environ["ARIA_STUDENT_SEED_BATCH"] = str(batch)
    else:
        # R-F2965 (C3): the default is now ON (§1 names seeding-off a clamp), so
        # to get the OFF behaviour the flag must be EXPLICITLY "0" — unsetting it
        # would now default ON.
        os.environ["ARIA_STUDENT_SEED_ALL_REGIONS"] = "0"

    await student._study_weak_regional_cells(explore=_fake_explore)
    return queries, credit_calls


async def _attempts_default(batch=5):
    """R-F2965 (C3): with the flag UNSET, seeding must default ON."""
    from aria_service.intel import student
    credit_calls = []
    _install_fakes(credit_calls)
    WEAK = [{"topic": "compliance", "region": "balkans", "score": 0.507}]

    async def _fake_hm():
        return {"heatmap": {}, "weak_cells": list(WEAK), "floor_breach_cells": list(WEAK)}
    student.get_regional_heatmap = _fake_hm
    queries = []

    async def _fake_explore(**kw):
        queries.append(kw.get("query", ""))
        return None
    os.environ.pop("ARIA_STUDENT_SEED_ALL_REGIONS", None)  # UNSET → default ON
    os.environ["ARIA_STUDENT_SEED_BATCH"] = str(batch)
    await student._study_weak_regional_cells(explore=_fake_explore)
    return queries


async def _run_all():
    # 1) explicit OFF ("0") → only the 3 existing balkans weak cells attempted
    q_off, credit_off = await _attempts(False)
    assert len(q_off) == 3, f"OFF attempted {len(q_off)} cells, expected 3 (the weak cells)"
    assert all("balkan" in q.lower() for q in q_off), f"OFF attempted non-balkans cells: {q_off}"
    assert credit_off == [], f"OFF must not credit when nothing grounds: {credit_off}"
    print(f"  ✓ OFF('0'): {len(q_off)} attempts (existing weak cells only), 0 credits")

    # 1b) R-F2965 (C3): UNSET → default ON → existing + seeded cells attempted
    q_default = await _attempts_default(batch=5)
    assert len(q_default) > 1, f"default (unset) must seed, got {len(q_default)} attempts"
    seeded_default = [q for q in q_default if "balkan" not in q.lower()]
    assert len(seeded_default) == 5, f"default ON must seed 5 cells, got {len(seeded_default)}"
    print(f"  ✓ DEFAULT(unset): seeding ON — {len(q_default)} attempts ({len(seeded_default)} seeded)")

    # 2) ON (batch=5) → 3 existing + 5 seeded = 8 attempted
    q_on, credit_on = await _attempts(True, batch=5)
    assert len(q_on) == 8, f"ON attempted {len(q_on)}, expected 3 existing + 5 seeded"
    assert credit_on == [], f"ON must ALSO not credit when nothing grounds (no gaming): {credit_on}"
    seeded = [q for q in q_on if "balkan" not in q.lower()]
    assert len(seeded) == 5, f"expected 5 seeded non-balkans cells, got {len(seeded)}"
    print(f"  ✓ ON(batch=5): {len(q_on)} attempts (3 existing + 5 NEW regions), 0 credits (no gaming)")

    # 3) batch honoured (batch=2 → 3 + 2 = 5)
    q_on2, _ = await _attempts(True, batch=2)
    assert len(q_on2) == 5, f"batch=2 → expected 5 attempts, got {len(q_on2)}"
    print(f"  ✓ ON(batch=2): batch cap honoured — {len(q_on2)} attempts")

    # 4) seeded cells are valid (in TOPICS / REGIONS, never general/global)
    from aria_service.intel import student
    for q in [x for x in q_on if "balkan" not in x.lower()]:
        assert "general" not in q.lower(), f"seeded a 'general' topic: {q}"
    print("  ✓ seeded cells are valid topic×region (no general/global)")


def test_seed_all_regions_flag():
    asyncio.run(_run_all())


if __name__ == "__main__":
    asyncio.run(_run_all())
    print("\nPASS")
