"""R-F291 — self_quiz diagnostic + orphan self-heal.

Pinned 2026-05-11 after live fly.io log showed `Quiz complete: 0/0 passed
(score 0.00)` with no way to distinguish:
  (a) library_empty path (student.py:589 early return)
  (b) all-sample-fell-through path (every sampled entry hit a `continue`)

The fix:
  1. self_quiz now returns library_size / sample_size / orphans /
     orphans_healed / skipped_no_question / skipped_no_response so
     main.py can log the silent-skip cause.
  2. Orphan index entries (entry present, case blob missing) are
     dropped from the index on the spot — no longer wait up to 24h
     for the next consolidate() sweep.
"""
from __future__ import annotations

import asyncio


def _aio_noop():
    async def _f(*a, **k):
        return None
    return _f()


def test_self_quiz_library_empty_returns_diagnostic(monkeypatch):
    from aria_service.intel import student, reasoning_library as rl

    async def _empty_index():
        return []
    monkeypatch.setattr(rl, "_load_index", _empty_index)

    out = asyncio.run(student.self_quiz(num_questions=5))
    assert out["quizzed"] == 0
    assert out["passed"] == 0
    assert out["note"] == "library empty"
    assert out["library_size"] == 0


def test_self_quiz_orphan_index_entries_self_heal(monkeypatch):
    """Every sampled entry is an orphan (index entry exists, case blob None).
    Result: quizzed=0 BUT orphans=N AND orphans_healed=N AND index gets
    rewritten without the dead ids."""
    from aria_service.intel import student, reasoning_library as rl

    fake_index = [
        {"id": f"c{i}", "ts_last_used": i, "confidence_score": 0.1}
        for i in range(5)
    ]
    saved_indexes: list[list[dict]] = []

    async def _load_index():
        return list(fake_index)

    async def _save_index():
        return None

    def _index_cache_set(new):
        saved_indexes.append(list(new))

    class _RS:
        async def get_json(self, key):
            # All case lookups return None — every entry is an orphan.
            return None

        async def set_json(self, key, value, *, ex=None):
            return None

    monkeypatch.setattr(rl, "_load_index", _load_index)
    monkeypatch.setattr(rl, "_save_index", _save_index)
    monkeypatch.setattr(rl, "_index_cache_set", _index_cache_set)
    monkeypatch.setattr(rl, "rs", _RS())
    monkeypatch.setattr(student, "rs", _RS())

    out = asyncio.run(student.self_quiz(num_questions=5))

    assert out["quizzed"] == 0
    assert out["library_size"] == 5
    assert out["sample_size"] == 5
    assert out["orphans"] == 5, f"expected 5 orphans, got {out['orphans']}"
    assert out["orphans_healed"] == 5, (
        f"orphan self-heal didn't fire: {out}"
    )
    # The index_cache should have been rewritten with zero entries.
    assert saved_indexes, "_index_cache_set was never called — heal missing"
    assert saved_indexes[-1] == [], (
        f"orphan ids not stripped from index: {saved_indexes[-1]}"
    )


def test_self_quiz_partial_orphans_heal_only_dead_ones(monkeypatch):
    """3 entries: 1 valid case + 2 orphans. Result: orphans=2, healed=2,
    surviving entry stays in the index."""
    from aria_service.intel import student, reasoning_library as rl
    from aria_service.intel import reasoning_router

    fake_index = [
        {"id": "good", "ts_last_used": 1, "confidence_score": 0.5},
        {"id": "dead1", "ts_last_used": 2, "confidence_score": 0.5},
        {"id": "dead2", "ts_last_used": 3, "confidence_score": 0.5},
    ]
    saved_indexes: list[list[dict]] = []

    cases = {
        "rl:case:good": {
            "id": "good",
            "question": "What sanctions apply to Iran?",
            "response": "OFAC SDN List + EU 359/2011 — comprehensive embargo.",
        },
    }

    async def _load_index():
        return list(fake_index)

    async def _save_index():
        return None

    def _index_cache_set(new):
        saved_indexes.append(list(new))

    def _case_key(case_id):
        return f"rl:case:{case_id}"

    class _RS:
        async def get_json(self, key):
            return cases.get(key)

        async def set_json(self, key, value, *, ex=None):
            return None

    async def _no_local(question, **kw):
        return {"answered": False}

    async def _no_update(*a, **k):
        return None

    monkeypatch.setattr(rl, "_load_index", _load_index)
    monkeypatch.setattr(rl, "_save_index", _save_index)
    monkeypatch.setattr(rl, "_index_cache_set", _index_cache_set)
    monkeypatch.setattr(rl, "_case_key", _case_key)
    monkeypatch.setattr(rl, "rs", _RS())
    monkeypatch.setattr(student, "rs", _RS())
    monkeypatch.setattr(reasoning_router, "try_local_reasoning", _no_local)
    monkeypatch.setattr(student, "update_mastery", _no_update)

    # Force the sample to cover all 3 by using num_questions large enough.
    out = asyncio.run(student.self_quiz(num_questions=10))

    assert out["library_size"] == 3
    assert out["orphans"] == 2, f"expected 2 orphans, got {out['orphans']}"
    assert out["orphans_healed"] == 2
    # 1 good entry quizzed (local stack returns answered=False → similarity=0
    # → passed=False, but it IS counted in `quizzed`).
    assert out["quizzed"] == 1
    assert out["passed"] == 0
    # Healed index should have only the good entry.
    assert saved_indexes, "self-heal didn't rewrite index"
    surviving_ids = {e["id"] for e in saved_indexes[-1]}
    assert surviving_ids == {"good"}, (
        f"wrong entries survived: {surviving_ids}"
    )
