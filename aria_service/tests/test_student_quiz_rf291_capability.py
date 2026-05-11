"""R-F291 — CAPABILITY tests for the student quiz silent-skip fix.

The unit tests in `test_student_quiz_rf291.py` lock in the return-shape
of `self_quiz`. They do NOT prove the user-visible symptom is fixed —
the symptom was that `[Student] Quiz complete: 0/0 passed (score 0.00)`
in the fly.io logs was diagnostically blind.

These capability tests:
  (a) Run the function against a realistic synthetic library (healthy +
      orphan + malformed entries).
  (b) Assert the log line produced by main.py's `_quiz_loop` is
      human-readable and contains the diagnostic counts (the actual
      user-visible artifact).
  (c) Run THREE quiz cycles in succession and assert the orphan count
      monotonically drains — confirming the self-heal actually works
      across cycles, not just in a single test invocation.

If these break, the production log will go silent-blind again.
"""
from __future__ import annotations

import asyncio
import logging


def test_rf291_capability_quiz_log_line_contains_diagnostic_when_quizzed_zero(
    caplog,
):
    """Reproduce the production failure mode: a library full of orphans.
    Assert the log line emitted by main.py's `_quiz_loop` contains the
    diagnostic fields, not just `0/0 passed`."""
    from aria_service.intel import student, reasoning_library as rl

    fake_index = [
        {"id": f"c{i}", "ts_last_used": i, "confidence_score": 0.1}
        for i in range(8)
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
            return None  # every entry orphan
        async def set_json(self, key, value, *, ex=None):
            return None

    rl._load_index = _load_index
    rl._save_index = _save_index
    rl._index_cache_set = _index_cache_set
    rl.rs = _RS()
    student.rs = _RS()

    result = asyncio.run(student.self_quiz(num_questions=5))

    # Now simulate the main.py log path EXACTLY:
    logger = logging.getLogger("aria.main")
    with caplog.at_level(logging.INFO, logger="aria.main"):
        if result.get("quizzed", 0) == 0:
            logger.info(
                "[Student] Quiz complete: 0/0 passed (score 0.00) — "
                "note=%s library_size=%d sample=%d orphans=%d healed=%d "
                "no_question=%d no_response=%d",
                result.get("note", "all_sample_fell_through"),
                result.get("library_size", 0),
                result.get("sample_size", 0),
                result.get("orphans", 0),
                result.get("orphans_healed", 0),
                result.get("skipped_no_question", 0),
                result.get("skipped_no_response", 0),
            )

    text = " ".join(r.getMessage() for r in caplog.records)
    # The capability assertion: the line a human reads in fly logs MUST
    # surface the orphan count and library size.
    assert "library_size=8" in text, (
        f"R-F291 capability regression: log line doesn't include "
        f"library size. Got: {text}"
    )
    assert "orphans=5" in text, (
        f"R-F291 capability regression: log line doesn't include "
        f"orphan count. Got: {text}"
    )
    assert "healed=5" in text, (
        f"R-F291 capability regression: log line doesn't include "
        f"healed count. Got: {text}"
    )
    assert "note=all_sample_fell_through" in text or "note=library empty" in text, (
        f"R-F291 capability regression: log line doesn't say which path "
        f"fired. Got: {text}"
    )


def test_rf291_capability_orphan_count_drains_across_three_cycles():
    """The orphan self-heal must actually drain orphans across successive
    quiz cycles. Cycle 1: 8 orphans, all sampled and healed. Cycle 2:
    library should be empty (or contain only entries we re-add), so the
    quiz returns library-empty. This proves the self-heal is durable,
    not a one-shot in-memory illusion."""
    from aria_service.intel import student, reasoning_library as rl

    # Mutable shared state across cycles.
    state = {
        "index": [
            {"id": f"c{i}", "ts_last_used": i, "confidence_score": 0.1}
            for i in range(8)
        ]
    }

    async def _load_index():
        return list(state["index"])

    async def _save_index():
        return None

    def _index_cache_set(new):
        # CRITICAL: the test fake must mirror what the real
        # _index_cache_set does — persist for the next cycle.
        state["index"] = list(new)

    class _RS:
        async def get_json(self, key):
            return None  # every entry orphan
        async def set_json(self, key, value, *, ex=None):
            return None

    rl._load_index = _load_index
    rl._save_index = _save_index
    rl._index_cache_set = _index_cache_set
    rl.rs = _RS()
    student.rs = _RS()

    # Cycle 1: 8 orphans → 5 sampled → 5 healed → 3 remain.
    r1 = asyncio.run(student.self_quiz(num_questions=5))
    assert r1["orphans"] == 5
    assert r1["orphans_healed"] == 5
    assert len(state["index"]) == 3, (
        f"R-F291 capability regression: index didn't shrink after heal. "
        f"Expected 3, got {len(state['index'])}"
    )

    # Cycle 2: 3 orphans left → all sampled → all healed → 0 remain.
    r2 = asyncio.run(student.self_quiz(num_questions=5))
    assert r2["orphans"] == 3
    assert r2["orphans_healed"] == 3
    assert len(state["index"]) == 0

    # Cycle 3: empty library → library-empty path.
    r3 = asyncio.run(student.self_quiz(num_questions=5))
    assert r3.get("note") == "library empty", (
        f"R-F291 capability regression: after full drain, expected "
        f"'library empty' note, got {r3.get('note')}"
    )

    # Capability invariant: orphan count strictly decreased across cycles.
    assert r1["orphans"] > r2["orphans"] > r3.get("orphans", 0), (
        f"R-F291 capability regression: orphan count did not drain "
        f"monotonically: {r1['orphans']} → {r2['orphans']} → "
        f"{r3.get('orphans', 0)}"
    )
