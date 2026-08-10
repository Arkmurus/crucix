"""Regression guard for F24 — store_fact skip_rag_ingest flag.

Live observation 2026-04-27 19:31:21-19:31:49: ~50 sentence-transformer
"Batches: 1/1" lines per research cycle. Audit revealed F23 (commit
6b7d2e6) added rag_store.add_facts_batch in _process_analysis but
store_fact's tail still ran rag_store.ingest_fact PER-FACT. Net effect:
F23 doubled the chromadb work instead of replacing it.

Fix: store_fact accepts skip_rag_ingest=True. _process_analysis passes
the flag so store_fact runs dedup/contradiction detection only, then
add_facts_batch does ONE encode for the whole article's facts.

Acceptance:
- store_fact signature has the new keyword
- store_fact default behaviour is unchanged (skip=False, rag still runs)
- _process_analysis passes skip_rag_ingest=True
"""
from __future__ import annotations

import asyncio
import inspect
from unittest.mock import AsyncMock, patch

# R-F3777/§16 — NOT inspect.getsource: it slices at line numbers captured AT
# IMPORT, so a mid-run edit silently returns a DIFFERENT function's body. A CLASS
# target scopes the lookup to that class's own body (R-F3771).
from ._source_probe import function_source


import pytest


@pytest.fixture(autouse=True)
def _isolate_knowledge_dedup_indices():
    """R-F3841 — reset the MODULE-GLOBAL dedup indices around every test here.

    THE FLAKE IS REAL: `test_store_fact_default_runs_rag_ingest` passes alone and
    flips in full runs, observed independently on win-arm64 (local §16 run) and on
    linux (the CI gate), so it is order-dependent rather than a platform artefact.

    WHAT THIS FIXTURE IS, STATED HONESTLY: isolation of state the test provably
    depends on and provably cannot control — NOT a confirmed fix for a reproduced
    mechanism. `knowledge._topic_index` / `_content_index` / `_index_count` are
    MODULE globals (knowledge.py:98, ~1119-1160). The test patches `_load`, which
    controls the STORE but not those indices; if `store_fact` takes its topic-dedup
    branch it returns `{"action": "updated"}` BEFORE the `rag_store.ingest_fact` call
    at knowledge.py:1592 — and `"updated"` is in the set the first assertion accepts,
    so the test would sail past it and fail on `len(ingest_calls) == 1` with zero
    calls. That matches the observed failure exactly.

    IT WAS NOT REPRODUCED. Seeding `_topic_index` before the test does NOT break it:
    `_ensure_indices` rebuilds whenever `_index_count != len(db["facts"])`, which
    wipes the seed. Surviving that needs `id(db)` reuse AND matching counts, and the
    counts are maintained in lock-step, so the combination looks unreachable by that
    route. The trigger is therefore still UNKNOWN and this fixture may not be the
    cure — it removes one class of coupling and is cheap, but do not read it as
    proof. If the test flips again, this docstring is the record of what was already
    ruled out.
    """

    from aria_service.intel import knowledge as _k

    saved = (dict(_k._topic_index), dict(_k._content_index),
             _k._index_count, _k._indexed_cache_id)
    _k._topic_index, _k._content_index = {}, {}
    _k._index_count, _k._indexed_cache_id = 0, None
    try:
        yield
    finally:
        (_k._topic_index, _k._content_index,
         _k._index_count, _k._indexed_cache_id) = saved


def test_store_fact_signature_has_skip_rag_ingest():
    from aria_service.intel.knowledge import store_fact
    sig = inspect.signature(store_fact)
    assert "skip_rag_ingest" in sig.parameters, (
        "store_fact must accept skip_rag_ingest=True kwarg so callers "
        "doing batch RAG upserts (researcher._process_analysis) avoid "
        "double-encoding"
    )
    # Default must be False so existing callers' behaviour doesn't change
    assert sig.parameters["skip_rag_ingest"].default is False


def test_store_fact_default_runs_rag_ingest():
    """Existing call sites must keep doing the rag_store cascade."""
    from aria_service.intel import knowledge

    # Stub the rag_store.ingest_fact and a few other dependencies so
    # store_fact runs end-to-end without external state.
    ingest_calls: list = []

    async def fake_ingest(*args, **kwargs):
        ingest_calls.append((args, kwargs))
        return True

    async def fake_load():
        return {"facts": []}

    async def fake_save(*a, **k): return None

    with patch("aria_service.intel.rag_store.ingest_fact", side_effect=fake_ingest), \
         patch("aria_service.intel.knowledge._load", side_effect=fake_load), \
         patch("aria_service.intel.knowledge._save", side_effect=fake_save):
        result = asyncio.run(knowledge.store_fact(
            topic="test_topic",
            content=(
                # R-F2801: R-F1526 requires >=50 chars for non-brain_hook sources
                # (short content is treated as a FAILED extraction and rejected).
                # This fixture predates that and was 39 chars, so store_fact
                # returned rejected_no_content and never reached the rag path
                # the test exists to measure. The guard is correct; the fixture
                # was stale.
                "some content that is comfortably longer than the fifty "
                "character minimum enforced by R-F1526"
            ),
            source="test",
        ))

    assert result.get("action") in ("created", "updated", "superseded")
    # Default — RAG ingest must have fired
    assert len(ingest_calls) == 1, (
        f"store_fact default should call rag_store.ingest_fact once; "
        f"got {len(ingest_calls)} calls"
    )


def test_store_fact_skip_flag_skips_rag_ingest():
    """skip_rag_ingest=True must skip the rag_store cascade entirely."""
    from aria_service.intel import knowledge

    ingest_calls: list = []

    async def fake_ingest(*args, **kwargs):
        ingest_calls.append((args, kwargs))
        return True

    async def fake_load():
        return {"facts": []}

    async def fake_save(*a, **k): return None

    with patch("aria_service.intel.rag_store.ingest_fact", side_effect=fake_ingest), \
         patch("aria_service.intel.knowledge._load", side_effect=fake_load), \
         patch("aria_service.intel.knowledge._save", side_effect=fake_save):
        result = asyncio.run(knowledge.store_fact(
            topic="test_topic",
            content=(
                # R-F2801: R-F1526 requires >=50 chars for non-brain_hook sources
                # (short content is treated as a FAILED extraction and rejected).
                # This fixture predates that and was 39 chars, so store_fact
                # returned rejected_no_content and never reached the rag path
                # the test exists to measure. The guard is correct; the fixture
                # was stale.
                "some content that is comfortably longer than the fifty "
                "character minimum enforced by R-F1526"
            ),
            source="test",
            skip_rag_ingest=True,
        ))

    assert result.get("action") in ("created", "updated", "superseded")
    # Flag set — no RAG ingest call
    assert len(ingest_calls) == 0, (
        f"store_fact(skip_rag_ingest=True) must not call rag_store; "
        f"got {len(ingest_calls)} calls"
    )


def test_process_analysis_uses_skip_flag():
    """The researcher's _process_analysis loop must pass the flag so
    its add_facts_batch tail isn't doing duplicate work."""
    from aria_service.intel import researcher
    src = function_source(researcher, "_process_analysis")
    assert "skip_rag_ingest=True" in src, (
        "_process_analysis must pass skip_rag_ingest=True to store_fact "
        "since it does the RAG upsert via add_facts_batch at the end"
    )
