"""R-F895 — semantic_search encode failures are observable to the brain.

Ecosystem-360 P1-5: the sentence-transformer encoder is the centre of every
event-loop wedge, but its three encode call sites (add / add_batch / query)
swallowed failures at logger.debug — and because the encode runs inside an
asyncio.to_thread worker, even a WARNING would be dropped by error_log_handler
(it drops records emitted off the loop thread). So an EncodeLockTimeout (the
wedge precursor) or a model error was invisible to the coder/operator.

R-F895 routes each encode failure through _report_encode_failure, which emits a
*deduped* capability_gaps gap (gap_type='embedder_failure') — scheduling it on
the captured main loop so it works from a worker thread too.
"""
from __future__ import annotations

import asyncio
from unittest.mock import patch

import pytest

np = pytest.importorskip("numpy")


def test_rf895_gap_type_registered():
    from aria_service.intel import capability_gaps
    assert "embedder_failure" in capability_gaps.VALID_GAP_TYPES


def test_rf895_query_encode_failure_records_gap_and_degrades():
    """Capability test: a query-encode failure must (a) degrade gracefully to
    None (TF-IDF fallback) AND (b) record an embedder_failure gap to the brain."""
    from aria_service.intel import semantic_search as ss, capability_gaps

    recorded: list = []

    async def fake_record_gap(**kwargs):
        recorded.append(kwargs)
        return {}

    class BoomModel:
        def encode(self, *a, **k):
            raise ss.EncodeLockTimeout("simulated wedge — lock not acquired")

    async def run():
        idx = ss.SemanticIndex()
        # Populate one embedding so _search_embeddings passes its early guards
        idx._embeddings = {"d1": np.zeros(8, dtype=float)}
        idx._matrix_dirty = True
        with patch.object(ss, "_get_embedder", return_value=BoomModel()), \
             patch.object(capability_gaps, "record_gap", side_effect=fake_record_gap):
            result = idx._search_embeddings("angola defence budget", 5, 0.0)
            await asyncio.sleep(0.02)  # let the scheduled create_task fire
        assert result is None, "encode failure must degrade to None (TF-IDF fallback)"
        assert any(r.get("gap_type") == "embedder_failure" for r in recorded), (
            f"expected an embedder_failure gap, got: {recorded}"
        )
        assert any("query" in (r.get("source") or "") for r in recorded)

    asyncio.run(run())


def test_rf895_no_running_loop_does_not_crash():
    """_report_encode_failure must be safe from a pure-sync context with no
    captured loop — no exception, no record, no unawaited-coroutine crash."""
    from aria_service.intel import semantic_search as ss, capability_gaps

    recorded: list = []

    async def fake_record_gap(**kwargs):
        recorded.append(kwargs)
        return {}

    saved_loop = ss._MAIN_LOOP
    ss._MAIN_LOOP = None
    try:
        with patch.object(capability_gaps, "record_gap", side_effect=fake_record_gap):
            # No running loop here (plain sync test body) → must not raise.
            ss._report_encode_failure("add", ss.EncodeLockTimeout("x"))
    finally:
        ss._MAIN_LOOP = saved_loop
    assert recorded == [], "with no loop the gap can't be scheduled; must not record or crash"


def test_rf895_capture_main_loop_sets_loop():
    from aria_service.intel import semantic_search as ss

    async def run():
        ss._MAIN_LOOP = None
        ss._capture_main_loop()
        assert ss._MAIN_LOOP is asyncio.get_running_loop()

    asyncio.run(run())
